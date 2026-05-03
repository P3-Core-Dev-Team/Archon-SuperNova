package com.archon.openmetadata;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.scheduling.annotation.EnableAsync;

import org.springframework.context.annotation.Bean;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import org.springframework.web.filter.CorsFilter;

@SpringBootApplication
@EnableJpaAuditing
@EnableAsync
public class ArchonOpenMetadataApplication {
    public static void main(String[] args) {
        SpringApplication.run(ArchonOpenMetadataApplication.class, args);
    }

    // Cors configuration moved to SecurityConfig
}
