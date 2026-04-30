package com.discovery.extraction.core;

import com.discovery.extraction.api.ConnectionConfig;
import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.ExtractionRequest;
import com.discovery.extraction.api.ExtractionResponse;
import com.discovery.extraction.api.ManifestEntry;
import com.discovery.extraction.exception.ExtractionException;
import com.discovery.extraction.exception.QueryNotAllowedException;
import com.discovery.extraction.metrics.ExtractionMetrics;
import com.discovery.extraction.storage.LocalStorageBackend;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * Central monolithic orchestrator. Runs the following pipeline:
 *
 * <ol>
 *     <li>Validate the request body (null checks)</li>
 *     <li>Apply {@link QueryWhitelistValidator} - throws
 *         {@link com.discovery.extraction.exception.QueryNotAllowedException}.
 *         A {@code recordRejected} audit event is emitted on rejection.</li>
 *     <li>Emit {@code recordAccepted} (whitelist passed, bulkhead not yet
 *         acquired)</li>
 *     <li>Resolve the {@code HikariDataSource} via {@link ConnectionPoolManager}</li>
 *     <li>Acquire a permit from the global {@link SourceThrottle} bulkhead.
 *         A {@code recordRejected} event is emitted if the bulkhead is full
 *         ({@code rejection_reason="bulkhead-full"}).</li>
 *     <li>Invoke {@link PostgresExtractor} directly (only supported source).
 *         {@code recordCompleted} on success or {@code recordFailed} on
 *         non-validation failure.</li>
 *     <li>Return an {@link ExtractionResponse} with the manifest</li>
 * </ol>
 *
 * <p>Synchronous, blocks until done. There is no async path, no registry, no
 * cancellation - if the caller disconnects mid-extraction the JVM keeps
 * running until the COPY completes, then the response is dropped.
 */
@Service
public class ExtractionService {

    private static final Logger log = LoggerFactory.getLogger(ExtractionService.class);

    private final QueryWhitelistValidator whitelist;
    private final ConnectionPoolManager pools;
    private final SourceThrottle throttle;
    private final PostgresExtractor extractor;
    private final LocalStorageBackend storage;
    private final ExtractionMetrics metrics;
    private final AuditLogger audit;

    public ExtractionService(QueryWhitelistValidator whitelist,
                             ConnectionPoolManager pools,
                             SourceThrottle throttle,
                             PostgresExtractor extractor,
                             LocalStorageBackend storage,
                             ExtractionMetrics metrics,
                             AuditLogger audit) {
        this.whitelist = whitelist;
        this.pools = pools;
        this.throttle = throttle;
        this.extractor = extractor;
        this.storage = storage;
        this.metrics = metrics;
        this.audit = audit;
    }

    /**
     * Runs the extraction synchronously and returns a populated
     * {@link ExtractionResponse}.
     */
    public ExtractionResponse extractSync(ExtractionRequest request) {
        UUID id = UUID.randomUUID();
        MDC.put("request_id", id.toString());

        // Pre-compute audit-context values before any work so every audit
        // emission below can reuse them. These are safe to compute even if
        // the request is invalid; null fields produce empty strings.
        String query = request == null ? "" : (request.query() == null ? "" : request.query());
        String queryHash = AuditLogger.sha256Hex(query);
        int queryLength = query.length();
        String callerTag = (request != null && request.options() != null)
                ? request.options().tag() : null;
        DatabaseType reqType = (request != null && request.connection() != null)
                ? request.connection().type() : null;
        String sourceType = reqType == null ? "" : reqType.wire();
        String outputPath = (request != null && request.output() != null)
                ? request.output().path() : null;

        try {
            // 1. Validate basic shape (null/blank checks). These are
            // BAD_REQUEST and not audit-rejected; they never touched the
            // whitelist or pool.
            validateRequired(request, "request");
            validateRequired(request.query(), "query");
            validateRequired(request.connection(), "connection");
            validateRequired(request.output(), "output");
            validateRequired(request.output().path(), "output.path");
            validateRequired(request.connection().type(), "connection.type");

            // 2. Whitelist - emit recordRejected if rejected, then rethrow.
            try {
                whitelist.validate(request.query());
            } catch (QueryNotAllowedException qnae) {
                audit.recordRejected(id, callerTag, sourceType, queryHash, queryLength,
                        "whitelist:" + qnae.getMessage());
                throw qnae;
            }

            // 3. Whitelist passed - now emit the accepted audit event before
            // any bulkhead admission so it semantically means "we plan to run".
            audit.recordAccepted(id, request);

            // 4. Pool
            DatabaseType type = request.connection().type();
            if (type != DatabaseType.POSTGRES) {
                // The outer catch(ExtractionException) emits recordFailed
                // for non-throttle, non-bad-request codes, so this path
                // produces exactly one audit event.
                throw new ExtractionException(
                        "UNSUPPORTED_SOURCE",
                        "Only postgres source type is supported in this build",
                        false,
                        null);
            }
            HikariDataSource ds = pools.poolFor(request.connection());

            // 5. Throttle + extract
            String throttleKey = request.connection().throttleKey();
            String tag = request.optionsOrDefault().tag();
            log.info("Extraction start id={} type={} throttleKey={} tag={}",
                    id, type.wire(), throttleKey, tag);

            Instant start = Instant.now();
            try {
                List<ManifestEntry> files = throttle.call(throttleKey, () -> {
                    metrics.onExtractionStart(throttleKey);
                    try {
                        return extractor.extract(request, ds, storage);
                    } catch (Exception e) {
                        throw new RuntimeExecutionException(e);
                    } finally {
                        metrics.onExtractionEnd(throttleKey);
                    }
                });

                Duration dur = Duration.between(start, Instant.now());
                long rows = files.stream().mapToLong(ManifestEntry::rows).sum();
                long bytes = files.stream().mapToLong(ManifestEntry::bytes).sum();
                long seconds = Math.max(1, dur.toMillis() / 1000);
                long rowsPerSec = rows / seconds;
                long bytesPerSec = bytes / seconds;

                metrics.recordRowsExtracted(type.wire(), rows);
                metrics.recordBytesWritten(type.wire(), bytes);
                metrics.recordDuration("completed", dur);
                metrics.recordRowGroups(files.stream().mapToInt(ManifestEntry::rowGroups).sum());
                metrics.recordRequest("completed");

                String resolvedOutputPath = files.isEmpty()
                        ? (outputPath == null ? "" : outputPath)
                        : files.get(0).path();
                audit.recordCompleted(id, callerTag, sourceType, queryHash, queryLength,
                        resolvedOutputPath, rows, dur.toMillis(), 200);

                log.info("Extraction done id={} rows={} bytes={} duration_ms={}",
                        id, rows, bytes, dur.toMillis());
                return ExtractionResponse.success(id,
                        new ExtractionResponse.Manifest(files, dur.toMillis(), rowsPerSec, bytesPerSec));
            } catch (RuntimeExecutionException rex) {
                Throwable cause = rex.getCause();
                if (cause instanceof ExtractionException ee) {
                    throw ee;
                }
                throw new ExtractionException("INTERNAL_ERROR",
                        cause == null ? rex.getMessage() : cause.getMessage(),
                        false, cause);
            }
        } catch (QueryNotAllowedException qnae) {
            // Already audited as rejected above; just bookkeep metrics.
            metrics.recordRequest("failed");
            throw qnae;
        } catch (ExtractionException ee) {
            metrics.recordRequest("failed");
            String code = ee.getCode() == null ? "" : ee.getCode();
            if ("SOURCE_THROTTLED".equals(code)) {
                audit.recordRejected(id, callerTag, sourceType, queryHash, queryLength,
                        "bulkhead-full");
            } else if ("BAD_REQUEST".equals(code)) {
                // Skip audit: validation failures aren't part of the
                // accepted/rejected/completed/failed audit trail.
            } else {
                audit.recordFailed(id, callerTag, sourceType, queryHash, queryLength,
                        ee.getCode(), ee.getMessage());
            }
            throw ee;
        } catch (Exception e) {
            metrics.recordRequest("failed");
            audit.recordFailed(id, callerTag, sourceType, queryHash, queryLength,
                    "INTERNAL_ERROR", e.getMessage());
            throw new ExtractionException("INTERNAL_ERROR", e.getMessage(), false, e);
        } finally {
            MDC.remove("request_id");
        }
    }

    /**
     * Live cardinality probe (Sprint 4).  For each {@code (schema, table,
     * column)} triple, runs {@code SELECT COUNT(*), COUNT(DISTINCT col)
     * FROM "schema"."table"} on the source DB and returns the totals.
     *
     * <p>Identifiers are validated with a strict regex
     * ({@link #VALID_IDENT}) before being spliced into SQL — JDBC
     * doesn't parameterise identifiers so the only safe path is
     * ALLOW-list-then-quote.  Any pair that fails validation, or that
     * the database rejects (table missing, permission denied) is
     * absent from the result list rather than emitted as a sentinel.
     *
     * <p>Postgres-only in this build, mirroring the rest of the
     * service's source-type support.  Connection reuse via the
     * existing {@link ConnectionPoolManager} so we don't multiply
     * pools when the pipeline submits batches.
     */
    public com.discovery.extraction.api.CardinalityProbeResponse probeCardinality(
            com.discovery.extraction.api.CardinalityProbeRequest request) {
        validateRequired(request, "request");
        validateRequired(request.connection(), "connection");
        validateRequired(request.connection().type(), "connection.type");
        if (request.connection().type() != DatabaseType.POSTGRES) {
            throw new ExtractionException(
                    "UNSUPPORTED_SOURCE",
                    "Only postgres source type is supported in this build",
                    false,
                    null);
        }
        java.util.List<com.discovery.extraction.api.CardinalityProbeRequest.Pair> pairs =
                request.pairs() == null
                        ? java.util.List.of()
                        : request.pairs();

        java.util.List<com.discovery.extraction.api.CardinalityProbeResponse.Result> out =
                new java.util.ArrayList<>(pairs.size());

        com.zaxxer.hikari.HikariDataSource ds = pools.poolFor(request.connection());
        try (java.sql.Connection conn = ds.getConnection()) {
            for (com.discovery.extraction.api.CardinalityProbeRequest.Pair p : pairs) {
                if (!isSafeIdentifier(p.schema())
                        || !isSafeIdentifier(p.table())
                        || !isSafeIdentifier(p.column())) {
                    log.warn("probeCardinality.skipped_identifier schema={} table={} column={}",
                            p.schema(), p.table(), p.column());
                    continue;
                }
                String sql = String.format(
                        "SELECT COUNT(*) AS total, COUNT(DISTINCT %s) AS distinct_count "
                                + "FROM %s.%s",
                        quoteIdent(p.column()),
                        quoteIdent(p.schema()),
                        quoteIdent(p.table()));
                try (java.sql.Statement st = conn.createStatement();
                     java.sql.ResultSet rs = st.executeQuery(sql)) {
                    if (rs.next()) {
                        out.add(new com.discovery.extraction.api.CardinalityProbeResponse.Result(
                                p.schema(), p.table(), p.column(),
                                rs.getLong(1), rs.getLong(2)));
                    }
                } catch (java.sql.SQLException sqe) {
                    // Permission errors / missing tables / etc. — log
                    // and skip; the response simply omits this pair.
                    log.warn("probeCardinality.skipped sql_state={} message={}",
                            sqe.getSQLState(), sqe.getMessage());
                }
            }
        } catch (java.sql.SQLException sqe) {
            throw new ExtractionException(
                    "CONNECTION_FAILED",
                    sqe.getMessage(),
                    true,
                    sqe);
        }
        return new com.discovery.extraction.api.CardinalityProbeResponse(out);
    }

    /**
     * Strict identifier guard for the cardinality-probe path.
     * Postgres allows quoted identifiers with virtually any character,
     * but we reject anything outside [A-Za-z0-9_$.] to keep SQL
     * splicing safe under a defensive interpretation.  A leading dot
     * is also rejected.  This is intentionally narrower than what
     * Postgres permits; if a user's schema legitimately contains
     * spaces or unicode they'll need to migrate the table or extend
     * this guard.
     */
    private static final java.util.regex.Pattern VALID_IDENT =
            java.util.regex.Pattern.compile("[A-Za-z_][A-Za-z0-9_$]*");

    private static boolean isSafeIdentifier(String s) {
        return s != null && !s.isEmpty() && VALID_IDENT.matcher(s).matches();
    }

    private static String quoteIdent(String s) {
        // Even though the regex above forbids embedded double quotes,
        // double them on output anyway — defence in depth.
        return "\"" + s.replace("\"", "\"\"") + "\"";
    }

    /**
     * Validates the supplied {@link ConnectionConfig} by running a
     * short-lived probe.
     */
    public void testConnection(ConnectionConfig config) {
        validateRequired(config, "connection");
        validateRequired(config.type(), "connection.type");
        if (config.type() != DatabaseType.POSTGRES) {
            throw new ExtractionException(
                    "UNSUPPORTED_SOURCE",
                    "Only postgres source type is supported in this build",
                    false,
                    null);
        }
        try {
            HikariDataSource ds = pools.poolFor(config);
            extractor.testConnection(ds);
        } catch (ExtractionException ee) {
            throw ee;
        } catch (Exception e) {
            throw new ExtractionException(
                    "CONNECTION_FAILED", e.getMessage(), true, e);
        }
    }

    private static void validateRequired(Object v, String field) {
        if (v == null) {
            throw new ExtractionException(
                    "BAD_REQUEST", "Missing required field: " + field, false, null);
        }
        if (v instanceof String s && s.isBlank()) {
            throw new ExtractionException(
                    "BAD_REQUEST", "Empty required field: " + field, false, null);
        }
    }

    /**
     * Bridges checked exceptions through the bulkhead's Callable contract.
     */
    private static final class RuntimeExecutionException extends RuntimeException {
        RuntimeExecutionException(Throwable cause) {
            super(cause == null ? null : cause.getMessage(), cause);
        }
    }
}
