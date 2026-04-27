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
