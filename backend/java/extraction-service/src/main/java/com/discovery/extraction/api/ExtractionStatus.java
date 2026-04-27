package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Lifecycle states reported on the wire. Sync-only monolith - there is no
 * queued or cancelled state. {@link #RUNNING} is reserved for transient
 * structured logging and is not currently emitted on a response body.
 */
public enum ExtractionStatus {
    RUNNING("running"),
    COMPLETED("completed"),
    FAILED("failed");

    private final String wire;

    ExtractionStatus(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static ExtractionStatus fromWire(String value) {
        if (value == null) {
            return null;
        }
        String v = value.trim().toLowerCase();
        for (ExtractionStatus s : values()) {
            if (s.wire.equals(v)) {
                return s;
            }
        }
        throw new IllegalArgumentException("Unknown extraction status: " + value);
    }

    public boolean isTerminal() {
        return this == COMPLETED || this == FAILED;
    }
}
