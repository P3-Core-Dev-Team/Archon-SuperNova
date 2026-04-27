package com.discovery.extraction;

import com.discovery.extraction.config.ApplicationProperties;
import com.discovery.extraction.core.SourceThrottle;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies that {@link SourceThrottle} caps concurrency at the configured
 * limit under real thread contention. The monolith uses a single global
 * bulkhead, so different throttle keys still hit the same cap.
 */
class SourceThrottleTest {

    @Test
    void capsConcurrencyAtMaxConcurrent() throws Exception {
        int maxConcurrent = 4;
        int callers = 16;
        ApplicationProperties props = defaultProps(maxConcurrent, 1);

        BulkheadRegistry registry = BulkheadRegistry.of(
                BulkheadConfig.custom()
                        .maxConcurrentCalls(maxConcurrent)
                        .maxWaitDuration(Duration.ofMinutes(1))
                        .build());
        SourceThrottle throttle = new SourceThrottle(registry, props);

        CountDownLatch holdOpen = new CountDownLatch(1);
        CountDownLatch startLine = new CountDownLatch(callers);
        AtomicInteger concurrent = new AtomicInteger(0);
        AtomicInteger peak = new AtomicInteger(0);

        ExecutorService pool = Executors.newFixedThreadPool(callers);
        try {
            String key = "host:1234/db";
            for (int i = 0; i < callers; i++) {
                pool.submit(() -> {
                    startLine.countDown();
                    throttle.call(key, () -> {
                        int now = concurrent.incrementAndGet();
                        peak.accumulateAndGet(now, Math::max);
                        holdOpen.await();
                        concurrent.decrementAndGet();
                        return null;
                    });
                    return null;
                });
            }
            // Wait until all callers have entered their runnables so the
            // bulkhead has had a chance to admit up to maxConcurrent.
            assertThat(startLine.await(5, TimeUnit.SECONDS)).isTrue();
            Thread.sleep(200);

            // The peak concurrent count should never exceed maxConcurrent.
            assertThat(peak.get()).isLessThanOrEqualTo(maxConcurrent);
            assertThat(concurrent.get()).isLessThanOrEqualTo(maxConcurrent);
        } finally {
            holdOpen.countDown();
            pool.shutdown();
            assertThat(pool.awaitTermination(10, TimeUnit.SECONDS)).isTrue();
        }
    }

    @Test
    void differentKeysShareTheGlobalBulkhead() {
        ApplicationProperties props = defaultProps(2, 1);
        BulkheadRegistry registry = BulkheadRegistry.of(
                BulkheadConfig.custom()
                        .maxConcurrentCalls(2)
                        .maxWaitDuration(Duration.ofSeconds(1))
                        .build());
        SourceThrottle throttle = new SourceThrottle(registry, props);

        // Two distinct throttle keys must resolve to the same bulkhead -
        // this is the defining property of the global-bulkhead refactor.
        assertThat(throttle.bulkhead("hostA:5432/db"))
                .isSameAs(throttle.bulkhead("hostB:5432/db"))
                .isSameAs(throttle.globalBulkhead());
    }

    private ApplicationProperties defaultProps(int maxConcurrent, int maxWaitMinutes) {
        return new ApplicationProperties(
                new ApplicationProperties.Storage("local",
                        new ApplicationProperties.Storage.Local("/tmp/parquet")),
                "test-token",
                true,
                new ApplicationProperties.SourceThrottleProps(maxConcurrent, maxWaitMinutes),
                new ApplicationProperties.PoolDefaults(10, 30000, 1800000, 3600000, 30),
                new ApplicationProperties.ParquetProps("zstd", 3, 100000, 1048576)
        );
    }
}
