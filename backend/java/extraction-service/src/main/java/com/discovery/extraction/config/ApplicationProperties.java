package com.discovery.extraction.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Strongly-typed wire-up from {@code application.yml} under {@code extraction.*}.
 *
 * <p>Using a Spring Boot 3 record-based {@code @ConfigurationProperties}.
 * Constructor binding is automatic - no explicit {@code @ConstructorBinding}
 * annotation required.
 *
 * <p><strong>Fail-fast invariant:</strong> if {@code authDisabled=false} the
 * service refuses to start unless {@code authToken} is set to a non-blank
 * value. This prevents production deploys from silently shipping with an
 * empty bearer token if the operator forgets to set
 * {@code EXTRACTION_SERVICE_TOKEN}.
 */
@ConfigurationProperties(prefix = "extraction")
public record ApplicationProperties(
        @DefaultValue Storage storage,
        @DefaultValue("") String authToken,
        @DefaultValue("false") boolean authDisabled,
        @DefaultValue SourceThrottleProps sourceThrottle,
        @DefaultValue PoolDefaults poolDefaults,
        @DefaultValue ParquetProps parquet
) {

    public ApplicationProperties {
        // Compact-constructor validation runs at bind time, so a misconfigured
        // production deploy (auth enabled but no token) fails fast at app
        // start instead of silently shipping with no credential check.
        if (!authDisabled && (authToken == null || authToken.isBlank())) {
            throw new IllegalStateException(
                    "extraction.auth-token is blank but auth is enabled. "
                            + "Set EXTRACTION_SERVICE_TOKEN (or extraction.auth-disabled=true "
                            + "for local-only dev profiles).");
        }
    }

    public record Storage(
            @DefaultValue("local") String type,
            @DefaultValue Local local
    ) {
        public record Local(
                @DefaultValue("/data/parquet") String basePath
        ) {
        }
    }

    public record SourceThrottleProps(
            @DefaultValue("8") int maxConcurrent,
            @DefaultValue("10") int maxWaitMinutes
    ) {
    }

    public record PoolDefaults(
            @DefaultValue("10") int maxSize,
            @DefaultValue("30000") long connectionTimeoutMs,
            @DefaultValue("1800000") long idleTimeoutMs,
            @DefaultValue("3600000") long maxLifetimeMs,
            @DefaultValue("30") int idleEvictionMinutes
    ) {
    }

    public record ParquetProps(
            @DefaultValue("zstd") String defaultCompression,
            @DefaultValue("3") int defaultCompressionLevel,
            @DefaultValue("100000") int defaultRowGroupSize,
            @DefaultValue("1048576") int defaultPageSize
    ) {
    }
}
