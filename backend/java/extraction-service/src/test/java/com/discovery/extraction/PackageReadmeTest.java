package com.discovery.extraction;

import com.discovery.extraction.config.ApplicationProperties;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.context.ConfigurationPropertiesAutoConfiguration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Minimal sanity test that the application context wires
 * {@link ApplicationProperties} from {@code application.yml} without errors.
 * Does NOT boot the full web stack, so it is fast and deterministic.
 */
class PackageReadmeTest {

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(ConfigurationPropertiesAutoConfiguration.class))
            .withUserConfiguration(TestConfig.class)
            // The fail-fast invariant requires a non-blank token unless auth is disabled.
            .withPropertyValues("extraction.auth-token=test-token");

    @Test
    void propertiesBindWithDefaults() {
        runner.run(ctx -> {
            assertThat(ctx).hasNotFailed();
            ApplicationProperties props = ctx.getBean(ApplicationProperties.class);
            assertThat(props).isNotNull();
            assertThat(props.sourceThrottle().maxConcurrent()).isEqualTo(8);
            assertThat(props.poolDefaults().maxSize()).isEqualTo(10);
            assertThat(props.parquet().defaultCompression()).isEqualTo("zstd");
            assertThat(props.parquet().defaultCompressionLevel()).isEqualTo(3);
            assertThat(props.parquet().defaultRowGroupSize()).isEqualTo(100_000);
            assertThat(props.storage().type()).isEqualTo("local");
        });
    }

    @Test
    void propertiesAcceptOverrides() {
        runner.withPropertyValues(
                        "extraction.source-throttle.max-concurrent=16",
                        "extraction.parquet.default-compression-level=9",
                        "extraction.storage.local.base-path=/tmp/override")
                .run(ctx -> {
                    assertThat(ctx).hasNotFailed();
                    ApplicationProperties props = ctx.getBean(ApplicationProperties.class);
                    assertThat(props.sourceThrottle().maxConcurrent()).isEqualTo(16);
                    assertThat(props.parquet().defaultCompressionLevel()).isEqualTo(9);
                    assertThat(props.storage().local().basePath()).isEqualTo("/tmp/override");
                });
    }

    @Test
    void failsFastOnMissingTokenWhenAuthEnabled() {
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(ConfigurationPropertiesAutoConfiguration.class))
                .withUserConfiguration(TestConfig.class)
                .withPropertyValues("extraction.auth-token=", "extraction.auth-disabled=false")
                .run(ctx -> assertThat(ctx).hasFailed());
    }

    @Configuration
    @EnableConfigurationProperties(ApplicationProperties.class)
    static class TestConfig {
    }
}
