package com.discovery.extraction.core;

import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.ExtractionRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.UUID;

/**
 * Writes one structured event per audited extraction lifecycle transition to
 * the dedicated {@code com.discovery.extraction.audit} logger.
 *
 * <p><strong>Never logs the raw query.</strong> A SHA-256 hash and the query
 * length are emitted instead so query identity can be correlated across
 * deployments without leaking column names or filter literals to log
 * aggregation systems.
 *
 * <p>Uses the SLF4J 2.x fluent API ({@link Logger#atInfo()}) so structured
 * fields are exposed as {@code KeyValuePair}s on each {@code ILoggingEvent},
 * which the LogstashEncoder serialises to first-class JSON properties.
 *
 * <p>Four event names are emitted, forming a complete audit trail:
 * <ul>
 *     <li>{@code extract_accepted} - request body parsed, query passed the
 *         whitelist; bulkhead admission has not yet happened.</li>
 *     <li>{@code extract_rejected} - request denied before any extraction
 *         work began (whitelist violation, bearer-token failure, bulkhead
 *         full). Includes a free-form {@code rejection_reason}.</li>
 *     <li>{@code extract_completed} - extraction finished successfully and
 *         a manifest was produced.</li>
 *     <li>{@code extract_failed} - extraction was admitted but threw a
 *         non-validation error during execution.</li>
 * </ul>
 *
 * <p>The filter-time auth-rejection path has only the {@code requestId},
 * {@code rejectionReason}, and an empty placeholder for query/source fields
 * because the request body has not been parsed yet. Bearer token values are
 * never included.
 */
@Component
public class AuditLogger {

    private static final Logger AUDIT =
            LoggerFactory.getLogger("com.discovery.extraction.audit");

    /**
     * Emits one structured "extract_accepted" event. Should be called AFTER
     * the whitelist passes but BEFORE bulkhead admission so the event semantically
     * means "we plan to run this".
     */
    public void recordAccepted(UUID requestId, ExtractionRequest request) {
        String tag = request != null && request.options() != null
                ? request.options().tag()
                : null;
        String query = request == null ? "" : (request.query() == null ? "" : request.query());
        DatabaseType type = request != null && request.connection() != null
                ? request.connection().type()
                : null;
        String outputPath = request != null && request.output() != null
                ? request.output().path()
                : null;

        AUDIT.atInfo()
                .setMessage("extract_accepted")
                .addKeyValue("event", "extract_accepted")
                .addKeyValue("request_id", requestId == null ? "" : requestId.toString())
                .addKeyValue("caller_tag", tag == null ? "" : tag)
                .addKeyValue("source_type", type == null ? "" : type.wire())
                .addKeyValue("query_hash", sha256Hex(query))
                .addKeyValue("query_length", query.length())
                .addKeyValue("output_path", outputPath == null ? "" : outputPath)
                .log();
    }

    /**
     * Emits one structured "extract_rejected" event. Used by the whitelist
     * path, the bearer-token filter, and the bulkhead-full path.
     *
     * <p>For auth rejections the body has not yet been parsed; pass
     * {@code null}/{@code 0}/empty placeholders for fields that are unknown.
     * Never include the bearer token or any portion of it.
     */
    public void recordRejected(UUID requestId,
                               String callerTag,
                               String sourceType,
                               String queryHash,
                               int queryLength,
                               String rejectionReason) {
        AUDIT.atInfo()
                .setMessage("extract_rejected")
                .addKeyValue("event", "extract_rejected")
                .addKeyValue("request_id", requestId == null ? "" : requestId.toString())
                .addKeyValue("caller_tag", callerTag == null ? "" : callerTag)
                .addKeyValue("source_type", sourceType == null ? "" : sourceType)
                .addKeyValue("query_hash", queryHash == null ? "" : queryHash)
                .addKeyValue("query_length", queryLength)
                .addKeyValue("rejection_reason", rejectionReason == null ? "" : rejectionReason)
                .log();
    }

    /**
     * Emits one structured "extract_completed" event after a successful
     * extraction. The {@code outputPath}, {@code rowsExtracted},
     * {@code durationMs} and {@code statusCode} let an auditor reconcile what
     * actually got written against what was accepted.
     */
    public void recordCompleted(UUID requestId,
                                String callerTag,
                                String sourceType,
                                String queryHash,
                                int queryLength,
                                String outputPath,
                                long rowsExtracted,
                                long durationMs,
                                int statusCode) {
        AUDIT.atInfo()
                .setMessage("extract_completed")
                .addKeyValue("event", "extract_completed")
                .addKeyValue("request_id", requestId == null ? "" : requestId.toString())
                .addKeyValue("caller_tag", callerTag == null ? "" : callerTag)
                .addKeyValue("source_type", sourceType == null ? "" : sourceType)
                .addKeyValue("query_hash", queryHash == null ? "" : queryHash)
                .addKeyValue("query_length", queryLength)
                .addKeyValue("output_path", outputPath == null ? "" : outputPath)
                .addKeyValue("rows_extracted", rowsExtracted)
                .addKeyValue("duration_ms", durationMs)
                .addKeyValue("status_code", statusCode)
                .log();
    }

    /**
     * Emits one structured "extract_failed" event when an extraction that was
     * already admitted (past the whitelist + accepted event) throws during
     * execution. Whitelist rejections (QUERY_NOT_ALLOWED) are audited as
     * {@code extract_rejected} instead. Body-shape validation errors
     * (BAD_REQUEST: missing/blank required fields) never touched the
     * whitelist or the bulkhead and are deliberately not part of the
     * accepted/rejected/completed/failed audit trail; they are logged at the
     * controller / global-exception-handler level only.
     */
    public void recordFailed(UUID requestId,
                             String callerTag,
                             String sourceType,
                             String queryHash,
                             int queryLength,
                             String errorCode,
                             String errorMessage) {
        AUDIT.atInfo()
                .setMessage("extract_failed")
                .addKeyValue("event", "extract_failed")
                .addKeyValue("request_id", requestId == null ? "" : requestId.toString())
                .addKeyValue("caller_tag", callerTag == null ? "" : callerTag)
                .addKeyValue("source_type", sourceType == null ? "" : sourceType)
                .addKeyValue("query_hash", queryHash == null ? "" : queryHash)
                .addKeyValue("query_length", queryLength)
                .addKeyValue("error_code", errorCode == null ? "" : errorCode)
                .addKeyValue("error_message", errorMessage == null ? "" : errorMessage)
                .log();
    }

    static String sha256Hex(String value) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
