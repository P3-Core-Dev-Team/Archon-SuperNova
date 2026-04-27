package com.discovery.extraction.config;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

/**
 * Central Resilience4j wiring. Provides a registry-backed
 * {@link BulkheadRegistry} and the single global {@link Bulkhead} named
 * {@code source-extraction} (default: 8 concurrent calls, 10-minute max wait).
 *
 * <p>The monolith uses one global bulkhead - per-source keying is gone.
 * {@code SourceThrottle} resolves the same bean for every call.
 */
@Configuration
public class ResilienceConfig {

    public static final String DEFAULT_BULKHEAD = "source-extraction";

    @Bean
    public BulkheadConfig defaultBulkheadConfig(ApplicationProperties props) {
        return BulkheadConfig.custom()
                .maxConcurrentCalls(props.sourceThrottle().maxConcurrent())
                .maxWaitDuration(Duration.ofMinutes(props.sourceThrottle().maxWaitMinutes()))
                .build();
    }

    @Bean
    public BulkheadRegistry bulkheadRegistry(BulkheadConfig defaultBulkheadConfig) {
        return BulkheadRegistry.of(defaultBulkheadConfig);
    }

    @Bean(name = "sourceExtractionBulkhead")
    public Bulkhead sourceExtractionBulkhead(BulkheadRegistry registry) {
        return registry.bulkhead(DEFAULT_BULKHEAD);
    }
}
