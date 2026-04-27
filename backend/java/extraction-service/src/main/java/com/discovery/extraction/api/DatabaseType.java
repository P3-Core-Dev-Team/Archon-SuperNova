package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Source database type. The monolith only supports {@code postgres}; the
 * enum is retained so the wire protocol can grow other dialects in a future
 * version without breaking JSON contract clients.
 */
public enum DatabaseType {
    POSTGRES("postgres");

    private final String wire;

    DatabaseType(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static DatabaseType fromWire(String value) {
        if (value == null) {
            return null;
        }
        String v = value.trim().toLowerCase();
        for (DatabaseType t : values()) {
            if (t.wire.equals(v)) {
                return t;
            }
        }
        throw new IllegalArgumentException("Unknown database type: " + value);
    }
}
