package com.discovery.extraction.core;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.function.Function;

/**
 * Resolves secret references of the form:
 *
 * <ul>
 *     <li>{@code env://VAR_NAME} - read from process environment</li>
 *     <li>{@code vault://path}   - explicitly not implemented; throws</li>
 * </ul>
 *
 * <p>Plain strings (no scheme) are <strong>rejected</strong>. The earlier
 * fallback that silently returned the raw value as a password is gone:
 * callers that pass a plaintext credential get an
 * {@link IllegalArgumentException}, ensuring credentials never travel in
 * extraction-request JSON.
 *
 * <p><strong>Never logs the resolved secret.</strong> Only the variable
 * identifier (or a redacted scheme tag) is ever recorded.
 */
@Component
public class SecretResolver {

    private static final Logger log = LoggerFactory.getLogger(SecretResolver.class);

    private static final String ENV_SCHEME = "env://";
    private static final String VAULT_SCHEME = "vault://";

    private final Function<String, String> envSource;

    public SecretResolver() {
        this(System::getenv);
    }

    /**
     * Test-visible constructor for injecting an alternate environment source.
     */
    public SecretResolver(Function<String, String> envSource) {
        this.envSource = envSource;
    }

    /**
     * Resolves {@code ref} to plaintext, or returns {@code null} if the
     * ref itself is null/blank (so callers can choose to omit the credential).
     */
    public String resolve(String ref) {
        if (ref == null || ref.isBlank()) {
            return null;
        }
        if (ref.startsWith(ENV_SCHEME)) {
            String var = ref.substring(ENV_SCHEME.length());
            if (var.isBlank()) {
                throw new IllegalArgumentException(
                        "env:// secret reference with empty variable name");
            }
            String val = envSource.apply(var);
            if (val == null) {
                throw new IllegalArgumentException(
                        "Environment variable not set: " + var);
            }
            log.debug("Resolved secret ref (scheme=env, ref_id={})", var);
            return val;
        }
        if (ref.startsWith(VAULT_SCHEME)) {
            throw new UnsupportedOperationException(
                    "vault:// scheme not implemented; use env:// references");
        }
        // Anything else - including a plain password - is forbidden.
        throw new IllegalArgumentException(
                "password_secret_ref must be 'env://VAR' or 'vault://path'; "
                        + "plain strings are rejected to keep credentials out of JSON payloads");
    }
}
