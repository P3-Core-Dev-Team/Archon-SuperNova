package com.discovery.extraction.exception;

import com.discovery.extraction.api.ErrorInfo;
import com.discovery.extraction.api.ExtractionResponse;
import com.fasterxml.jackson.core.JsonProcessingException;
import io.github.resilience4j.bulkhead.BulkheadFullException;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;

/**
 * Maps service-layer exceptions into HTTP responses using a consistent
 * {@link ExtractionResponse} + {@link ErrorInfo} envelope.
 *
 * <p>Bulkhead exhaustion (whether surfaced as
 * {@link BulkheadFullException} directly or wrapped in an
 * {@link ExtractionException} with code {@code SOURCE_THROTTLED}) is mapped
 * to HTTP 429 with a {@code Retry-After} header, matching the published
 * OpenAPI contract.
 */
@ControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    /** Suggested retry-after when the bulkhead is full (seconds). */
    private static final String DEFAULT_RETRY_AFTER_SECONDS = "30";

    @ExceptionHandler(QueryNotAllowedException.class)
    public ResponseEntity<ExtractionResponse> handleQueryNotAllowed(QueryNotAllowedException ex) {
        log.info("Query whitelist rejection: {}", ex.getMessage());
        ErrorInfo err = new ErrorInfo("QUERY_NOT_ALLOWED", ex.getMessage(), false);
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(new ExtractionResponse(null, null, null, err));
    }

    @ExceptionHandler(ExtractionException.class)
    public ResponseEntity<ExtractionResponse> handleExtraction(ExtractionException ex,
                                                               HttpServletRequest request) {
        String code = ex.getCode() == null ? "" : ex.getCode();
        if ("SOURCE_THROTTLED".equals(code)) {
            return tooManyRequests(ex.getCode(), ex.getMessage(), true);
        }
        HttpStatus status = switch (code) {
            case "QUERY_NOT_ALLOWED", "BAD_REQUEST" -> HttpStatus.BAD_REQUEST;
            case "CONNECTION_FAILED" -> HttpStatus.BAD_REQUEST;
            case "UNSUPPORTED_SOURCE" -> HttpStatus.NOT_IMPLEMENTED;
            case "NOT_FOUND" -> HttpStatus.NOT_FOUND;
            default -> HttpStatus.INTERNAL_SERVER_ERROR;
        };
        if (status.is5xxServerError()) {
            log.warn("ExtractionException {} on {}: {}", ex.getCode(),
                    request == null ? "?" : request.getRequestURI(), ex.getMessage(), ex);
        } else {
            log.info("ExtractionException {} on {}: {}", ex.getCode(),
                    request == null ? "?" : request.getRequestURI(), ex.getMessage());
        }
        ErrorInfo err = new ErrorInfo(ex.getCode(), ex.getMessage(), ex.isRetryable());
        return ResponseEntity.status(status)
                .body(new ExtractionResponse(null, null, null, err));
    }

    @ExceptionHandler(BulkheadFullException.class)
    public ResponseEntity<ExtractionResponse> handleBulkheadFull(BulkheadFullException ex) {
        log.warn("Bulkhead full: {}", ex.getMessage());
        return tooManyRequests("SOURCE_THROTTLED", ex.getMessage(), true);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ExtractionResponse> handleIllegalArgument(IllegalArgumentException ex) {
        log.info("Illegal argument: {}", ex.getMessage());
        ErrorInfo err = new ErrorInfo("BAD_REQUEST", ex.getMessage(), false);
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(new ExtractionResponse(null, null, null, err));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ExtractionResponse> handleMalformedJson(HttpMessageNotReadableException ex) {
        log.info("Malformed request body: {}", ex.getMessage());
        ErrorInfo err = new ErrorInfo("BAD_REQUEST",
                "Could not parse request body: " + rootMessage(ex), false);
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(new ExtractionResponse(null, null, null, err));
    }

    @ExceptionHandler(UnsupportedOperationException.class)
    public ResponseEntity<ExtractionResponse> handleUnsupported(UnsupportedOperationException ex) {
        log.info("Unsupported operation: {}", ex.getMessage());
        ErrorInfo err = new ErrorInfo("UNSUPPORTED_SOURCE", ex.getMessage(), false);
        return ResponseEntity.status(HttpStatus.NOT_IMPLEMENTED)
                .body(new ExtractionResponse(null, null, null, err));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ExtractionResponse> handleGeneric(Exception ex, HttpServletRequest request) {
        log.error("Unhandled exception on {}: {}", request == null ? "?" : request.getRequestURI(),
                ex.getMessage(), ex);
        ErrorInfo err = new ErrorInfo("INTERNAL_ERROR",
                ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage(),
                false);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ExtractionResponse(null, null, null, err));
    }

    private static ResponseEntity<ExtractionResponse> tooManyRequests(String code,
                                                                       String message,
                                                                       boolean retryable) {
        ErrorInfo err = new ErrorInfo(code, message, retryable);
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, DEFAULT_RETRY_AFTER_SECONDS)
                .body(new ExtractionResponse(null, null, null, err));
    }

    private static String rootMessage(Throwable t) {
        Throwable c = t;
        while (c.getCause() != null && c.getCause() != c) {
            c = c.getCause();
        }
        if (c instanceof JsonProcessingException jpe) {
            return jpe.getOriginalMessage();
        }
        return c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
    }
}
