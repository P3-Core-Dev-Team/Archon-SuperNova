package com.discovery.extraction.api;

import java.util.List;

/**
 * Response body for {@code POST /api/v1/probe-cardinality} — Sprint 4.
 *
 * <p>One {@link Result} per successfully-probed pair.  The pipeline
 * matches results back to its eligible-relationships list by
 * {@code (schema, table, column)} key; pairs that the service couldn't
 * probe (table missing, identifier rejected, permission error) are
 * simply absent from the result list rather than emitted as nulls or
 * sentinel rows.
 */
public record CardinalityProbeResponse(List<Result> results) {

    /** One probe result.  ``totalRows`` and ``distinctCount`` are
     *  unsigned 64-bit counts; serialised as JSON numbers. */
    public record Result(
            String schema,
            String table,
            String column,
            long totalRows,
            long distinctCount) {}
}
