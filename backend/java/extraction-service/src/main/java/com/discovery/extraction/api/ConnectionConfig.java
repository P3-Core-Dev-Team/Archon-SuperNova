package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Objects;
import java.util.TreeMap;

/**
 * Wire model for source-database connection details.
 *
 * <p>Credentials are never held here as plaintext - callers pass a
 * {@code passwordSecretRef} string of the form {@code env://VAR_NAME} or
 * {@code vault://path/to/secret} which the service resolves through
 * {@link com.discovery.extraction.core.SecretResolver}. Plain strings are
 * rejected with {@code IllegalArgumentException}.
 *
 * <p>Aligned with {@code openapi/extraction-service-v1.yaml} v1.1.0: there
 * is no {@code jdbc_url} or {@code additional_jdbc_properties}; the JDBC
 * URL is constructed safely from {@code host}/{@code port}/{@code database}
 * via {@link java.util.Properties} (no string concatenation of user input).
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = true)
public record ConnectionConfig(
        @JsonProperty("type") DatabaseType type,
        @JsonProperty("host") String host,
        @JsonProperty("port") Integer port,
        @JsonProperty("database") String database,
        @JsonProperty("user") String user,
        @JsonProperty("password_secret_ref") String passwordSecretRef,
        @JsonProperty("ssl_mode") SslMode sslMode,
        @JsonProperty("application_name") String applicationName
) {

    @JsonCreator
    public ConnectionConfig {
        if (sslMode == null) {
            sslMode = SslMode.REQUIRE;
        }
        if (applicationName == null || applicationName.isBlank()) {
            applicationName = "discovery-extractor";
        }
    }

    /**
     * Stable hash used as the {@code ConnectionPoolManager} key. Excludes the
     * password reference so two requests that share everything except
     * credential rotations still hit the same pool.
     */
    public String hash() {
        TreeMap<String, String> parts = new TreeMap<>();
        parts.put("type", type == null ? "" : type.wire());
        parts.put("host", Objects.toString(host, ""));
        parts.put("port", Objects.toString(port, ""));
        parts.put("database", Objects.toString(database, ""));
        parts.put("user", Objects.toString(user, ""));
        parts.put("ssl_mode", sslMode == null ? "" : sslMode.wire());
        parts.put("application_name", Objects.toString(applicationName, ""));
        StringBuilder sb = new StringBuilder();
        parts.forEach((k, v) -> sb.append(k).append('=').append(v).append('\n'));
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(sb.toString().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    /**
     * Host:port/database - retained for log/metric tagging; with one global
     * bulkhead this is no longer used as a throttle key.
     */
    public String throttleKey() {
        return (host == null ? "?" : host)
                + ":" + (port == null ? "?" : port)
                + "/" + (database == null ? "?" : database);
    }
}
