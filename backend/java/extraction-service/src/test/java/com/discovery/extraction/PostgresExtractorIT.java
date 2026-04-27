package com.discovery.extraction;

import com.discovery.extraction.api.ConnectionConfig;
import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.ExtractionOptions;
import com.discovery.extraction.api.ExtractionRequest;
import com.discovery.extraction.api.ManifestEntry;
import com.discovery.extraction.api.OutputConfig;
import com.discovery.extraction.api.SslMode;
import com.discovery.extraction.config.ApplicationProperties;
import com.discovery.extraction.core.ConnectionPoolManager;
import com.discovery.extraction.core.PostgresExtractor;
import com.discovery.extraction.core.SecretResolver;
import com.discovery.extraction.storage.LocalStorageBackend;
import com.zaxxer.hikari.HikariDataSource;
import org.apache.avro.generic.GenericRecord;
import org.apache.hadoop.conf.Configuration;
import org.apache.parquet.avro.AvroParquetReader;
import org.apache.parquet.hadoop.ParquetReader;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test: spins up a Postgres Testcontainer, seeds a tiny table,
 * runs the {@link PostgresExtractor}, and asserts the resulting Parquet
 * file manifest captures the expected row count.
 *
 * <p>The {@code nullSentinelStringSurvives} test inserts a real text value
 * equal to the legacy NULL sentinel string ("NULL_PG_NULL_MARKER"), then
 * reads the resulting Parquet file back via {@link AvroParquetReader} and
 * asserts the actual cell values - the literal sentinel survives, a real
 * Postgres NULL is read back as {@code null}, and an empty string remains
 * an empty string (not NULL). This is the round-trip regression for C1.
 */
@Testcontainers
class PostgresExtractorIT {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("discovery")
            .withUsername("postgres")
            .withPassword("secret");

    @TempDir
    static Path tempDir;

    static ApplicationProperties props;
    static ConnectionPoolManager pools;
    static LocalStorageBackend storage;

    @BeforeAll
    static void seed() throws Exception {
        postgres.start();
        props = new ApplicationProperties(
                new ApplicationProperties.Storage("local",
                        new ApplicationProperties.Storage.Local(tempDir.toString())),
                "test-token", true,
                new ApplicationProperties.SourceThrottleProps(8, 10),
                new ApplicationProperties.PoolDefaults(5, 30000, 1800000, 3600000, 30),
                new ApplicationProperties.ParquetProps("zstd", 3, 100000, 1048576));
        SecretResolver resolver = new SecretResolver(name -> switch (name) {
            case "PG_PASS" -> postgres.getPassword();
            default -> null;
        });
        pools = new ConnectionPoolManager(props, resolver);
        storage = new LocalStorageBackend(props);

        HikariDataSource ds = pools.poolFor(connectionConfig());
        try (Connection c = ds.getConnection();
             Statement s = c.createStatement()) {
            s.execute("CREATE TABLE widgets (id INT PRIMARY KEY, name TEXT, created TIMESTAMP)");
            s.execute("INSERT INTO widgets VALUES "
                    + "(1, 'alpha', '2024-01-01 00:00:00'),"
                    + "(2, 'beta', '2024-02-01 00:00:00'),"
                    + "(3, 'gamma', '2024-03-01 00:00:00')");
            s.execute("CREATE TABLE null_survival (id INT PRIMARY KEY, payload TEXT)");
            // Row 1 has the literal string that used to be the NULL sentinel.
            // Row 2 is a real NULL. Row 3 is an empty string.
            s.execute("INSERT INTO null_survival VALUES "
                    + "(1, 'NULL_PG_NULL_MARKER'),"
                    + "(2, NULL),"
                    + "(3, '')");
            c.commit();
        }
    }

    @AfterAll
    static void stop() {
        if (pools != null) {
            pools.shutdown();
        }
        postgres.stop();
    }

    @Test
    void extractsTableIntoParquet() throws Exception {
        PostgresExtractor extractor = new PostgresExtractor();
        ConnectionConfig cfg = connectionConfig();
        HikariDataSource ds = pools.poolFor(cfg);
        ExtractionRequest req = new ExtractionRequest(
                cfg,
                "SELECT * FROM widgets",
                new OutputConfig(tempDir.resolve("widgets.parquet").toString(),
                        "zstd", 3, 100000, 1048576),
                new ExtractionOptions(1000, 60, null, "pg-it"));

        List<ManifestEntry> files = extractor.extract(req, ds, storage);
        assertThat(files).hasSize(1);
        ManifestEntry entry = files.get(0);
        assertThat(entry.rows()).isEqualTo(3);
        assertThat(entry.bytes()).isPositive();
        assertThat(entry.checksumSha256()).hasSize(64);
        assertThat(java.nio.file.Files.exists(java.nio.file.Path.of(entry.path()))).isTrue();
    }

    /**
     * Round-trip test for the NULL-sentinel data-corruption bug (C1).
     * A real text cell equal to the legacy {@code NULL_PG_NULL_MARKER}
     * literal must survive extraction without being coerced to NULL.
     *
     * <p>Reads the produced Parquet back via {@link AvroParquetReader} and
     * asserts each row's payload value cell-by-cell:
     * <ul>
     *     <li>id=1 → payload = {@code "NULL_PG_NULL_MARKER"} (NOT null)</li>
     *     <li>id=2 → payload = {@code null} (real Postgres NULL preserved)</li>
     *     <li>id=3 → payload = {@code ""} (empty string preserved, not NULL)</li>
     * </ul>
     */
    @Test
    void nullSentinelStringSurvives() throws Exception {
        PostgresExtractor extractor = new PostgresExtractor();
        ConnectionConfig cfg = connectionConfig();
        HikariDataSource ds = pools.poolFor(cfg);
        ExtractionRequest req = new ExtractionRequest(
                cfg,
                "SELECT * FROM null_survival ORDER BY id",
                new OutputConfig(tempDir.resolve("null_survival.parquet").toString(),
                        "zstd", 3, 100000, 1048576),
                new ExtractionOptions(1000, 60, null, "pg-null-sentinel"));

        List<ManifestEntry> files = extractor.extract(req, ds, storage);
        assertThat(files).hasSize(1);
        ManifestEntry entry = files.get(0);
        // Necessary condition: row count is preserved.
        assertThat(entry.rows()).isEqualTo(3);

        // Sufficient condition: read the Parquet back and check each cell.
        List<GenericRecord> records = readAllRecords(Path.of(entry.path()));
        assertThat(records).hasSize(3);

        // id=1: literal sentinel-style string survives, NOT null.
        GenericRecord r1 = records.get(0);
        assertThat(((Number) r1.get("id")).intValue()).isEqualTo(1);
        Object p1 = r1.get("payload");
        assertThat(p1).as("row id=1 payload must NOT be null").isNotNull();
        assertThat(p1.toString()).isEqualTo("NULL_PG_NULL_MARKER");

        // id=2: real Postgres NULL is preserved as null.
        GenericRecord r2 = records.get(1);
        assertThat(((Number) r2.get("id")).intValue()).isEqualTo(2);
        assertThat(r2.get("payload"))
                .as("row id=2 payload must be null (real Postgres NULL)")
                .isNull();

        // id=3: empty string is preserved as "" - the most subtle round-trip
        // assertion: empty string must NOT be silently collapsed to NULL.
        GenericRecord r3 = records.get(2);
        assertThat(((Number) r3.get("id")).intValue()).isEqualTo(3);
        Object p3 = r3.get("payload");
        assertThat(p3)
                .as("row id=3 payload must be \"\" (empty string), NOT null")
                .isNotNull();
        assertThat(p3.toString()).isEqualTo("");
    }

    /**
     * Reads every {@link GenericRecord} from the Parquet file at {@code path}
     * using the same Avro decode path that {@link
     * com.discovery.extraction.parquet.StreamingParquetWriter} used to write
     * it, so the test rejects encode/decode asymmetries as well as nulling
     * regressions.
     */
    private static List<GenericRecord> readAllRecords(Path path) throws Exception {
        List<GenericRecord> out = new ArrayList<>();
        org.apache.hadoop.fs.Path hadoopPath = new org.apache.hadoop.fs.Path(path.toUri());
        Configuration conf = new Configuration();
        try (ParquetReader<GenericRecord> reader =
                     AvroParquetReader.<GenericRecord>builder(hadoopPath)
                             .withConf(conf)
                             .build()) {
            GenericRecord rec;
            while ((rec = reader.read()) != null) {
                out.add(rec);
            }
        }
        return out;
    }

    private static ConnectionConfig connectionConfig() {
        return new ConnectionConfig(
                DatabaseType.POSTGRES,
                postgres.getHost(),
                postgres.getMappedPort(5432),
                postgres.getDatabaseName(),
                postgres.getUsername(),
                "env://PG_PASS",
                SslMode.DISABLE,
                "extraction-it");
    }
}
