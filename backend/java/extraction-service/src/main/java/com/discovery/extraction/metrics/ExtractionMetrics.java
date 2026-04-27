package com.discovery.extraction.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.DistributionSummary;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Tags;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Thin facade over Micrometer for extraction-pipeline metrics.
 *
 * <p>Emitted meters:
 * <ul>
 *   <li>{@code extraction_rows_extracted_total} - counter, tagged with database.</li>
 *   <li>{@code extraction_bytes_written_total} - counter, tagged with database.</li>
 *   <li>{@code extraction_requests_total} - counter, tagged with status.</li>
 *   <li>{@code extraction_duration_seconds} - timer, tagged with status.</li>
 *   <li>{@code source_extraction_concurrent_calls} - gauge, tagged with throttle key.</li>
 * </ul>
 */
@Component
public class ExtractionMetrics {

    public static final String ROWS_EXTRACTED = "extraction_rows_extracted_total";
    public static final String BYTES_WRITTEN = "extraction_bytes_written_total";
    public static final String REQUESTS_TOTAL = "extraction_requests_total";
    public static final String DURATION = "extraction_duration_seconds";
    public static final String PARQUET_ROW_GROUPS = "extraction_parquet_row_groups";
    public static final String CONCURRENT_CALLS = "source_extraction_concurrent_calls";

    private final MeterRegistry registry;
    private final ConcurrentHashMap<String, AtomicInteger> concurrentBySource = new ConcurrentHashMap<>();

    public ExtractionMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    public void recordRowsExtracted(String database, long rows) {
        Counter.builder(ROWS_EXTRACTED)
                .tag("database", database == null ? "unknown" : database)
                .register(registry)
                .increment(rows);
    }

    public void recordBytesWritten(String database, long bytes) {
        Counter.builder(BYTES_WRITTEN)
                .tag("database", database == null ? "unknown" : database)
                .register(registry)
                .increment(bytes);
    }

    public void recordRequest(String status) {
        Counter.builder(REQUESTS_TOTAL)
                .tag("status", status == null ? "unknown" : status)
                .register(registry)
                .increment();
    }

    public void recordDuration(String status, Duration duration) {
        Timer.builder(DURATION)
                .tag("status", status == null ? "unknown" : status)
                .register(registry)
                .record(duration);
    }

    public void recordRowGroups(int groupCount) {
        DistributionSummary.builder(PARQUET_ROW_GROUPS)
                .register(registry)
                .record(groupCount);
    }

    /**
     * Increments the gauge for concurrent in-flight extractions against
     * {@code throttleKey}. Returns the new count.
     */
    public int onExtractionStart(String throttleKey) {
        AtomicInteger counter = concurrentBySource.computeIfAbsent(throttleKey, key -> {
            AtomicInteger ai = new AtomicInteger(0);
            registry.gauge(CONCURRENT_CALLS, Tags.of("throttle_key", key), ai);
            return ai;
        });
        return counter.incrementAndGet();
    }

    public int onExtractionEnd(String throttleKey) {
        AtomicInteger counter = concurrentBySource.get(throttleKey);
        return counter == null ? 0 : counter.decrementAndGet();
    }

    public int currentConcurrent(String throttleKey) {
        AtomicInteger counter = concurrentBySource.get(throttleKey);
        return counter == null ? 0 : counter.get();
    }

    public MeterRegistry registry() {
        return registry;
    }
}
