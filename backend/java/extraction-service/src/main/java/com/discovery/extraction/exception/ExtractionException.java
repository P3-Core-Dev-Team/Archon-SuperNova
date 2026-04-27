package com.discovery.extraction.exception;

/**
 * Top-level checked-equivalent for any failure surfaced by the extraction
 * pipeline. Uses RuntimeException so it flows unimpeded through
 * virtual-thread work units.
 */
public class ExtractionException extends RuntimeException {

    private final String code;
    private final boolean retryable;

    public ExtractionException(String message) {
        this("EXTRACTION_ERROR", message, false, null);
    }

    public ExtractionException(String message, Throwable cause) {
        this("EXTRACTION_ERROR", message, false, cause);
    }

    public ExtractionException(String code, String message, boolean retryable, Throwable cause) {
        super(message, cause);
        this.code = code;
        this.retryable = retryable;
    }

    public String getCode() {
        return code;
    }

    public boolean isRetryable() {
        return retryable;
    }
}
