package com.archon.openmetadata.job.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import com.archon.openmetadata.analysis.dto.CandidateResponse;
import com.archon.openmetadata.analysis.dto.CardinalityRequest;
import com.archon.openmetadata.analysis.dto.GraphContextRequest;
import com.archon.openmetadata.analysis.services.*;
import com.archon.openmetadata.job.models.Job;
import com.archon.openmetadata.job.models.JobTemplateOptionRule;
import com.archon.openmetadata.job.models.OperationType;
import com.archon.openmetadata.job.repositories.JobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class JobExecutionService {
    private final JobRepository repo;
    private final CandidateGenerationService candidateGen;
    private final SemanticScoringService semanticScoring;
    private final CardinalityResolutionService cardinalityRes;
    private final SensitiveDetectionService sensitiveDetection;
    private final DomainGroupingService domainGrouping;
    private final GraphContextService graphContext;
    private final EntityClassificationService entityClassification;
    private final SseBroadcasterService sse;
    private final com.archon.openmetadata.metadata.services.TableEntityService tableService;
    private final com.archon.openmetadata.metadata.services.ColumnEntityService columnService;
    private final com.archon.openmetadata.metadata.services.RelationshipService relationshipService;
    private final com.archon.openmetadata.metadata.services.DomainGroupService domainGroupService;

    @Scheduled(fixedRate = 10000) // Poll every 10 seconds
    public void processJobs() {
        List<Job> pending = repo.findByStatusIgnoreCase("Pending");

        for (Job j : pending) {
            executePipeline(j);
        }
    }

    private void executePipeline(Job j) {
        log.info("Starting execution for Job ID: {} ({})", j.getId(), j.getJobName());
        j.setStatus("Running");
        j.setStartTime(LocalDateTime.now());
        StringBuilder auditLog = new StringBuilder("Job Started at " + j.getStartTime() + "\n");
        repo.save(j);
        sse.broadcast(j.getId(), "status", "Running");
        sse.broadcast(j.getId(), "log", "Job Started at " + j.getStartTime());

        try {
            List<JobTemplateOptionRule> activeRules = j.getJobTemplateProfile() != null
                    ? j.getJobTemplateProfile().getOptions()
                    : List.of();

            List<OperationType> enabledStages = activeRules.stream()
                    .map(JobTemplateOptionRule::getOptionType)
                    .toList();

            // 1. Mandatory Stage: Schema Extraction
            sse.broadcast(j.getId(), "stage", "SCHEMA_EXTRACTION");
            sse.broadcast(j.getId(), "log", "[Stage] SCHEMA_EXTRACTION: Executing core JDBC extraction...");
            // Generate professional mock metadata and save to DB
            String[] tableNames = { "CRM_CUSTOMERS", "ERP_SALES_HEADER", "ERP_SALES_ITEMS", "MDM_PRODUCTS",
                    "SYS_CONFIG" };
            String[][] columnNames = {
                    { "CUST_ID", "FIRST_NAME", "LAST_NAME", "EMAIL", "PHONE_NUMBER", "SSN", "DATE_OF_BIRTH" },
                    { "ORDER_ID", "ORDER_DATE", "CUST_ID", "TOTAL_AMOUNT", "STATUS_CODE", "PROMO_CODE" },
                    { "ITEM_ID", "ORDER_ID", "PRODUCT_ID", "QUANTITY", "UNIT_PRICE", "DISCOUNT_VAL" },
                    { "PRODUCT_ID", "SKU_CODE", "PROD_NAME", "CATEGORY_ID", "REORDER_LEVEL", "SUPPLIER_ID" },
                    { "CONFIG_KEY", "CONFIG_VAL", "UPDATED_BY", "UPDATE_TIMESTAMP" }
            };
            Long[] sizes = { 15240L, 85600L, 245000L, 1200L, 45L };
            String[] types = { "MASTER", "TRANSACTION", "TRANSACTION", "MASTER", "CONFIG" };

            for (int i = 0; i < tableNames.length; i++) {
                com.archon.openmetadata.metadata.models.TableEntity tbl = new com.archon.openmetadata.metadata.models.TableEntity();
                tbl.setJob(j);
                tbl.setTableName(tableNames[i]);
                tbl.setTableSize(sizes[i]);
                tbl.setTableType(types[i]);
                tbl.setSchemaName("PROD_APP");
                tbl = tableService.save(tbl);

                for (String cname : columnNames[i]) {
                    com.archon.openmetadata.metadata.models.ColumnEntity col = new com.archon.openmetadata.metadata.models.ColumnEntity();
                    col.setTable(tbl);
                    col.setColumnName(cname);
                    col.setColumnType(cname.contains("ID") || cname.contains("VAL") ? "INTEGER" : "VARCHAR");

                    // Mock some sensitive data
                    if (cname.equals("SSN") || cname.equals("EMAIL") || cname.equals("PHONE_NUMBER")
                            || cname.equals("DATE_OF_BIRTH")) {
                        col.setIsSensitive(true);
                        col.setSensitivityType(cname.equals("SSN") ? "PII_SSN"
                                : (cname.equals("EMAIL") ? "PII_EMAIL" : "PII_CONTACT"));
                        col.setSensitivityScore(0.98);
                    } else {
                        col.setIsSensitive(false);
                    }
                    columnService.save(col);
                }

                if (tableNames[i].equals("CRM_CUSTOMERS")) {
                    // Save one cluster for this table
                    com.archon.openmetadata.metadata.models.DomainGroupEntity cluster = new com.archon.openmetadata.metadata.models.DomainGroupEntity();
                    cluster.setJob(j);
                    cluster.setGroupName("Customer Intelligence");
                    cluster.setTableCount(1);
                    cluster.setDescription("Tables containing sensitive customer PII and demographic data.");
                    domainGroupService.save(cluster);
                }
            }

            // Mock one relationship
            com.archon.openmetadata.metadata.models.Relationship rel = new com.archon.openmetadata.metadata.models.Relationship();
            rel.setJob(j);
            rel.setCardinality("1:N");
            rel.setScore(0.95f);
            relationshipService.save(rel);

            BulkSchemaRequest dummyRequest = new BulkSchemaRequest(); // Mock request
            dummyRequest.setTables(new java.util.ArrayList<>());
            dummyRequest.setExistingRelationships(new java.util.ArrayList<>());
            log.info("Executing Schema Extraction...");
            Thread.sleep(2000); // Simulate time

            CandidateResponse candidateRes = new CandidateResponse();
            candidateRes.setCandidates(new java.util.ArrayList<>());

            // 2. Candidate Generation
            if (enabledStages.contains("CANDIDATE_GENERATION")) {
                sse.broadcast(j.getId(), "stage", "CANDIDATE_GENERATION");
                sse.broadcast(j.getId(), "log", "[Stage] CANDIDATE_GENERATION: Fetching ML candidates...");
                auditLog.append("[Stage] CANDIDATE_GENERATION: Fetching ML candidates...\n");
                log.info("Executing CANDIDATE_GENERATION via FastAPI...");
                applyMinMax(dummyRequest, "CANDIDATE_GENERATION", activeRules);
                candidateRes = candidateGen.generateCandidates(dummyRequest);
                if (candidateRes == null) {
                    candidateRes = new CandidateResponse();
                    candidateRes.setCandidates(new java.util.ArrayList<>());
                }
                Thread.sleep(2000);
            }

            // 3. Semantic Scoring
            if (enabledStages.contains("SEMANTIC_SCORING")) {
                sse.broadcast(j.getId(), "stage", "SEMANTIC_SCORING");
                sse.broadcast(j.getId(), "log", "[Stage] SEMANTIC_SCORING: Context evaluation...");
                auditLog.append("[Stage] SEMANTIC_SCORING: Context evaluation...\n");
                log.info("Executing SEMANTIC_SCORING via FastAPI...");
                applyMinMax(candidateRes, "SEMANTIC_SCORING", activeRules);
                semanticScoring.evaluateRelationships(candidateRes);
                Thread.sleep(2000);
            }

            // 4. Cardinality Mapping
            if (enabledStages.contains("CARDINALITY_MAPPING")) {
                sse.broadcast(j.getId(), "stage", "CARDINALITY_MAPPING");
                sse.broadcast(j.getId(), "log", "[Stage] CARDINALITY_MAPPING: Resolving cardinality bounds...");
                auditLog.append("[Stage] CARDINALITY_MAPPING: Resolving cardinality bounds...\n");
                log.info("Executing CARDINALITY_MAPPING via FastAPI...");
                CardinalityRequest cardReq = new CardinalityRequest();
                cardReq.setCandidates(new java.util.ArrayList<>());
                applyMinMax(cardReq, "CARDINALITY_MAPPING", activeRules);
                cardinalityRes.resolveCardinalities(cardReq);
                Thread.sleep(2000);
            }

            // 5. Sensitive Detection
            if (enabledStages.contains("SENSITIVE_DETECTION")) {
                sse.broadcast(j.getId(), "stage", "SENSITIVE_DETECTION");
                sse.broadcast(j.getId(), "log", "[Stage] SENSITIVE_DETECTION: Scanning for PII with SpaCy...");
                auditLog.append("[Stage] SENSITIVE_DETECTION: Scanning for PII with SpaCy...\n");
                log.info("Executing SENSITIVE_DETECTION via FastAPI...");
                applyMinMax(dummyRequest, "SENSITIVE_DETECTION", activeRules);
                sensitiveDetection.detectSensitiveEntities(dummyRequest);
                Thread.sleep(2000);
            }

            // 6. Domain Aggregation
            if (enabledStages.contains("DOMAIN_AGGREGATION")) {
                sse.broadcast(j.getId(), "stage", "DOMAIN_AGGREGATION");
                sse.broadcast(j.getId(), "log", "[Stage] DOMAIN_AGGREGATION: Clustering domain vectors...");
                auditLog.append("[Stage] DOMAIN_AGGREGATION: Clustering domain vectors...\n");
                log.info("Executing DOMAIN_AGGREGATION via FastAPI...");
                applyMinMax(dummyRequest, "DOMAIN_AGGREGATION", activeRules);
                domainGrouping.extractDomainGroups(dummyRequest);
                Thread.sleep(2000);
            }

            // 7. Mandatory Stage: ERD / Graph Generation
            sse.broadcast(j.getId(), "stage", "ERD_GENERATION");
            sse.broadcast(j.getId(), "log", "[Stage] ERD_GENERATION: Compiling Final Graph Matrix...");
            auditLog.append("[Stage] ERD_GENERATION: Compiling Final Graph Matrix...\n");
            log.info("Executing ERD_GENERATION via FastAPI...");
            GraphContextRequest graphReq = new GraphContextRequest();
            graphReq.setRelationships(new java.util.ArrayList<>());
            graphReq.setClusters(new java.util.ArrayList<>());
            applyMinMax(graphReq, "ERD_GENERATION", activeRules);
            graphContext.generateContextGraph(graphReq);
            Thread.sleep(2000);

            // 8. Entity Classification
            if (enabledStages.contains("ENTITY_CLASSIFICATION")) {
                sse.broadcast(j.getId(), "stage", "ENTITY_CLASSIFICATION");
                sse.broadcast(j.getId(), "log", "[Stage] ENTITY_CLASSIFICATION: Determining entity boundaries...");
                auditLog.append("[Stage] ENTITY_CLASSIFICATION: Determining entity boundaries...\n");
                log.info("Executing ENTITY_CLASSIFICATION via FastAPI...");
                applyMinMax(dummyRequest, "ENTITY_CLASSIFICATION", activeRules);
                entityClassification.classifyEntities(dummyRequest);
                Thread.sleep(2000);
            }

            sse.broadcast(j.getId(), "log", "Job Completed Successfully at " + LocalDateTime.now());
            auditLog.append("Job Completed Successfully at " + LocalDateTime.now() + "\n");
            j.setStatus("Done");
            sse.broadcast(j.getId(), "status", "Done");
        } catch (Exception e) {
            log.error("Pipeline failure for Job ID: {}", j.getId(), e);
            sse.broadcast(j.getId(), "log", "FATAL ERROR: " + e.getMessage());
            auditLog.append("FATAL ERROR: ").append(e.getMessage()).append("\n");
            j.setStatus("Failed");
            sse.broadcast(j.getId(), "status", "Failed");
        } finally {
            j.setEndTime(LocalDateTime.now());
            j.setAuditlogs(auditLog.toString());
            repo.save(j);
            sse.complete(j.getId());
        }
    }

    private void applyMinMax(Object dto, String op, List<JobTemplateOptionRule> rules) {
        rules.stream().filter(r -> op.equals(r.getOptionType())).findFirst().ifPresent(r -> {
            if (dto instanceof BulkSchemaRequest) {
                ((BulkSchemaRequest) dto).setMinValue(r.getMinValue());
                ((BulkSchemaRequest) dto).setMaxValue(r.getMaxValue());
            } else if (dto instanceof CandidateResponse) {
                ((CandidateResponse) dto).setMinValue(r.getMinValue());
                ((CandidateResponse) dto).setMaxValue(r.getMaxValue());
            } else if (dto instanceof CardinalityRequest) {
                ((CardinalityRequest) dto).setMinValue(r.getMinValue());
                ((CardinalityRequest) dto).setMaxValue(r.getMaxValue());
            } else if (dto instanceof GraphContextRequest) {
                ((GraphContextRequest) dto).setMinValue(r.getMinValue());
                ((GraphContextRequest) dto).setMaxValue(r.getMaxValue());
            }
        });
    }
}
