package com.metadata.engine.be.metadata_engine_be.services;

import com.metadata.engine.be.metadata_engine_be.models.DataConnection;
import com.metadata.engine.be.metadata_engine_be.models.DiscoveredTable;
import com.metadata.engine.be.metadata_engine_be.models.DomainGroup;
import com.metadata.engine.be.metadata_engine_be.models.Relationship;
import com.metadata.engine.be.metadata_engine_be.models.SensitiveColumn;
import com.metadata.engine.be.metadata_engine_be.models.dto.SchemaColumn;
import com.metadata.engine.be.metadata_engine_be.models.AnalysisJob;
import com.metadata.engine.be.metadata_engine_be.repositories.AnalysisJobRepository;
import com.metadata.engine.be.metadata_engine_be.repositories.DataConnectionRepository;
import com.metadata.engine.be.metadata_engine_be.repositories.DiscoveredTableRepository;
import com.metadata.engine.be.metadata_engine_be.repositories.DomainGroupRepository;
import com.metadata.engine.be.metadata_engine_be.repositories.RelationshipRepository;
import com.metadata.engine.be.metadata_engine_be.repositories.SensitiveColumnRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Connection;
import java.sql.DriverManager;
import java.time.Instant;
import java.util.List;

import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.springframework.scheduling.annotation.Async;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
@RequiredArgsConstructor
@Slf4j
public class AnalysisService {

    private final AnalysisJobRepository analysisJobRepository;
    private final DataConnectionRepository dataConnectionRepository;
    private final DiscoveredTableRepository discoveredTableRepository;
    private final RelationshipRepository relationshipRepository;
    private final DomainGroupRepository domainGroupRepository;
    private final SensitiveColumnRepository sensitiveColumnRepository;

    private final SseBroadcasterService sse;
    private final SchemaExtractionService schemaExtraction;
    private final PythonMlIntegrationService mlIntegration;

    @Transactional
    public void deleteJob(Long jobId) {
        discoveredTableRepository.deleteByJobId(jobId);
        relationshipRepository.deleteByJobId(jobId);
        domainGroupRepository.deleteByJobId(jobId);
        sensitiveColumnRepository.deleteByJobId(jobId);
        analysisJobRepository.deleteById(jobId);
        sse.clearHistory(jobId);
    }

    @Transactional
    public AnalysisJob initiateJob(String targetSchema) {
        List<DataConnection> conns = dataConnectionRepository.findAll();
        DataConnection connection = conns.stream()
                .filter(c -> c.getSchemaName().equals(targetSchema))
                .findFirst().orElse(null);

        AnalysisJob job = new AnalysisJob();
        job.setTargetSchema(targetSchema);
        job.setStatus("RUNNING");
        job.setStartTime(Instant.now());
        if (connection != null) {
            job.setProfile(connection);
        }
        job = analysisJobRepository.save(job);
        
        // Spring @Async ignores internal method calls. Spin physical thread to free HTTP!
        final java.lang.Long finalJobId = job.getId();
        final com.metadata.engine.be.metadata_engine_be.models.DataConnection finalConn = connection;
        java.util.concurrent.CompletableFuture.runAsync(() -> {
             executeJobRealtime(finalJobId, finalConn);
        });

        return job;
    }

    public SseEmitter subscribe(Long id) {
        return sse.subscribe(id);
    }

    public List<AnalysisJob> getAllJobs() {
        return analysisJobRepository.findAll();
    }

    @Async
    @Transactional
    public void executeJobRealtime(Long jobId, DataConnection connection) {
        try {
            Thread.sleep(5000); // Robust Latch padding for Angular SSE Subscriber HTTP handshake

            if (connection == null) {
                sse.sendEvent(jobId, Map.of("stage", "[INIT]", "msg", "No connection profile found!", "cls", "lwarn"));
                return;
            }

            sse.sendEvent(jobId, Map.of("stage", "[INIT]", "msg",
                    "Job #" + jobId + " spinning up against " + connection.getSchemaName() + " via SchemaCrawler",
                    "cls", "lm"));

            // 1. Schema Extraction (JDBC)
            log.info("[Job #{}] Commencing Stage 1: JDBC SchemaCrawler Mapping...", jobId);
            com.metadata.engine.be.metadata_engine_be.models.dto.BulkSchemaRequest schema = schemaExtraction
                    .extractSchema(jobId, connection);
            log.info(" Count :{}", schema.getTables().size());
            if (schema != null && schema.getTables() != null) {
                List<DiscoveredTable> dtList = new java.util.ArrayList<>();
                for (com.metadata.engine.be.metadata_engine_be.models.dto.SchemaTable st : schema.getTables()) {
                    DiscoveredTable dt = new DiscoveredTable();
                    dt.setJobId(jobId);
                    dt.setSchemaName(connection.getSchemaName());
                    dt.setTableName(st.getTableName());
                    dt.setColumns(new java.util.ArrayList<>());
                    for (com.metadata.engine.be.metadata_engine_be.models.dto.SchemaColumn col : st.getColumns()) {
                        com.metadata.engine.be.metadata_engine_be.models.DiscoveredColumn dc = new com.metadata.engine.be.metadata_engine_be.models.DiscoveredColumn();
                        dc.setColumnName(col.getColumnName());
                        dc.setDataType(col.getDataType());
                        dc.setLength(col.getLength() != null ? col.getLength() : 255);
                        dc.setIsPrimaryKey(col.isPrimaryKey());
                        dc.setDiscoveredTable(dt);
                        dt.getColumns().add(dc);
                    }
                    dtList.add(dt);
                }
                discoveredTableRepository.saveAll(dtList);
                
                if (schema.getSchemaCrawlerRelationships() != null) {
                    schema.getSchemaCrawlerRelationships().forEach(r -> {
                        r.setJobId(jobId);
                        r.setSourceTable(discoveredTableRepository.findByTableNameAndJobId(r.getSourceTableName(), jobId));
                        r.setTargetTable(discoveredTableRepository.findByTableNameAndJobId(r.getTargetTableName(), jobId));
                    });
                    saveRelationships(schema.getSchemaCrawlerRelationships());
                }
            }
            log.info("[Job #{}] Stage 1 Complete. Parsed schema successfully.", jobId);

            // 2. Stage 1: Candidate Generation
            log.info("[Job #{}] Commencing Stage 1.5: Machine Learning Target Candidate Matching...", jobId);

            com.metadata.engine.be.metadata_engine_be.models.dto.CandidateResponse pairs = mlIntegration
                    .generateCandidates(jobId, schema);
            
            // Safety check fallback
            if (pairs == null) {
                pairs = new com.metadata.engine.be.metadata_engine_be.models.dto.CandidateResponse();
                pairs.setCandidates(new java.util.ArrayList<>());
            } else if (pairs.getCandidates() == null) {
                pairs.setCandidates(new java.util.ArrayList<>());
            }

            log.info(" Count :{}", pairs.getCandidates().size());
            // 3. Stage 2: Context/Semantic Scoring
            com.metadata.engine.be.metadata_engine_be.models.dto.CandidateResponse scored = null;
            if (!pairs.getCandidates().isEmpty()) {
                log.info("[Job #{}] Commencing Stage 2: Context/Semantic Boundary Extraction...", jobId);
                scored = mlIntegration.evaluateRelationships(jobId, pairs);
            } else {
                log.info("[Job #{}] Bypassing Stage 2: Context/Semantic Boundary Extraction (0 Candidates)...", jobId);
                sse.sendEvent(jobId, Map.of("stage", "[SCORE]", "msg", "Bypassing Context Scoring (0 ML Candidates)", "cls", "lm"));
            }

            // 4. Stage 3: Data Cardinality Mapping
            com.metadata.engine.be.metadata_engine_be.models.dto.CardinalityResponse limits = null;
            if (scored != null && scored.getCandidates() != null && !scored.getCandidates().isEmpty()) {
                log.info("[Job #{}] Commencing Stage 3: Data Cardinality Metric Verification...", jobId);
                limits = mlIntegration.resolveCardinalities(jobId, scored, connection);
                if (limits != null && limits.getRelationships() != null) {
                    limits.getRelationships().forEach(r -> {
                        r.setJobId(jobId);
                        r.setSourceTable(discoveredTableRepository.findByTableNameAndJobId(r.getSourceTableName(), jobId));
                        r.setTargetTable(discoveredTableRepository.findByTableNameAndJobId(r.getTargetTableName(), jobId));
                    });
                    saveRelationships(limits.getRelationships());
                }
            } else {
                log.info("[Job #{}] Bypassing Stage 3: Data Cardinality Metric Verification (0 Relationships)...", jobId);
                sse.sendEvent(jobId, Map.of("stage", "[CARD]", "msg", "Bypassing Cardinality Resolution (0 ML Relationships)", "cls", "lm"));
            }

            // 5. Stage 4: PII / Sensitive Detection (Chunked CPU limits)
            int pageSize = 50;
            log.info("[Job #{}] Commencing Stage 4: SpaCy Deep PII Classification Mapping (Paginated)...", jobId);
            com.metadata.engine.be.metadata_engine_be.models.dto.SensitiveResponse sensitiveData = new com.metadata.engine.be.metadata_engine_be.models.dto.SensitiveResponse();
            sensitiveData.setSensitive_columns(new java.util.ArrayList<>());
            for (int i = 0; i < schema.getTables().size(); i += pageSize) {
                com.metadata.engine.be.metadata_engine_be.models.dto.BulkSchemaRequest chunk = new com.metadata.engine.be.metadata_engine_be.models.dto.BulkSchemaRequest();
                chunk.setSchemaName(schema.getSchemaName());
                chunk.setTables(schema.getTables().subList(i, Math.min(i + pageSize, schema.getTables().size())));

                com.metadata.engine.be.metadata_engine_be.models.dto.SensitiveResponse sData = mlIntegration
                        .detectSensitiveEntities(jobId, chunk);
                if (sData != null && sData.getSensitive_columns() != null) {
                    sensitiveData.getSensitive_columns().addAll(sData.getSensitive_columns());
                }
            }
            if (sensitiveData != null && sensitiveData.getSensitive_columns() != null) {
                sensitiveData.getSensitive_columns().forEach(s -> {
                    s.setJobId(jobId);
                    if (s.getTransientTableName() != null) {
                        s.setTable(discoveredTableRepository.findByTableNameAndJobId(s.getTransientTableName(), jobId));
                    }
                });
                saveSensitiveColumns(sensitiveData.getSensitive_columns());
            }

            // Stage 5: Domain / Graph Tracking Hooks
            log.info("[Job #{}] Commencing Stage 5: Domain Vector Aggregation via Sentence-Transformers...", jobId);
            
            if (limits != null && limits.getRelationships() != null) {
                schema.setMlRelationships(limits.getRelationships());
            }
            
            int stage5PageSize = 50;
            com.metadata.engine.be.metadata_engine_be.models.dto.DomainGroupResponse clusters = new com.metadata.engine.be.metadata_engine_be.models.dto.DomainGroupResponse();
            clusters.setClusters(new java.util.ArrayList<>());
            
            for (int i = 0; i < schema.getTables().size(); i += stage5PageSize) {
                int end = Math.min(i + stage5PageSize, schema.getTables().size());
                com.metadata.engine.be.metadata_engine_be.models.dto.BulkSchemaRequest chunkReq = new com.metadata.engine.be.metadata_engine_be.models.dto.BulkSchemaRequest();
                chunkReq.setTables(schema.getTables().subList(i, end));
                chunkReq.setSchemaCrawlerRelationships(schema.getSchemaCrawlerRelationships());
                chunkReq.setMlRelationships(schema.getMlRelationships());
                
                com.metadata.engine.be.metadata_engine_be.models.dto.DomainGroupResponse chunkRes = mlIntegration
                        .extractDomainGroups(jobId, chunkReq);
                        
                if (chunkRes != null && chunkRes.getClusters() != null) {
                    clusters.getClusters().addAll(chunkRes.getClusters());
                }
            }

            if (clusters != null && clusters.getClusters() != null && !clusters.getClusters().isEmpty()) {
                clusters.getClusters().forEach(c -> {
                    c.setJobId(jobId);
                    if (c.getTableNames() != null) {
                        List<com.metadata.engine.be.metadata_engine_be.models.DiscoveredTable> linkedTables = new java.util.ArrayList<>();
                        for (String tName : c.getTableNames()) {
                            com.metadata.engine.be.metadata_engine_be.models.DiscoveredTable found = discoveredTableRepository
                                    .findByTableNameAndJobId(tName, jobId);
                            if (found != null) {
                                linkedTables.add(found);
                            }
                        }
                        c.setTables(linkedTables);
                    }
                });
                saveDomainGroups(clusters.getClusters());
            }

            // Stage 6: Construct ER Graph mapping
            log.info("[Job #{}] Commencing Stage 6: ERD Graph Context Generation Framework...", jobId);
            List<com.metadata.engine.be.metadata_engine_be.models.Relationship> allRels = new java.util.ArrayList<>();
            if (schema.getSchemaCrawlerRelationships() != null) {
                allRels.addAll(schema.getSchemaCrawlerRelationships());
            }
            if (limits != null && limits.getRelationships() != null) {
                allRels.addAll(limits.getRelationships());
            }
            
            int stage6PageSize = 250;
            for (int i = 0; i < allRels.size(); i += stage6PageSize) {
                int end = Math.min(i + stage6PageSize, allRels.size());
                com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextRequest graphReq = new com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextRequest();
                graphReq.setRelationships(allRels.subList(i, end));
                if (clusters != null && clusters.getClusters() != null) {
                    graphReq.setClusters(clusters.getClusters());
                } else {
                    graphReq.setClusters(new java.util.ArrayList<>());
                }
                
                com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextResponse graph = mlIntegration
                        .generateContextGraph(jobId, graphReq);
            }

            // Stage 7: Entity Classification
            log.info("[Job #{}] Commencing Stage 7: Entity/Table Classification Framework...", jobId);
            
            // Re-aggregate ML relationships and schema Crawler relationships into the schema payload
            schema.setMlRelationships(limits != null ? limits.getRelationships() : null);
            
            java.util.List<Map<String, String>> classifications = mlIntegration.classifyEntities(jobId, schema);
            if (classifications != null && !classifications.isEmpty()) {
                for (Map<String, String> cls : classifications) {
                    String tName = cls.get("tableName");
                    String type = cls.get("tableType");
                    com.metadata.engine.be.metadata_engine_be.models.DiscoveredTable dt = discoveredTableRepository.findByTableNameAndJobId(tName, jobId);
                    if (dt != null) {
                        dt.setTableType(type);
                        discoveredTableRepository.save(dt);
                    }
                }
            }

            log.info("[Job #{}] All Pipeline Stages Finalized Successfully!", jobId);
            sse.sendEvent(jobId, Map.of("stage", "[DONE]", "msg", "All schema analysis stages finished successfully.",
                    "cls", "lok"));
                    
            completeJob(jobId, "Stages extracted and Machine Learning mapping completed flawlessly.");

        } catch (Exception e) {
            log.error("[Job #{}] Analysis Pipeline Trace Exception: ", jobId, e);
            sse.sendEvent(jobId,
                    Map.of("stage", "[ERROR]", "msg", "Analysis Runtime Exception: " + e.getMessage(), "cls", "lwarn"));
            e.printStackTrace();
            
            com.metadata.engine.be.metadata_engine_be.models.AnalysisJob job = analysisJobRepository.findById(jobId).orElse(null);
            if (job != null) {
                job.setStatus("FAILED");
                job.setEndTime(java.time.Instant.now());
                job.setAuditLogs("Fatal Exception: " + e.getMessage());
                analysisJobRepository.save(job);
            }
        }
    }

    @Transactional
    public AnalysisJob completeJob(Long id, String auditLogs) {
        AnalysisJob job = analysisJobRepository.findById(id)
                .orElseThrow(() -> new RuntimeException("Job not found: " + id));
        job.setStatus("COMPLETED");
        job.setEndTime(Instant.now());
        job.setAuditLogs(auditLogs);
        return analysisJobRepository.save(job);
    }

    public DataConnection testAndSaveConnection(DataConnection config) {
        try {
            // Attempt standard JDBC Connection test
            try (Connection conn = DriverManager.getConnection(config.getUrl(), config.getUsername(),
                    config.getPassword())) {
                if (conn.isValid(2)) {
                    return dataConnectionRepository.save(config);
                }
            }
        } catch (Exception e) {
            throw new RuntimeException("Database connection failed: " + e.getMessage());
        }
        throw new RuntimeException("Connection not valid.");
    }

    public List<DataConnection> getConnections() {
        return dataConnectionRepository.findAll();
    }

    public org.springframework.data.domain.Page<DiscoveredTable> getDiscoveredTables(Long jobId,
            org.springframework.data.domain.Pageable pageable) {
        return discoveredTableRepository.findByJobId(jobId, pageable);
    }

    @Transactional
    public List<DiscoveredTable> saveDiscoveredTables(List<DiscoveredTable> tables) {
        if (tables != null) {
            for (DiscoveredTable t : tables) {
                if (t.getColumns() != null) {
                    t.getColumns().forEach(c -> c.setDiscoveredTable(t));
                }
            }
        }
        return discoveredTableRepository.saveAll(tables);
    }

    public org.springframework.data.domain.Page<Relationship> getRelationships(Long jobId,
            org.springframework.data.domain.Pageable pageable) {
        return relationshipRepository.findByJobId(jobId, pageable);
    }

    @Transactional
    public List<Relationship> saveRelationships(List<Relationship> relationships) {
        return relationshipRepository.saveAll(relationships);
    }

    public org.springframework.data.domain.Page<DomainGroup> getDomainGroups(Long jobId,
            org.springframework.data.domain.Pageable pageable) {
        return domainGroupRepository.findByJobId(jobId, pageable);
    }

    @Transactional
    public List<DomainGroup> saveDomainGroups(List<DomainGroup> groups) {
        return domainGroupRepository.saveAll(groups);
    }

    public org.springframework.data.domain.Page<SensitiveColumn> getSensitiveColumns(Long jobId,
            org.springframework.data.domain.Pageable pageable) {
        return sensitiveColumnRepository.findByJobId(jobId, pageable);
    }

    @Transactional
    public List<SensitiveColumn> saveSensitiveColumns(List<SensitiveColumn> columns) {
        return sensitiveColumnRepository.saveAll(columns);
    }
}
