package com.discovery.extraction;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.discovery.extraction.config.ApplicationProperties;
import com.discovery.extraction.config.SecurityConfig;
import com.discovery.extraction.core.AuditLogger;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.slf4j.event.KeyValuePair;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * Direct unit test of the {@link SecurityConfig.BearerTokenFilter} logic:
 * exempt paths, missing/wrong tokens, and the constant-time comparison.
 */
class BearerTokenFilterTest {

    private final ApplicationProperties props = new ApplicationProperties(
            new ApplicationProperties.Storage("local",
                    new ApplicationProperties.Storage.Local("/tmp")),
            "right-token", false,
            new ApplicationProperties.SourceThrottleProps(8, 10),
            new ApplicationProperties.PoolDefaults(10, 30000, 1800000, 3600000, 30),
            new ApplicationProperties.ParquetProps("zstd", 3, 100000, 1048576));

    private final AuditLogger auditLogger = new AuditLogger();

    private final SecurityConfig.BearerTokenFilter filter =
            new SecurityConfig.BearerTokenFilter(props, auditLogger);

    private Logger auditLogbackLogger;
    private ListAppender<ILoggingEvent> auditAppender;

    @BeforeEach
    void attachAuditAppender() {
        auditLogbackLogger = (Logger) LoggerFactory.getLogger("com.discovery.extraction.audit");
        auditAppender = new ListAppender<>();
        auditAppender.start();
        auditLogbackLogger.addAppender(auditAppender);
        auditLogbackLogger.setLevel(Level.INFO);
    }

    @AfterEach
    void detachAuditAppender() {
        if (auditLogbackLogger != null && auditAppender != null) {
            auditLogbackLogger.detachAppender(auditAppender);
        }
    }

    @Test
    void exemptPathIsLetThrough() throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        req.setRequestURI("/actuator/health");
        MockHttpServletResponse resp = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(req, resp, chain);
        verify(chain, times(1)).doFilter(any(HttpServletRequest.class),
                any(HttpServletResponse.class));
        assertThat(resp.getStatus()).isEqualTo(200);
    }

    @Test
    void missingHeaderReturns401() throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        req.setRequestURI("/api/v1/extract");
        MockHttpServletResponse resp = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(req, resp, chain);
        verify(chain, never()).doFilter(any(), any());
        assertThat(resp.getStatus()).isEqualTo(401);
        assertThat(resp.getContentAsString()).contains("missing bearer token");
    }

    @Test
    void wrongTokenReturns401() throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        req.setRequestURI("/api/v1/extract");
        req.addHeader("Authorization", "Bearer wrong-token");
        MockHttpServletResponse resp = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(req, resp, chain);
        verify(chain, never()).doFilter(any(), any());
        assertThat(resp.getStatus()).isEqualTo(401);
        assertThat(resp.getContentAsString()).contains("invalid bearer token");
    }

    @Test
    void rightTokenIsLetThrough() throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        req.setRequestURI("/api/v1/extract");
        req.addHeader("Authorization", "Bearer right-token");
        MockHttpServletResponse resp = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(req, resp, chain);
        verify(chain, times(1)).doFilter(any(HttpServletRequest.class),
                any(HttpServletResponse.class));
        assertThat(resp.getStatus()).isEqualTo(200);
    }

    @Test
    void missingHeaderEmitsAuditRejection() throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        req.setRequestURI("/api/v1/extract");
        MockHttpServletResponse resp = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(req, resp, chain);

        assertThat(auditAppender.list).isNotEmpty();
        ILoggingEvent ev = lastAuditEvent();
        Map<String, Object> kvs = asMap(ev.getKeyValuePairs());
        assertThat(ev.getFormattedMessage()).isEqualTo("extract_rejected");
        assertThat(kvs).containsEntry("event", "extract_rejected");
        assertThat(kvs).containsEntry("rejection_reason", "auth:missing-bearer-token");
        // Defence in depth: no bearer-token-shaped value in the event.
        for (Object v : kvs.values()) {
            assertThat(String.valueOf(v)).doesNotContain("Bearer ");
        }
    }

    @Test
    void wrongTokenEmitsAuditRejection() throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        req.setRequestURI("/api/v1/extract");
        req.addHeader("Authorization", "Bearer wrong-token");
        MockHttpServletResponse resp = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(req, resp, chain);

        ILoggingEvent ev = lastAuditEvent();
        Map<String, Object> kvs = asMap(ev.getKeyValuePairs());
        assertThat(kvs).containsEntry("event", "extract_rejected");
        assertThat(kvs).containsEntry("rejection_reason", "auth:invalid-bearer-token");
        // Defence in depth: the presented token must NOT appear in any KV.
        for (Object v : kvs.values()) {
            assertThat(String.valueOf(v)).doesNotContain("wrong-token");
        }
    }

    private ILoggingEvent lastAuditEvent() {
        return auditAppender.list.get(auditAppender.list.size() - 1);
    }

    private static Map<String, Object> asMap(List<KeyValuePair> pairs) {
        Map<String, Object> out = new HashMap<>();
        if (pairs != null) {
            for (KeyValuePair kv : pairs) {
                out.put(kv.key, kv.value);
            }
        }
        return out;
    }

    @Test
    void constantTimeEqualsSpotChecks() throws Exception {
        // Reach the package-private constantTimeEquals via reflection;
        // direct value tests for equal/not-equal pairs of the same length.
        Method m = SecurityConfig.BearerTokenFilter.class
                .getDeclaredMethod("constantTimeEquals", String.class, String.class);
        m.setAccessible(true);
        assertThat((boolean) m.invoke(null, "abc", "abc")).isTrue();
        assertThat((boolean) m.invoke(null, "abc", "abd")).isFalse();
        assertThat((boolean) m.invoke(null, "abc", "abcd")).isFalse();
        assertThat((boolean) m.invoke(null, "", "")).isTrue();
        assertThat((boolean) m.invoke(null, null, "abc")).isFalse();
        assertThat((boolean) m.invoke(null, "abc", null)).isFalse();
    }
}
