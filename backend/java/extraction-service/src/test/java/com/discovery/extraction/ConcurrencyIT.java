package com.discovery.extraction;

import com.discovery.extraction.api.ConnectionConfig;
import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.ExtractionOptions;
import com.discovery.extraction.api.ExtractionRequest;
import com.discovery.extraction.api.ExtractionResponse;
import com.discovery.extraction.api.OutputConfig;
import com.discovery.extraction.api.SslMode;
import com.discovery.extraction.core.SecretResolver;
import com.discovery.extraction.metrics.ExtractionMetrics;
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

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * Fires 50 simultaneous extraction requests and verifies that no more than
 * 8 are inside the global source-extraction bulkhead at once, matching the
 * configured {@code maxConcurrentCalls=8} cap.
 *
 * <p>Observation is done via the custom
 * {@link ExtractionMetrics#currentConcurrent(String)} gauge wrapper.
 */
@SpringBootTest
@AutoConfigureMockMvc
@DirtiesContext
@Testcontainers
@Import(ConcurrencyIT.SecretResolverOverride.class)
class ConcurrencyIT {

    private static final int CALLERS = 50;
    private static final int MAX_ALLOWED = 8;

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("d")
            .withUsername("postgres")
            .withPassword("secret");

    @TempDir
    static Path tempDir;

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;
    @Autowired ExtractionMetrics metrics;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("extraction.storage.type", () -> "local");
        registry.add("extraction.storage.local.base-path", () -> tempDir.toString());
        registry.add("extraction.auth-disabled", () -> "true");
        registry.add("extraction.source-throttle.max-concurrent", () -> MAX_ALLOWED);
    }

    @BeforeAll
    static void seed() throws Exception {
        postgres.start();
        try (var c = postgres.createConnection("");
             var s = c.createStatement()) {
            s.execute("CREATE TABLE pebbles (id INT PRIMARY KEY, name TEXT)");
            StringBuilder vals = new StringBuilder("INSERT INTO pebbles VALUES ");
            for (int i = 0; i < 200; i++) {
                if (i > 0) {
                    vals.append(',');
                }
                vals.append("(").append(i).append(",'name-").append(i).append("')");
            }
            s.execute(vals.toString());
        }
    }

    private ConnectionConfig cc() {
        return new ConnectionConfig(
                DatabaseType.POSTGRES,
                postgres.getHost(), postgres.getMappedPort(5432),
                postgres.getDatabaseName(), postgres.getUsername(),
                "env://PG_PASS", SslMode.DISABLE, "ext-concurrency");
    }

    @Test
    void at_most_eight_hit_source_at_once() throws Exception {
        AtomicInteger peak = new AtomicInteger(0);
        Thread watcher = new Thread(() -> {
            String key = postgres.getHost() + ":" + postgres.getMappedPort(5432) + "/"
                    + postgres.getDatabaseName();
            while (!Thread.currentThread().isInterrupted()) {
                int now = metrics.currentConcurrent(key);
                peak.accumulateAndGet(now, Math::max);
                try {
                    Thread.sleep(10);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "concurrency-watcher");
        watcher.setDaemon(true);
        watcher.start();

        ExecutorService pool = Executors.newFixedThreadPool(CALLERS);
        CountDownLatch done = new CountDownLatch(CALLERS);
        List<Throwable> failures = new ArrayList<>();

        try {
            for (int i = 0; i < CALLERS; i++) {
                final int idx = i;
                pool.submit(() -> {
                    try {
                        ExtractionRequest req = new ExtractionRequest(
                                cc(),
                                "SELECT * FROM pebbles",
                                new OutputConfig(
                                        tempDir.resolve("conc-" + idx + ".parquet").toString(),
                                        "zstd", 3, 100_000, 1_048_576),
                                new ExtractionOptions(1000, 60, null,
                                        "conc=" + idx + ",id=" + UUID.randomUUID()));
                        String body = mapper.writeValueAsString(req);
                        var res = mvc.perform(post("/api/v1/extract")
                                        .contentType(MediaType.APPLICATION_JSON)
                                        .content(body))
                                .andReturn();
                        int code = res.getResponse().getStatus();
                        // 429 (Too Many Requests) is the throttle-rejected
                        // path now that the OpenAPI contract is aligned.
                        assertThat(code).isIn(200, 429);
                        if (code == 200) {
                            ExtractionResponse parsed = mapper.readValue(
                                    res.getResponse().getContentAsString(),
                                    ExtractionResponse.class);
                            assertThat(parsed.manifest().files().get(0).rows()).isEqualTo(200L);
                        }
                    } catch (Throwable t) {
                        synchronized (failures) {
                            failures.add(t);
                        }
                    } finally {
                        done.countDown();
                    }
                });
            }
            assertThat(done.await(5, TimeUnit.MINUTES)).isTrue();
        } finally {
            watcher.interrupt();
            pool.shutdownNow();
        }

        assertThat(failures).isEmpty();
        assertThat(peak.get()).isLessThanOrEqualTo(MAX_ALLOWED);
        assertThat(peak.get()).isGreaterThan(0);
    }

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
