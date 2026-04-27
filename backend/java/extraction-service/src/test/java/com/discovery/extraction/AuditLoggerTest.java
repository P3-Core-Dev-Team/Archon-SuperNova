package com.discovery.extraction;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.discovery.extraction.api.ConnectionConfig;
import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.ExtractionOptions;
import com.discovery.extraction.api.ExtractionRequest;
import com.discovery.extraction.api.OutputConfig;
import com.discovery.extraction.api.SslMode;
import com.discovery.extraction.core.AuditLogger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.slf4j.event.KeyValuePair;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies the {@link AuditLogger} emits structured fields and - critically -
 * never includes the raw query text in the log body.
 */
class AuditLoggerTest {

    private Logger auditLogger;
    private ListAppender<ILoggingEvent> appender;

    @BeforeEach
    void attachAppender() {
        auditLogger = (Logger) LoggerFactory.getLogger("com.discovery.extraction.audit");
        appender = new ListAppender<>();
        appender.start();
        auditLogger.addAppender(appender);
        auditLogger.setLevel(Level.INFO);
    }

    @AfterEach
    void detach() {
        auditLogger.detachAppender(appender);
    }

    @Test
    void emitsStructuredFieldsAndNoRawQuery() {
        AuditLogger logger = new AuditLogger();
        UUID id = UUID.randomUUID();
        String query = "SELECT secret_column FROM private_schema.users";
        ExtractionRequest req = new ExtractionRequest(
                new ConnectionConfig(DatabaseType.POSTGRES,
                        "host", 5432, "db", "user",
                        "env://PG_PASS", SslMode.DISABLE, "audit-test"),
                query,
                new OutputConfig("/data/out.parquet", "zstd", 3, 100_000, 1_048_576),
                new ExtractionOptions(1000, 60, null, "audit-it"));

        logger.recordAccepted(id, req);

        assertThat(appender.list).hasSize(1);
        ILoggingEvent event = appender.list.get(0);

        // The base message must NOT contain the query.
        assertThat(event.getFormattedMessage()).doesNotContain("secret_column");
        assertThat(event.getFormattedMessage()).doesNotContain("private_schema");
        assertThat(event.getFormattedMessage()).isEqualTo("extract_accepted");

        // Structured key/value pairs carry the metadata.
        Map<String, Object> kvs = asMap(event.getKeyValuePairs());
        assertThat(kvs).containsEntry("event", "extract_accepted");
        assertThat(kvs).containsEntry("request_id", id.toString());
        assertThat(kvs).containsEntry("caller_tag", "audit-it");
        assertThat(kvs).containsEntry("source_type", "postgres");
        assertThat(kvs).containsEntry("output_path", "/data/out.parquet");
        assertThat(kvs).containsEntry("query_hash", AuditLogger.sha256Hex(query));
        assertThat(kvs).containsEntry("query_length", query.length());
        // Defence in depth: no value in any structured pair contains the
        // raw query text.
        for (Object v : kvs.values()) {
            assertThat(String.valueOf(v)).doesNotContain("secret_column");
            assertThat(String.valueOf(v)).doesNotContain("private_schema");
        }
    }

    @Test
    void hashIs64HexChars() {
        String hash = AuditLogger.sha256Hex("anything");
        assertThat(hash).hasSize(64).matches("^[0-9a-f]+$");
    }

    @Test
    void recordRejectedEmitsStructuredFields() {
        AuditLogger logger = new AuditLogger();
        UUID id = UUID.randomUUID();
        logger.recordRejected(id, "tag-r", "postgres",
                AuditLogger.sha256Hex("SELECT * FROM t"),
                15, "whitelist:JOIN not allowed");

        assertThat(appender.list).hasSize(1);
        ILoggingEvent event = appender.list.get(0);
        assertThat(event.getFormattedMessage()).isEqualTo("extract_rejected");
        Map<String, Object> kvs = asMap(event.getKeyValuePairs());
        assertThat(kvs).containsEntry("event", "extract_rejected");
        assertThat(kvs).containsEntry("request_id", id.toString());
        assertThat(kvs).containsEntry("caller_tag", "tag-r");
        assertThat(kvs).containsEntry("source_type", "postgres");
        assertThat(kvs).containsEntry("query_length", 15);
        assertThat(kvs).containsEntry("rejection_reason", "whitelist:JOIN not allowed");
    }

    @Test
    void recordCompletedEmitsStructuredFields() {
        AuditLogger logger = new AuditLogger();
        UUID id = UUID.randomUUID();
        logger.recordCompleted(id, "tag-c", "postgres",
                AuditLogger.sha256Hex("Q"), 1,
                "/data/out.parquet", 12_345L, 678L, 200);

        ILoggingEvent event = appender.list.get(0);
        assertThat(event.getFormattedMessage()).isEqualTo("extract_completed");
        Map<String, Object> kvs = asMap(event.getKeyValuePairs());
        assertThat(kvs).containsEntry("event", "extract_completed");
        assertThat(kvs).containsEntry("rows_extracted", 12_345L);
        assertThat(kvs).containsEntry("duration_ms", 678L);
        assertThat(kvs).containsEntry("status_code", 200);
        assertThat(kvs).containsEntry("output_path", "/data/out.parquet");
    }

    @Test
    void recordFailedEmitsStructuredFields() {
        AuditLogger logger = new AuditLogger();
        UUID id = UUID.randomUUID();
        logger.recordFailed(id, "tag-f", "postgres",
                AuditLogger.sha256Hex("Q"), 1,
                "INTERNAL_ERROR", "boom");

        ILoggingEvent event = appender.list.get(0);
        assertThat(event.getFormattedMessage()).isEqualTo("extract_failed");
        Map<String, Object> kvs = asMap(event.getKeyValuePairs());
        assertThat(kvs).containsEntry("event", "extract_failed");
        assertThat(kvs).containsEntry("error_code", "INTERNAL_ERROR");
        assertThat(kvs).containsEntry("error_message", "boom");
    }

    private static Map<String, Object> asMap(List<KeyValuePair> pairs) {
        Map<String, Object> out = new HashMap<>();
        if (pairs != null) {
            for (KeyValuePair kv : pairs) {
                out.put(kv.key, kv.value);
            }
        }
        return out;
    }
}
