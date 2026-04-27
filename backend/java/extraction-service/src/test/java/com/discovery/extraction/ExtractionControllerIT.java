package com.discovery.extraction;

import com.discovery.extraction.api.ConnectionConfig;
import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.ExtractionOptions;
import com.discovery.extraction.api.ExtractionRequest;
import com.discovery.extraction.api.OutputConfig;
import com.discovery.extraction.api.SslMode;
import com.discovery.extraction.core.SecretResolver;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * End-to-end test: HTTP POST -> extraction service -> Parquet written
 * to a temp dir. Also verifies the whitelist rejection path returns 400.
 *
 * <p>The async path (POST /extract/async, GET /extractions/{id}, cancel) was
 * removed in v1.1.0 - the monolith only exposes the synchronous endpoint.
 */
@SpringBootTest
@AutoConfigureMockMvc
@DirtiesContext
@Testcontainers
@Import(ExtractionControllerIT.SecretResolverOverride.class)
class ExtractionControllerIT {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("discovery")
            .withUsername("postgres")
            .withPassword("secret");

    @TempDir
    static Path tempDir;

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("extraction.storage.type", () -> "local");
        registry.add("extraction.storage.local.base-path", () -> tempDir.toString());
        registry.add("extraction.auth-disabled", () -> "true");
    }

    @BeforeAll
    static void seed() throws Exception {
        postgres.start();
        try (var c = postgres.createConnection("");
             var s = c.createStatement()) {
            s.execute("CREATE TABLE widgets (id INT PRIMARY KEY, name TEXT)");
            s.execute("INSERT INTO widgets VALUES (1,'a'),(2,'b'),(3,'c')");
        }
    }

    private ConnectionConfig connection() {
        return new ConnectionConfig(
                DatabaseType.POSTGRES,
                postgres.getHost(), postgres.getMappedPort(5432),
                postgres.getDatabaseName(), postgres.getUsername(),
                "env://PG_PASS", SslMode.DISABLE, "ext-ctrl-it");
    }

    @Test
    void syncExtractionWritesParquet() throws Exception {
        String outPath = tempDir.resolve("widgets-sync.parquet").toString();
        ExtractionRequest req = new ExtractionRequest(
                connection(),
                "SELECT * FROM widgets",
                new OutputConfig(outPath, "zstd", 3, 100_000, 1_048_576),
                new ExtractionOptions(1000, 60, null, "ctrl-it"));

        String body = mapper.writeValueAsString(req);
        mvc.perform(post("/api/v1/extract")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("completed"))
                .andExpect(jsonPath("$.manifest.files[0].rows").value(3));
        assertThat(Files.exists(Path.of(outPath))).isTrue();
    }

    @Test
    void forbiddenQueryReturns400() throws Exception {
        ExtractionRequest req = new ExtractionRequest(
                connection(),
                "SELECT COUNT(*) FROM widgets",
                new OutputConfig(tempDir.resolve("never.parquet").toString(),
                        "zstd", 3, 100_000, 1_048_576),
                new ExtractionOptions(1000, 60, null, null));

        mvc.perform(post("/api/v1/extract")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(mapper.writeValueAsString(req)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("QUERY_NOT_ALLOWED"));
    }

    /**
     * Overrides the production {@link SecretResolver} so the Postgres
     * Testcontainer's password can be looked up via {@code env://PG_PASS}
     * without actually setting a JVM env var. Plain-text passwords in the
     * request body are no longer accepted by the production resolver.
     */
    @TestConfiguration
    static class SecretResolverOverride {
        @Bean
        @Primary
        SecretResolver testSecretResolver() {
            return new SecretResolver(name -> switch (name) {
                case "PG_PASS" -> postgres.getPassword();
                default -> null;
            });
        }
    }
}
