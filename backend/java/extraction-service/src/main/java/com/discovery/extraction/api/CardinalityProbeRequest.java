package com.discovery.extraction.api;

import java.util.List;

/**
 * Request body for {@code POST /api/v1/probe-cardinality} — Sprint 4.
 *
 * <p>The pipeline batches {@code (schema, table, column)} triples and asks
 * the service to return {@code COUNT(*)} and {@code COUNT(DISTINCT col)}
 * for each.  Identifiers are NOT trusted on the wire: the service runs
 * each through {@link com.discovery.extraction.core.QueryWhitelistValidator}
 * (or equivalent identifier-safe quoting) before splicing into SQL.
 *
 * <p>This endpoint is sync-only — same contract as {@code /extract}.  No
 * partial response; either every probe succeeds or the service returns a
 * partial list with the failures absent (caller treats absence as
 * "couldn't probe").
 */
public record CardinalityProbeRequest(
        ConnectionConfig connection,
        List<Pair> pairs) {

    /** One probe target. */
    public record Pair(String schema, String table, String column) {}
}
