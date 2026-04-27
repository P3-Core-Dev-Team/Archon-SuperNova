package com.discovery.extraction.core;

import com.discovery.extraction.api.ConnectionConfig;
import com.discovery.extraction.api.DatabaseType;
import com.discovery.extraction.api.SslMode;
import com.discovery.extraction.config.ApplicationProperties;
import com.discovery.extraction.exception.ExtractionException;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.util.Objects;
import java.util.Properties;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Lazily-initialized per-connection HikariCP pools keyed by
 * {@link ConnectionConfig#hash()}. Idle pools (no borrow in the configured
 * eviction window) are closed by a scheduled sweep.
 *
 * <p>The base JDBC URL is built only from {@code host}, {@code port} and
 * {@code database} (path-only - never with query parameters constructed from
 * user input). All other Postgres connection options - {@code sslmode},
 * {@code applicationName}, the user/password - go through Hikari's
 * {@code dataSourceProperties} which the driver loads via
 * {@link java.util.Properties}, eliminating the JDBC-URL injection vector.
 */
@Component
public class ConnectionPoolManager {

    private static final Logger log = LoggerFactory.getLogger(ConnectionPoolManager.class);

    private final ConcurrentHashMap<String, PoolEntry> pools = new ConcurrentHashMap<>();
    private final ApplicationProperties props;
    private final SecretResolver secretResolver;

    public ConnectionPoolManager(ApplicationProperties props, SecretResolver secretResolver) {
        this.props = props;
        this.secretResolver = secretResolver;
    }

    /**
     * Returns the pooled {@link HikariDataSource} for {@code config}.
     * Creates the pool if needed. Thread-safe.
     */
    public HikariDataSource poolFor(ConnectionConfig config) {
        Objects.requireNonNull(config, "connectionConfig");
        String key = config.hash();
        PoolEntry entry = pools.compute(key, (k, existing) -> {
            if (existing == null) {
                return new PoolEntry(build(config), Instant.now());
            }
            existing.touch();
            return existing;
        });
        return entry.dataSource;
    }

    private HikariDataSource build(ConnectionConfig config) {
        if (config.type() == null) {
            throw new ExtractionException("Connection config missing type");
        }
        if (config.type() != DatabaseType.POSTGRES) {
            throw new ExtractionException(
                    "UNSUPPORTED_SOURCE",
                    "Only postgres source type is supported in this build",
                    false,
                    null);
        }

        HikariConfig hc = new HikariConfig();
        // Path-only JDBC URL: only literal hostname/port/database go in the
        // URL, all parameter-style options are pushed through Properties.
        hc.setJdbcUrl(jdbcBaseUrl(config));

        // Driver/connection properties: user, password, sslmode,
        // ApplicationName, etc. The Postgres driver merges these with the
        // URL, but - critically - they cannot inject extra '?key=value'
        // pairs because they aren't string-spliced into the URL.
        Properties dsProps = new Properties();
        if (config.user() != null) {
            dsProps.setProperty("user", config.user());
            hc.setUsername(config.user());
        }
        String password = secretResolver.resolve(config.passwordSecretRef());
        if (password != null) {
            dsProps.setProperty("password", password);
            hc.setPassword(password);
        }
        SslMode ssl = config.sslMode() == null ? SslMode.REQUIRE : config.sslMode();
        dsProps.setProperty("sslmode", ssl.wire());
        dsProps.setProperty("ApplicationName",
                config.applicationName() == null ? "discovery-extractor" : config.applicationName());
        hc.setDataSourceProperties(dsProps);

        ApplicationProperties.PoolDefaults d = props.poolDefaults();
        hc.setMaximumPoolSize(d.maxSize());
        hc.setConnectionTimeout(d.connectionTimeoutMs());
        hc.setIdleTimeout(d.idleTimeoutMs());
        hc.setMaxLifetime(d.maxLifetimeMs());
        hc.setPoolName("extraction-" + shortHash(config.hash()));
        hc.setAutoCommit(false);
        hc.setConnectionTestQuery("SELECT 1");

        log.info("Creating HikariCP pool name={} host={} db={} maxSize={}",
                hc.getPoolName(), config.host(), config.database(), d.maxSize());
        return new HikariDataSource(hc);
    }

    private String jdbcBaseUrl(ConnectionConfig config) {
        // Validate that host/port/database don't contain characters that
        // would let them escape the URL path component. Hosts and database
        // names that pass these checks are safe to embed directly.
        String host = config.host();
        if (host == null || host.isBlank()) {
            throw new ExtractionException(
                    "BAD_REQUEST", "connection.host is required", false, null);
        }
        if (host.indexOf('/') >= 0 || host.indexOf('?') >= 0
                || host.indexOf('#') >= 0 || host.indexOf('@') >= 0
                || host.indexOf('&') >= 0 || host.indexOf(':') >= 0) {
            throw new ExtractionException(
                    "BAD_REQUEST", "connection.host contains forbidden characters", false, null);
        }
        int port = config.port() == null ? 5432 : config.port();
        if (port <= 0 || port > 65535) {
            throw new ExtractionException(
                    "BAD_REQUEST", "connection.port is out of range", false, null);
        }
        String db = config.database();
        if (db == null || db.isBlank()) {
            throw new ExtractionException(
                    "BAD_REQUEST", "connection.database is required", false, null);
        }
        if (db.indexOf('/') >= 0 || db.indexOf('?') >= 0
                || db.indexOf('#') >= 0 || db.indexOf('&') >= 0) {
            throw new ExtractionException(
                    "BAD_REQUEST", "connection.database contains forbidden characters", false, null);
        }
        return "jdbc:postgresql://" + host + ":" + port + "/" + db;
    }

    private static String shortHash(String fullHash) {
        if (fullHash == null || fullHash.length() < 8) {
            return fullHash;
        }
        return fullHash.substring(0, 8);
    }

    /**
     * Proactive sweep: close pools whose {@code lastTouched} exceeds the
     * configured idle-eviction interval (default 30 min). Sweep cadence is
     * 5 minutes by default (overridable via
     * {@code extraction.pool-defaults.sweep-interval-minutes}).
     */
    @Scheduled(fixedRateString = "${extraction.pool-defaults.sweep-interval-minutes:5}",
            timeUnit = java.util.concurrent.TimeUnit.MINUTES)
    public void evictIdlePools() {
        Instant cutoff = Instant.now().minus(
                Duration.ofMinutes(props.poolDefaults().idleEvictionMinutes()));
        pools.entrySet().removeIf(entry -> {
            PoolEntry p = entry.getValue();
            if (p.lastTouched.isBefore(cutoff)) {
                log.info("Evicting idle pool key={}", shortHash(entry.getKey()));
                try {
                    p.dataSource.close();
                } catch (Exception e) {
                    log.warn("Failed to close idle pool: {}", e.getMessage());
                }
                return true;
            }
            return false;
        });
    }

    @PreDestroy
    public void shutdown() {
        log.info("Closing {} connection pool(s)", pools.size());
        for (PoolEntry entry : pools.values()) {
            try {
                entry.dataSource.close();
            } catch (Exception e) {
                log.warn("Pool close failed: {}", e.getMessage());
            }
        }
        pools.clear();
    }

    public int poolCount() {
        return pools.size();
    }

    /**
     * Used only by tests that want to manually evict / inspect pools.
     */
    public boolean closePool(ConnectionConfig cfg) {
        String key = cfg.hash();
        PoolEntry entry = pools.remove(key);
        if (entry == null) {
            return false;
        }
        try {
            entry.dataSource.close();
        } catch (Exception ignore) {
            // best effort
        }
        return true;
    }

    /**
     * Minimal self-check against {@code config} - acquires a connection,
     * closes it. Surfaces underlying {@link SQLException} via the returned
     * boolean / caught throwable.
     */
    public void selfTest(ConnectionConfig config) throws SQLException {
        HikariDataSource ds = poolFor(config);
        try (var c = ds.getConnection()) {
            if (!c.isValid(5)) {
                throw new SQLException("Connection invalid after acquisition");
            }
        }
    }

    private static final class PoolEntry {
        final HikariDataSource dataSource;
        volatile Instant lastTouched;

        PoolEntry(HikariDataSource dataSource, Instant lastTouched) {
            this.dataSource = dataSource;
            this.lastTouched = lastTouched;
        }

        void touch() {
            this.lastTouched = Instant.now();
        }
    }
}
