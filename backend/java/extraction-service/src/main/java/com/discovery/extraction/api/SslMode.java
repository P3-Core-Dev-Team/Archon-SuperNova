package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Supported {@code ssl_mode} values for {@link ConnectionConfig}. Mirrors the
 * Postgres libpq mode names so the JDBC URL is a direct passthrough.
 */
public enum SslMode {
    DISABLE("disable"),
    REQUIRE("require"),
    VERIFY_CA("verify-ca"),
    VERIFY_FULL("verify-full");

    private final String wire;

    SslMode(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static SslMode fromWire(String value) {
        if (value == null || value.isBlank()) {
            return REQUIRE;
        }
        String v = value.trim().toLowerCase();
        for (SslMode m : values()) {
            if (m.wire.equals(v)) {
                return m;
            }
        }
        throw new IllegalArgumentException("Unknown ssl_mode: " + value);
    }
}
