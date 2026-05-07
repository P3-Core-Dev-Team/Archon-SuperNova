package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import com.archon.openmetadata.analysis.dto.SchemaColumn;
import com.archon.openmetadata.analysis.dto.SchemaTable;
import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.services.SseBroadcasterService;

import com.archon.openmetadata.metadata.models.RelationshipEntity;
import com.archon.openmetadata.metadata.models.SchemaEntity;
import com.archon.openmetadata.metadata.models.TableEntity;
import com.archon.openmetadata.metadata.services.ColumnEntityService;
import com.archon.openmetadata.metadata.services.RelationshipService;
import com.archon.openmetadata.metadata.services.TableEntityService;
import com.archon.openmetadata.metadata.services.SchemaEntityService;
import com.archon.openmetadata.job.repositories.JobRepository;
import com.archon.openmetadata.job.models.Job;
import lombok.RequiredArgsConstructor;
import schemacrawler.schema.*;
import us.fatehi.utility.datasource.DatabaseConnectionSource;
import us.fatehi.utility.datasource.DatabaseConnectionSourceBuilder;
import us.fatehi.utility.datasource.MultiUseUserCredentials;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import java.util.*;

import schemacrawler.schemacrawler.SchemaCrawlerOptions;
import schemacrawler.schemacrawler.SchemaCrawlerOptionsBuilder;
import schemacrawler.schemacrawler.SchemaInfoLevelBuilder;
import schemacrawler.schemacrawler.LoadOptionsBuilder;
import schemacrawler.tools.utility.SchemaCrawlerUtility;

@Service
@RequiredArgsConstructor
@Slf4j
public class SchemaExtractionService {
    private final SseBroadcasterService sse;
    private final TableEntityService tableService;
    private final ColumnEntityService columnService;
    private final RelationshipService relationshipService;
    private final SchemaEntityService schemaService;
    private final JobRepository jobRepo;

    public void crawlAndSaveSchema(UUID jobId, ConnectionProfile connection) throws Exception {
        Job job = jobRepo.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        sse.broadcast(jobId, "stage", "[CRAWL] Initiating JDBC extraction pipeline.");
        
        String[] schemaNames = connection.getListOfSchemas().split(",");
        SchemaCrawlerOptions options = createSchemaCrawlerOptions();
        DatabaseConnectionSource dcs = createConnectionSource(connection);

        try {
            Catalog catalog = SchemaCrawlerUtility.getCatalog(dcs, options);
            for (String targetSchema : schemaNames) {
                processTargetSchema(job, connection, catalog, targetSchema);
            }
        } catch (Exception e) {
            log.error("Crawl failed for Job {}: {}", jobId, e.getMessage());
            throw e;
        }
    }

    private void processTargetSchema(Job job, ConnectionProfile cp, Catalog catalog, String targetName) {
        for (Schema schema : catalog.getSchemas()) {
            if (isSchemaMatch(schema.getName(), targetName)) {
                sse.broadcast(job.getId(), "log", "Processing schema: " + schema.getName());
                
                SchemaEntity schemaEntity = createSchemaEntity(job, cp, schema.getName());
                ExtractionContext context = new ExtractionContext(job, schemaEntity);
                
                var tables = catalog.getTables(schema);
                saveTablesAndColumns(context, tables);
                saveRelationships(context, tables);
                
                sse.broadcast(job.getId(), "log", "Completed schema: " + schema.getName());
            }
        }
    }

    private void saveTablesAndColumns(ExtractionContext ctx, Collection<Table> tables) {
        for (Table table : tables) {
            TableEntity te = new TableEntity();
            te.setJob(ctx.job);
            te.setSchema(ctx.schemaEntity);
            te.setTableName(table.getName());
            te.setTableType(table.getTableType().getTableType());
            te.setSchemaName(ctx.schemaEntity.getSchemaName());
            te = tableService.save(te);
            
            ctx.tableIdMap.put(table.getName(), te.getId());

            for (Column column : table.getColumns()) {
                com.archon.openmetadata.metadata.models.ColumnEntity ce = new com.archon.openmetadata.metadata.models.ColumnEntity();
                ce.setTable(te);
                ce.setColumnName(column.getName());
                ce.setColumnType(column.getColumnDataType().getName());
                ce.setColumnLength(column.getSize());
                ce.setPrimary(column.isPartOfPrimaryKey());
                ce = columnService.save(ce);
                
                ctx.columnIdMap.put(table.getName() + "." + column.getName(), ce.getId());
            }
        }
    }

    private void saveRelationships(ExtractionContext ctx, Collection<Table> tables) {
        for (Table table : tables) {
            UUID srcTableId = ctx.tableIdMap.get(table.getName());
            if (srcTableId == null) continue;
            Set<String> uniqueRels = new HashSet<>();
            for (ForeignKey fk : table.getForeignKeys()) {
                for (var fkcr : fk.getColumnReferences()) {
                    UUID tgtTableId = ctx.tableIdMap.get(fkcr.getPrimaryKeyColumn().getParent().getName());
                    if (tgtTableId == null) continue;

                    UUID srcColId = ctx.columnIdMap.get(table.getName() + "." + fkcr.getForeignKeyColumn().getName());
                    UUID tgtColId = ctx.columnIdMap.get(fkcr.getPrimaryKeyColumn().getParent().getName() + "." + fkcr.getPrimaryKeyColumn().getName());

                    if (srcColId == null || tgtColId == null) continue;
                    String sig = srcTableId + "." + srcColId + "->" + tgtTableId + "." + tgtColId;
                    if(uniqueRels.add(sig)) {
                        RelationshipEntity re = new RelationshipEntity();
                        re.setJob(ctx.job);
                        re.setSourceTable(tableService.findById(srcTableId));
                        re.setTargetTable(tableService.findById(tgtTableId));
                        re.setSourceColumn(columnService.findById(srcColId));
                        re.setTargetColumn(columnService.findById(tgtColId));
                        re.setCardinality("1:N");
                        re.setScore(1.0f);
                        relationshipService.save(re);
                    }
                }
            }
            // Inferred relationships (WeakAssociations)
            for (WeakAssociation wa : table.getWeakAssociations()) {
                for (var waColRef : wa.getColumnReferences()) {
                    Table tgtTable = waColRef.getPrimaryKeyColumn().getParent();
                    UUID targetTableId = ctx.tableIdMap.get(tgtTable.getName());
                    if (targetTableId == null) continue;

                    UUID sColId = ctx.columnIdMap.get(table.getName() + "." + waColRef.getForeignKeyColumn().getName());
                    UUID tColId = ctx.columnIdMap.get(tgtTable.getName() + "." + waColRef.getPrimaryKeyColumn().getName());

                    if (sColId == null || tColId == null) continue;

                    String sig = srcTableId + "." + sColId + "->" + targetTableId + "." + tColId;
                    if (uniqueRels.add(sig)) {
                        RelationshipEntity re = new RelationshipEntity();
                        re.setJob(ctx.job);
                        re.setSourceTable(tableService.findById(srcTableId));
                        re.setTargetTable(tableService.findById(targetTableId));
                        re.setSourceColumn(columnService.findById(sColId));
                        re.setTargetColumn(columnService.findById(tColId));
                        re.setScore(0.95f); // High confidence for weak associations
                        re.setCardinality("1:N");
                        relationshipService.save(re);
                    }
                }
            }
        }
    }

    private SchemaEntity createSchemaEntity(Job job, ConnectionProfile cp, String name) {
        SchemaEntity entity = new SchemaEntity();
        entity.setJob(job);
        entity.setSchemaName(name);
        entity.setDatasourceName(cp.getProfileName());
        return schemaService.save(entity);
    }

    private boolean isSchemaMatch(String actual, String target) {
        if (target == null || target.isBlank() || target.equals("%")) return true;
        return actual.equalsIgnoreCase(target);
    }

    private SchemaCrawlerOptions createSchemaCrawlerOptions() {
        return SchemaCrawlerOptionsBuilder.newSchemaCrawlerOptions()
                .withLoadOptions(LoadOptionsBuilder.builder()
                        .withSchemaInfoLevel(SchemaInfoLevelBuilder.maximum())
                        .toOptions());
    }

    private DatabaseConnectionSource createConnectionSource(ConnectionProfile cp) {
        String decodedPass = cp.getPass();
        try {
            decodedPass = new String(java.util.Base64.getDecoder().decode(cp.getPass()));
        } catch (Exception e) {
            // Fallback
        }
        return DatabaseConnectionSourceBuilder.builder(cp.getUrl())
                .withUserCredentials(new MultiUseUserCredentials(cp.getUser(), decodedPass))
                .build();
    }

    private static class ExtractionContext {
        final Job job;
        final SchemaEntity schemaEntity;
        final Map<String, UUID> tableIdMap = new HashMap<>();
        final Map<String, UUID> columnIdMap = new HashMap<>();

        ExtractionContext(Job job, SchemaEntity schemaEntity) {
            this.job = job;
            this.schemaEntity = schemaEntity;
        }
    }

    public BulkSchemaRequest buildBulkSchemRequestWithRelationships(UUID jobId, List<TableEntity> tables, List<RelationshipEntity> relationships) {
        BulkSchemaRequest bsr = new BulkSchemaRequest();
        List<com.archon.openmetadata.analysis.dto.SchemaTable> dtos = new ArrayList<>();

        for (TableEntity te : tables) {
            com.archon.openmetadata.analysis.dto.SchemaTable st = new com.archon.openmetadata.analysis.dto.SchemaTable();
            st.setTableName(te.getTableName());
            List<com.archon.openmetadata.analysis.dto.SchemaColumn> cols = new ArrayList<>();
            if (te.getColumns() != null) {
                for (com.archon.openmetadata.metadata.models.ColumnEntity ce : te.getColumns()) {
                    com.archon.openmetadata.analysis.dto.SchemaColumn sc = new com.archon.openmetadata.analysis.dto.SchemaColumn();
                    sc.setColumnName(ce.getColumnName());
                    sc.setDataType(ce.getColumnType());
                    sc.setLength(ce.getColumnLength());
                    sc.setPrimaryKey(ce.getPrimary());
                    cols.add(sc);
                }
            }
            st.setColumns(cols);
            dtos.add(st);
        }
        bsr.setTables(dtos);
        
        List<com.archon.openmetadata.analysis.dto.RelationshipDto> relDtos = new ArrayList<>();
        if (relationships != null) {
            for (RelationshipEntity re : relationships) {
                com.archon.openmetadata.analysis.dto.RelationshipDto rd = new com.archon.openmetadata.analysis.dto.RelationshipDto();
                rd.setSourceTableName(re.getSourceTable().getTableName());
                rd.setSourceColumnName(re.getSourceColumn().getColumnName());
                rd.setTargetTableName(re.getTargetTable().getTableName());
                rd.setTargetColumnName(re.getTargetColumn().getColumnName());
                rd.setCardinality(re.getCardinality());
                rd.setScore(re.getScore());
                relDtos.add(rd);
            }
        }
        bsr.setExistingRelationships(relDtos);
        
        return bsr;
    }
}
