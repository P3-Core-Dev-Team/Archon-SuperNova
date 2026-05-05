package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import com.archon.openmetadata.analysis.dto.SchemaTable;
import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.services.SseBroadcasterService;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.HashSet;

import schemacrawler.schema.Catalog;
import schemacrawler.schema.Schema;
import schemacrawler.schema.Table;
import schemacrawler.schema.Column;
import schemacrawler.schema.ForeignKey;
import schemacrawler.schema.WeakAssociation;
import schemacrawler.schemacrawler.SchemaCrawlerOptions;
import schemacrawler.schemacrawler.SchemaCrawlerOptionsBuilder;
import schemacrawler.schemacrawler.SchemaInfoLevelBuilder;
import schemacrawler.schemacrawler.LoadOptionsBuilder;
import schemacrawler.tools.utility.SchemaCrawlerUtility;
import com.metadata.engine.be.metadata_engine_be.models.Relationship;

@Service
@RequiredArgsConstructor
public class SchemaExtractionService {
    private final SseBroadcasterService sse;

    public BulkSchemaRequest extractSchema(Long jobId, ConnectionProfile connection) throws Exception {
        sse.sendEvent(jobId,
                Map.of("stage", "[CRAWL]", "msg", "JDBC connection secured. Firing SchemaCrawler hooks.", "cls", "lm"));
        sse.sendEvent(jobId,
                Map.of("stage", "[CRAWL]", "msg", "SchemaCrawler compiling remote schema snapshot...", "cls", "lm"));

        BulkSchemaRequest bsr = new BulkSchemaRequest();
        bsr.setSchemaName(connection.getSchemaName());

        List<SchemaTable> bulkTables = new ArrayList<>();
        List<Relationship> explicitRelationships = new ArrayList<>();

        int tableCount = 0;
        int colCount = 0;

        Set<String> uniqueRels = new HashSet<>();

        try (Connection conn = DriverManager.getConnection(connection.getUrl(),
                connection.getUsername(),
                connection.getPassword())) {
            SchemaCrawlerOptions options = SchemaCrawlerOptionsBuilder.newSchemaCrawlerOptions()
                    .withLoadOptions(
                            LoadOptionsBuilder.builder()
                                    .withSchemaInfoLevel(SchemaInfoLevelBuilder.standard())
                                    .toOptions());

            us.fatehi.utility.datasource.DatabaseConnectionSource dcs = us.fatehi.utility.datasource.DatabaseConnectionSourceBuilder
                    .builder(connection.getUrl())
                    .withUserCredentials(new us.fatehi.utility.datasource.MultiUseUserCredentials(
                            connection.getUsername(), connection.getPassword()))
                    .build();

            Catalog catalog = SchemaCrawlerUtility.getCatalog(dcs, options);
            String schemaTarget = (connection.getSchemaName() != null &&
                    !connection.getSchemaName().isBlank())
                            ? connection.getSchemaName()
                            : null;

            for (Schema schema : catalog.getSchemas()) {
                if (schemaTarget != null && !schema.getName().equalsIgnoreCase(schemaTarget)
                        && !schemaTarget.equals("%")) {
                    continue;
                }

                var tables = catalog.getTables(schema);
                int totalTables = tables.size();
                int currentTable = 0;

                for (Table table : tables) {
                    currentTable++;
                    int pct = (int) Math.round(((double) currentTable / totalTables) * 100);
                    
                    tableCount++;
                    String tableName = table.getName();

                    sse.sendEvent(jobId, Map.of("stage", "[CRAWL]", "msg",
                            "Crawling schema table structure dynamically: " + tableName, "cls", "lm", "pct", pct));

                    SchemaTable st = new SchemaTable();
                    st.setTableName(tableName);
                    List<com.metadata.engine.be.metadata_engine_be.models.dto.SchemaColumn> cols = new ArrayList<>();

                    for (Column column : table.getColumns()) {
                        colCount++;
                        com.metadata.engine.be.metadata_engine_be.models.dto.SchemaColumn sc = new com.metadata.engine.be.metadata_engine_be.models.dto.SchemaColumn();
                        sc.setColumnName(column.getName());
                        sc.setDataType(column.getColumnDataType().getName());
                        sc.setLength(column.getSize());
                        sc.setPrimaryKey(column.isPartOfPrimaryKey());
                        cols.add(sc);
                    }
                    st.setColumns(cols);
                    bulkTables.add(st);

                    // Explicit Foreign Keys
                    for (ForeignKey fk : table.getForeignKeys()) {
                        for (var fkcr : fk.getColumnReferences()) {
                            String srcTable = fkcr.getForeignKeyColumn().getParent().getName();
                            String srcCol = fkcr.getForeignKeyColumn().getName();
                            String tgtTable = fkcr.getPrimaryKeyColumn().getParent().getName();
                            String tgtCol = fkcr.getPrimaryKeyColumn().getName();
                            
                            // Prevent self-joins mapping to exact same column
                            if (srcTable.equals(tgtTable) && srcCol.equals(tgtCol)) {
                                continue;
                            }
                            
                            String sig = srcTable + "." + srcCol + "->" + tgtTable + "." + tgtCol;
                            if (uniqueRels.add(sig)) {
                                Relationship r = new Relationship();
                                r.setSourceTableName(srcTable);
                                r.setSourceColumn(srcCol);
                                r.setTargetTableName(tgtTable);
                                r.setTargetColumn(tgtCol);
                                r.setScore(1.0);
                                r.setCardinality("1:N");
                                explicitRelationships.add(r);
                            }
                        }
                    }

                    // Inferred relationships (WeakAssociations)
                    for (WeakAssociation wa : table.getWeakAssociations()) {
                        String srcTable = wa.getForeignKeyTable().getName();
                        String tgtTable = wa.getPrimaryKeyTable().getName();
                        String sig = srcTable + ".implicit->" + tgtTable + ".implicit";
                        
                        if (!srcTable.equals(tgtTable) && uniqueRels.add(sig)) {
                            Relationship r = new Relationship();
                            r.setSourceTableName(srcTable);
                            r.setSourceColumn("implicit");
                            r.setTargetTableName(tgtTable);
                            r.setTargetColumn("implicit");
                            r.setScore(0.99); // Slightly less confidence than strict native DB FK constraints
                            r.setCardinality("1:N");
                            explicitRelationships.add(r);
                        }
                    }
                }
            }
        }

        bsr.setTables(bulkTables);
        bsr.setSchemaCrawlerRelationships(explicitRelationships);

        sse.sendEvent(jobId, Map.of("stage", "[CRAWL]", "msg",
                "Extracted " + tableCount + " tables, " + colCount + " columns, and " + explicitRelationships.size()
                        + " native constraints!",
                "cls", "lok"));

        return bsr;
    }
}
