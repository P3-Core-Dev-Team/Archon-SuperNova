package com.discovery.extraction;

import com.discovery.extraction.config.ApplicationProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Entry point for the Spring Boot Extraction Service.
 *
 * <p>Targets Java 17 LTS. The service is fully synchronous: every
 * {@code POST /api/v1/extract} call blocks the request thread until the
 * Parquet manifest is ready. Concurrency is bounded by Tomcat's platform
 * thread pool ({@code server.tomcat.threads.max}) and the global Resilience4j
 * Bulkhead ({@code source-extraction}, max 8 concurrent extractions).
 *
 * <p>Scheduling is enabled for the {@code ConnectionPoolManager}'s idle-pool
 * sweep.
 */
@SpringBootApplication
@EnableConfigurationProperties(ApplicationProperties.class)
@EnableScheduling
public class ExtractionApplication {

    public static void main(String[] args) {
        SpringApplication.run(ExtractionApplication.class, args);
    }
}
