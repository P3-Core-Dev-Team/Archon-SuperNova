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
import com.archon.openmetadata.metadata.services.DomainGroupService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import com.archon.openmetadata.metadata.models.TableEntity;
import com.archon.openmetadata.metadata.models.RelationshipEntity;
import com.archon.openmetadata.metadata.services.TableEntityService;
import com.archon.openmetadata.metadata.services.RelationshipService;
import com.archon.openmetadata.metadata.services.SchemaEntityService;
import com.archon.openmetadata.job.models.Job;

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
    private final DomainGroupService domainGroupService;
    private final SchemaExtractionService schemaExtractionService;
    private final TableEntityService tableService;
    private final RelationshipService relationshipService;

    @org.springframework.transaction.annotation.Transactional
    @Scheduled(fixedRate = 60000) // Poll every 10 seconds
    public void processJobs() {
        List<Job> pending = repo.findWithProfilesByStatusIgnoreCase("Pending");

        for (Job j : pending) {
            executePipeline(j);
        }
    }

    private void executePipeline(Job job) {
        log.info("Starting execution for Job ID: {} ({})", job.getId(), job.getJobName());
        job.setStatus("Running");
        job.setStartTime(LocalDateTime.now());
        StringBuilder auditLog = new StringBuilder("Job Started at " + job.getStartTime() + "\n");
        repo.save(job);
        sse.broadcast(job.getId(), "status", "Running");
        sse.broadcast(job.getId(), "log", "Job Started at " + job.getStartTime());

        try {
            List<JobTemplateOptionRule> activeRules = job.getJobTemplateProfile() != null
                    ? job.getJobTemplateProfile().getOptions()
                    : List.of();

            List<OperationType> enabledStages = activeRules.stream()
                    .map(JobTemplateOptionRule::getOptionType)
                    .toList();

            // 1. Mandatory Stage: Schema Extraction
            sse.broadcast(job.getId(), "stage", "SCHEMA_EXTRACTION");
            sse.broadcast(job.getId(), "log", "[Stage] SCHEMA_EXTRACTION: Executing core JDBC extraction...");
            
            schemaExtractionService.crawlAndSaveSchema(job.getId(), job.getDatasourceProfile());

            int pageSize = 50;
            int pageNum = 0;
            Page<TableEntity> tablePage;

            do {
                tablePage = tableService.findAll(
                        (root, query, cb) -> cb.equal(root.get("job").get("id"), job.getId()),
                        PageRequest.of(pageNum, pageSize)
                );

                if (!tablePage.isEmpty()) {
                    List<RelationshipEntity> relationships = relationshipService.findAll(
                            (root, query, cb) -> cb.equal(root.get("job").get("id"), job.getId()),
                            Pageable.unpaged()
                    ).getContent();

                    BulkSchemaRequest bsr = schemaExtractionService.buildBulkSchemRequestWithRelationships(
                            job.getId(), tablePage.getContent(), relationships);
                    startAnalysisWithStagesLevel(job, enabledStages, auditLog, bsr, activeRules);
                }
                pageNum++;
            } while (tablePage.hasNext());

            sse.broadcast(job.getId(), "log", "Job Completed Successfully at " + LocalDateTime.now());
            auditLog.append("Job Completed Successfully at " + LocalDateTime.now() + "\n");
            job.setStatus("Done");
            sse.broadcast(job.getId(), "status", "Done");
        } catch (Exception e) {
            log.error("Pipeline failure for Job ID: {}", job.getId(), e);
            sse.broadcast(job.getId(), "log", "FATAL ERROR: " + e.getMessage());
            auditLog.append("FATAL ERROR: ").append(e.getMessage()).append("\n");
            job.setStatus("Failed");
            sse.broadcast(job.getId(), "status", "Failed");
        } finally {
            job.setEndTime(LocalDateTime.now());
            job.setAuditlogs(auditLog.toString());
            repo.save(job);
            sse.complete(job.getId());
        }
    }

    private void startAnalysisWithStagesLevel(Job job, List<OperationType> enabledStages,
                                              StringBuilder auditLog,
                                              BulkSchemaRequest schemaRequest,
                                              List<JobTemplateOptionRule> activeRules) throws InterruptedException {
        log.info("Executing Schema Extraction...");

        CandidateResponse candidateRes = new CandidateResponse();
        candidateRes.setCandidates(new java.util.ArrayList<>());

        // 2. Candidate Generation
        if (enabledStages.contains(OperationType.CANDIDATE_FUZZY_MATCHING)) {
            sse.broadcast(job.getId(), "stage", "CANDIDATE_GENERATION");
            sse.broadcast(job.getId(), "log", "[Stage] CANDIDATE_GENERATION: Fetching ML candidates...");
            auditLog.append("[Stage] CANDIDATE_GENERATION: Fetching ML candidates...\n");
            log.info("Executing CANDIDATE_GENERATION via FastAPI...");
            applyMinMax(schemaRequest, OperationType.CANDIDATE_FUZZY_MATCHING, activeRules);
            candidateRes = candidateGen.generateCandidates(schemaRequest);
            if (candidateRes == null) {
                candidateRes = new CandidateResponse();
                candidateRes.setCandidates(new java.util.ArrayList<>());
            }
            Thread.sleep(2000);
        }

        // 3. Semantic Scoring
        if (enabledStages.contains(OperationType.SEMANTIC_ANALYSIS)) {
            sse.broadcast(job.getId(), "stage", "SEMANTIC_SCORING");
            sse.broadcast(job.getId(), "log", "[Stage] SEMANTIC_SCORING: Context evaluation...");
            auditLog.append("[Stage] SEMANTIC_SCORING: Context evaluation...\n");
            log.info("Executing SEMANTIC_SCORING via FastAPI...");
            applyMinMax(candidateRes, OperationType.SEMANTIC_ANALYSIS, activeRules);
            semanticScoring.evaluateRelationships(candidateRes);
            Thread.sleep(2000);
        }

        // 4. Cardinality Mapping
        if (enabledStages.contains(OperationType.CARDINALITY_DETECTION_SOURCE_COUNT)) {
            sse.broadcast(job.getId(), "stage", "CARDINALITY_MAPPING");
            sse.broadcast(job.getId(), "log", "[Stage] CARDINALITY_MAPPING: Resolving cardinality bounds...");
            auditLog.append("[Stage] CARDINALITY_MAPPING: Resolving cardinality bounds...\n");
            log.info("Executing CARDINALITY_MAPPING via FastAPI...");
            CardinalityRequest cardReq = new CardinalityRequest();
            cardReq.setCandidates(new java.util.ArrayList<>());
            applyMinMax(cardReq, OperationType.CARDINALITY_DETECTION_SOURCE_COUNT, activeRules);
            cardinalityRes.resolveCardinalities(cardReq);
            Thread.sleep(2000);
        }

        // 5. Sensitive Detection
        if (enabledStages.contains(OperationType.SENSITIVE_ANALYSIS_TABLE_DATA)) {
            sse.broadcast(job.getId(), "stage", "SENSITIVE_DETECTION");
            sse.broadcast(job.getId(), "log", "[Stage] SENSITIVE_DETECTION: Scanning for PII with SpaCy...");
            auditLog.append("[Stage] SENSITIVE_DETECTION: Scanning for PII with SpaCy...\n");
            log.info("Executing SENSITIVE_DETECTION via FastAPI...");
            applyMinMax(schemaRequest, OperationType.SENSITIVE_ANALYSIS_TABLE_DATA, activeRules);
            sensitiveDetection.detectSensitiveEntities(schemaRequest);
            Thread.sleep(2000);
        }

        // 6. Domain Aggregation
        if (enabledStages.contains(OperationType.TABLE_DOMAIN_GROUPING)) {
            sse.broadcast(job.getId(), "stage", "DOMAIN_AGGREGATION");
            sse.broadcast(job.getId(), "log", "[Stage] DOMAIN_AGGREGATION: Clustering domain vectors...");
            auditLog.append("[Stage] DOMAIN_AGGREGATION: Clustering domain vectors...\n");
            log.info("Executing DOMAIN_AGGREGATION via FastAPI...");
            applyMinMax(schemaRequest, OperationType.TABLE_DOMAIN_GROUPING, activeRules);
            domainGrouping.extractDomainGroups(schemaRequest);
            Thread.sleep(2000);
        }

        // 7. Mandatory Stage: ERD / Graph Generation
        sse.broadcast(job.getId(), "stage", "ERD_GENERATION");
        sse.broadcast(job.getId(), "log", "[Stage] ERD_GENERATION: Compiling Final Graph Matrix...");
        auditLog.append("[Stage] ERD_GENERATION: Compiling Final Graph Matrix...\n");
        log.info("Executing ERD_GENERATION via FastAPI...");
        GraphContextRequest graphReq = new GraphContextRequest();
        graphReq.setRelationships(new java.util.ArrayList<>());
        graphReq.setClusters(new java.util.ArrayList<>());
        applyMinMax(graphReq, OperationType.GRAPH_BUILDING_DETECTION, activeRules);
        graphContext.generateContextGraph(graphReq);
        Thread.sleep(2000);

        // 8. Entity Classification
        if (enabledStages.contains(OperationType.DATA_CLASSIFICATION_TABLE_TYPE)) {
            sse.broadcast(job.getId(), "stage", "ENTITY_CLASSIFICATION");
            sse.broadcast(job.getId(), "log", "[Stage] ENTITY_CLASSIFICATION: Determining entity boundaries...");
            auditLog.append("[Stage] ENTITY_CLASSIFICATION: Determining entity boundaries...\n");
            log.info("Executing ENTITY_CLASSIFICATION via FastAPI...");
            applyMinMax(schemaRequest, OperationType.DATA_CLASSIFICATION_TABLE_TYPE, activeRules);
            entityClassification.classifyEntities(schemaRequest);
            Thread.sleep(2000);
        }
    }

    private void applyMinMax(Object dto, OperationType op, List<JobTemplateOptionRule> rules) {
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
