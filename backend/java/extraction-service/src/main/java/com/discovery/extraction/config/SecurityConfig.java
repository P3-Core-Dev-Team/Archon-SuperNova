package com.discovery.extraction.config;

import com.discovery.extraction.core.AuditLogger;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.util.matcher.AntPathRequestMatcher;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;
import java.util.UUID;

/**
 * Bearer-token security wiring.
 *
 * <p>The default Spring Security HTTP-Basic chain - which would otherwise
 * be installed because {@code spring-boot-starter-security} is on the
 * classpath - is replaced with a stateless filter chain that:
 * <ul>
 *     <li>permits {@code /actuator/health}, {@code /actuator/info},
 *         Swagger UI and {@code /api-docs/**} unauthenticated;</li>
 *     <li>requires authentication on every other request;</li>
 *     <li>delegates the actual credential check to
 *         {@link BearerTokenFilter}, registered before
 *         {@link UsernamePasswordAuthenticationFilter}.</li>
 * </ul>
 *
 * <p>CSRF, formLogin, and httpBasic are explicitly disabled. The session
 * policy is {@code STATELESS} - we never establish a JSESSIONID.
 */
@Configuration
public class SecurityConfig {

    private static final Logger log = LoggerFactory.getLogger(SecurityConfig.class);

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http,
                                                   ApplicationProperties props,
                                                   AuditLogger auditLogger) throws Exception {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .formLogin(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(
                                new AntPathRequestMatcher("/actuator/health"),
                                new AntPathRequestMatcher("/actuator/health/**"),
                                new AntPathRequestMatcher("/actuator/info"),
                                new AntPathRequestMatcher("/actuator/info/**"),
                                new AntPathRequestMatcher("/swagger-ui/**"),
                                new AntPathRequestMatcher("/swagger-ui.html"),
                                new AntPathRequestMatcher("/api-docs/**"),
                                new AntPathRequestMatcher("/api-docs"))
                        .permitAll()
                        .anyRequest().authenticated())
                .addFilterBefore(new BearerTokenFilter(props, auditLogger),
                        UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    /**
     * Stateless bearer-token check. Enforced on every non-exempt request via
     * the {@link SecurityFilterChain} above. Constant-time string comparison
     * is used to avoid timing oracles.
     */
    public static class BearerTokenFilter extends OncePerRequestFilter {

        private final ApplicationProperties props;
        private final AuditLogger auditLogger;

        public BearerTokenFilter(ApplicationProperties props) {
            this(props, null);
        }

        public BearerTokenFilter(ApplicationProperties props, AuditLogger auditLogger) {
            this.props = props;
            this.auditLogger = auditLogger;
        }

        @Override
        protected void doFilterInternal(HttpServletRequest request,
                                        HttpServletResponse response,
                                        FilterChain chain) throws ServletException, IOException {
            String path = request.getRequestURI();
            if (isExempt(path)) {
                // Exempt paths skip both the token check and any
                // SecurityContext setup; the filter chain's permitAll() rule
                // for these matchers handles authorization.
                chain.doFilter(request, response);
                return;
            }
            if (props.authDisabled()) {
                // Auth disabled (dev / tests): grant a synthetic principal
                // so Spring Security's authorizationFilter (.authenticated())
                // sees an authenticated user rather than anonymous.
                grantSynthetic("auth-disabled");
                try {
                    chain.doFilter(request, response);
                } finally {
                    SecurityContextHolder.clearContext();
                }
                return;
            }
            String header = request.getHeader("Authorization");
            if (header == null || !header.regionMatches(true, 0, "Bearer ", 0, 7)) {
                auditAuthRejection("auth:missing-bearer-token");
                unauthorized(response, "missing bearer token");
                return;
            }
            String presented = header.substring(7).trim();
            String expected = props.authToken();
            if (expected == null || expected.isBlank() || !constantTimeEquals(presented, expected)) {
                auditAuthRejection("auth:invalid-bearer-token");
                unauthorized(response, "invalid bearer token");
                return;
            }
            grantSynthetic("bearer-client");
            try {
                chain.doFilter(request, response);
            } finally {
                SecurityContextHolder.clearContext();
            }
        }

        /**
         * Emits an {@code extract_rejected} audit event for a failed bearer
         * token check. The request body has not been parsed at this point,
         * so caller-tag/source-type/query-hash fields are left empty. The
         * bearer token value is never included.
         */
        private void auditAuthRejection(String reason) {
            if (auditLogger == null) {
                return;
            }
            auditLogger.recordRejected(UUID.randomUUID(), null, null, null, 0, reason);
        }

        private static void grantSynthetic(String principal) {
            UsernamePasswordAuthenticationToken auth = new UsernamePasswordAuthenticationToken(
                    principal, null,
                    List.of(new SimpleGrantedAuthority("ROLE_CLIENT")));
            SecurityContext ctx = SecurityContextHolder.createEmptyContext();
            ctx.setAuthentication(auth);
            SecurityContextHolder.setContext(ctx);
        }

        private boolean isExempt(String path) {
            if (path == null) {
                return false;
            }
            return path.equals("/actuator/health")
                    || path.startsWith("/actuator/health/")
                    || path.equals("/actuator/info")
                    || path.startsWith("/actuator/info/")
                    || path.startsWith("/swagger-ui")
                    || path.startsWith("/api-docs");
        }

        private void unauthorized(HttpServletResponse response, String reason) throws IOException {
            log.debug("Rejecting unauthenticated request: {}", reason);
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType("application/json");
            response.getWriter().write(
                    "{\"code\":\"UNAUTHORIZED\",\"message\":\"" + reason + "\"}");
        }

        static boolean constantTimeEquals(String a, String b) {
            if (a == null || b == null || a.length() != b.length()) {
                return false;
            }
            int diff = 0;
            for (int i = 0; i < a.length(); i++) {
                diff |= a.charAt(i) ^ b.charAt(i);
            }
            return diff == 0;
        }
    }
}
