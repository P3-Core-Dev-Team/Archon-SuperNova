package com.discovery.extraction;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.nio.file.Path;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Verifies the {@code SecurityFilterChain} contract:
 * <ul>
 *     <li>{@code /actuator/health} is open</li>
 *     <li>{@code /api/v1/extract} requires a valid bearer token</li>
 *     <li>An invalid token returns 401</li>
 * </ul>
 */
@SpringBootTest
@AutoConfigureMockMvc
class SecurityFilterChainTest {

    @TempDir
    static Path tempDir;

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("extraction.storage.type", () -> "local");
        registry.add("extraction.storage.local.base-path", () -> tempDir.toString());
        registry.add("extraction.auth-disabled", () -> "false");
        registry.add("extraction.auth-token", () -> "secret-test-token");
    }

    @Test
    void actuatorHealthIsOpen() throws Exception {
        mvc.perform(get("/actuator/health"))
                .andExpect(status().isOk());
    }

    @Test
    void extractWithoutTokenIs401() throws Exception {
        mvc.perform(post("/api/v1/extract")
                        .contentType("application/json")
                        .content("{}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void extractWithBadTokenIs401() throws Exception {
        mvc.perform(post("/api/v1/extract")
                        .header("Authorization", "Bearer wrong")
                        .contentType("application/json")
                        .content("{}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void prometheusRequiresAuth() throws Exception {
        mvc.perform(get("/actuator/prometheus"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void extractWithValidTokenPassesAuth() throws Exception {
        // The body is empty / malformed so we expect a 4xx from the
        // controller layer (specifically a BAD_REQUEST). The point is that
        // the bearer token cleared BOTH the filter check AND Spring
        // Security's authorizationFilter - asserting `!= 401` is too weak
        // because a 403 would also pass that yet still indicate broken
        // SecurityContext propagation.
        var result = mvc.perform(post("/api/v1/extract")
                        .header("Authorization", "Bearer secret-test-token")
                        .contentType("application/json")
                        .content("{}"))
                .andReturn();
        int status = result.getResponse().getStatus();
        org.assertj.core.api.Assertions.assertThat(status)
                .as("Valid token must reach the controller layer (200/400 expected); got %d", status)
                .isIn(200, 400);
    }
}
