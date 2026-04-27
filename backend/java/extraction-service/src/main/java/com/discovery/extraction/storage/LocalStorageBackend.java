package com.discovery.extraction.storage;

import com.discovery.extraction.config.ApplicationProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;

/**
 * Writes Parquet files directly to a configured local base directory.
 *
 * <p>Keys that begin with {@code /} or a drive letter are treated as
 * absolute paths (bypassing {@code base_path}). Otherwise the key is joined
 * to {@code base_path}.
 *
 * <p>The S3 backend was removed when the service was monolithised; only
 * local filesystem output is supported. Run the service with a writable
 * volume and let an out-of-band copier (rclone, aws cli, etc.) ship the
 * Parquet files to long-term storage.
 */
@Component
public class LocalStorageBackend {

    private static final Logger log = LoggerFactory.getLogger(LocalStorageBackend.class);

    private final Path basePath;

    public LocalStorageBackend(ApplicationProperties props) {
        this.basePath = Paths.get(props.storage().local().basePath()).toAbsolutePath();
    }

    public Path resolveScratchPath(String key) throws IOException {
        Path target = resolveFinal(key);
        Path parent = target.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        return target;
    }

    public String upload(Path scratch, String key) throws IOException {
        Path target = resolveFinal(key);
        if (!scratch.equals(target)) {
            Path parent = target.getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Files.move(scratch, target, StandardCopyOption.REPLACE_EXISTING);
        }
        log.debug("LocalStorageBackend uploaded key={} -> {}", key, target);
        return target.toString();
    }

    public OutputStream openOutputStream(String key) throws IOException {
        Path target = resolveFinal(key);
        Path parent = target.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        return Files.newOutputStream(target);
    }

    public String type() {
        return "local";
    }

    private Path resolveFinal(String key) {
        if (key == null || key.isBlank()) {
            throw new IllegalArgumentException("Key is required");
        }
        Path p = Paths.get(key);
        if (p.isAbsolute()) {
            return p;
        }
        return basePath.resolve(key);
    }

    public Path basePath() {
        return basePath;
    }
}
