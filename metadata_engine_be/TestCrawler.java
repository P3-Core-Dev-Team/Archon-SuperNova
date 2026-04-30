import schemacrawler.schema.Catalog;
import schemacrawler.schemacrawler.SchemaCrawlerOptions;
import schemacrawler.schemacrawler.SchemaCrawlerOptionsBuilder;
import schemacrawler.schemacrawler.SchemaInfoLevelBuilder;
import schemacrawler.schemacrawler.LoadOptionsBuilder;
import schemacrawler.tools.utility.SchemaCrawlerUtility;

public class TestCrawler {
    public void test() {
        SchemaCrawlerOptions options = SchemaCrawlerOptionsBuilder.newSchemaCrawlerOptions()
                .withLoadOptions(
                        LoadOptionsBuilder.builder()
                                .withSchemaInfoLevel(SchemaInfoLevelBuilder.maximum())
                                .toOptions()
                );
                
        com.zaxxer.hikari.HikariConfig config = new com.zaxxer.hikari.HikariConfig();
        config.setJdbcUrl("jdbc:postgresql://127.0.0.1:5432/metadata_engine");
        config.setUsername("adsuser");
        config.setPassword("AdS@3421");
        
        try (com.zaxxer.hikari.HikariDataSource ds = new com.zaxxer.hikari.HikariDataSource(config)) {
            us.fatehi.utility.datasource.DatabaseConnectionSource dcs = us.fatehi.utility.datasource.DatabaseConnectionSources.fromDataSource(ds);
            Catalog catalog = SchemaCrawlerUtility.getCatalog(dcs, options);
        }
    }
}
