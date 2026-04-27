package com.discovery.extraction.core;

import com.discovery.extraction.config.ApplicationProperties;
import com.discovery.extraction.config.ResilienceConfig;
import com.discovery.extraction.exception.ExtractionException;
import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.Callable;

/**
 * Wraps a single global Resilience4j {@link Bulkhead} that caps concurrent
 * extractions across the whole monolith (default: 8).
 *
 * <p>The {@code throttleKey} parameter is retained on the public API so
 * gauges and structured logs can carry the host:port/db tag, but every call
 * goes through the same {@link ResilienceConfig#DEFAULT_BULKHEAD} instance.
 */
@Component
public class SourceThrottle {

    private static final Logger log = LoggerFactory.getLogger(SourceThrottle.class);

    private final Bulkhead globalBulkhead;
    private final BulkheadConfig globalConfig;

    public SourceThrottle(BulkheadRegistry registry, ApplicationProperties props) {
        this.globalConfig = BulkheadConfig.custom()
                .maxConcurrentCalls(props.sourceThrottle().maxConcurrent())
                .maxWaitDuration(Duration.ofMinutes(props.sourceThrottle().maxWaitMinutes()))
                .build();
        this.globalBulkhead = registry.bulkhead(ResilienceConfig.DEFAULT_BULKHEAD, globalConfig);
    }

    /**
     * Returns the global bulkhead. The {@code throttleKey} is accepted for
     * call-site symmetry with the metrics tagging but is ignored.
     */
    public Bulkhead bulkhead(String throttleKey) {
        return globalBulkhead;
    }

    public Bulkhead globalBulkhead() {
        return globalBulkhead;
    }

    /**
     * Acquires a permit, runs the callable, releases the permit. If the
     * bulkhead is full for longer than the configured wait, this throws
     * an {@link ExtractionException} with code {@code SOURCE_THROTTLED}
     * (mapped to HTTP 429 in {@code GlobalExceptionHandler}).
     */
    public <T> T call(String throttleKey, Callable<T> work) throws Exception {
        log.debug("Acquiring global bulkhead for throttleKey={} available={}",
                throttleKey, globalBulkhead.getMetrics().getAvailableConcurrentCalls());
        try {
            return globalBulkhead.executeCallable(work);
        } catch (BulkheadFullException e) {
            throw new ExtractionException(
                    "SOURCE_THROTTLED",
                    "Source throttle full (max concurrent="
                            + globalConfig.getMaxConcurrentCalls() + ")",
                    true,
                    e);
        }
    }

    /**
     * Returns the current number of permits available on the global
     * bulkhead. Useful for tests that verify the cap.
     */
    public int availablePermits() {
        return globalBulkhead.getMetrics().getAvailableConcurrentCalls();
    }
}
