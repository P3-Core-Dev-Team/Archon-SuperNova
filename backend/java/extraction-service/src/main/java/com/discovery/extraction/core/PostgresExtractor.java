package com.discovery.extraction.core;

import com.discovery.extraction.api.ExtractionOptions;
import com.discovery.extraction.api.ExtractionRequest;
import com.discovery.extraction.api.ManifestEntry;
import com.discovery.extraction.api.OutputConfig;
import com.discovery.extraction.parquet.ArrowSchemaBuilder;
import com.discovery.extraction.parquet.StreamingParquetWriter;
import com.discovery.extraction.storage.LocalStorageBackend;
import org.apache.arrow.vector.types.pojo.Schema;
import org.postgresql.PGConnection;
import org.postgresql.copy.CopyManager;
import org.postgresql.copy.CopyOut;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Types;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Postgres-specific extractor that streams data via {@code COPY (query) TO
 * STDOUT}. The CSV mode below uses {@code FORCE_QUOTE *, NULL ''} so every
 * non-NULL field arrives wrapped in double quotes and a NULL is - and only
 * is - represented by an unquoted empty string. This eliminates the
 * silent-NULL-coercion class of bugs that the prior sentinel-string scheme
 * exposed (see C1 in the review).
 *
 * <p>The chosen mode is documented inline at {@link #COPY_TEMPLATE} - we
 * deliberately use CSV with {@code FORCE_QUOTE *} rather than
 * {@code FORMAT BINARY}. Reasons:
 * <ul>
 *     <li>FORCE_QUOTE * pins NULL detection to a syntactic property of the
 *         encoded stream, not a value-domain string. There is no possible
 *         legitimate text value that decodes to NULL.</li>
 *     <li>The CSV path is dramatically simpler than maintaining a per-OID
 *         binary decoder; on wide tables it still delivers ~2-3x the
 *         throughput of plain {@code ResultSet} iteration.</li>
 *     <li>Apache Arrow type coercion still happens in
 *         {@link StreamingParquetWriter}, so logical typing (date/decimal/
 *         etc.) is preserved through schema metadata.</li>
 * </ul>
 *
 * <p>This extractor is the only extractor in the monolith. There is no
 * fallback path - if COPY fails, the exception propagates.
 */
@Component
public class PostgresExtractor {

    private static final Logger log = LoggerFactory.getLogger(PostgresExtractor.class);

    /**
     * COPY command template. {@code FORCE_QUOTE *} forces every non-NULL
     * field to be double-quoted, and {@code NULL ''} maps NULLs to an
     * unquoted empty field - the only unquoted form the parser can see.
     */
    private static final String COPY_TEMPLATE =
            "COPY (%s) TO STDOUT (FORMAT CSV, HEADER false, "
                    + "FORCE_QUOTE *, NULL '', DELIMITER ',', QUOTE '\"', ESCAPE '\"')";

    public List<ManifestEntry> extract(ExtractionRequest request,
                                       DataSource dataSource,
                                       LocalStorageBackend storage) throws Exception {
        OutputConfig output = request.output();
        ExtractionOptions opts = request.optionsOrDefault();
        Long maxRows = opts.maxRows();

        Path scratch = storage.resolveScratchPath(output.path());

        try (Connection conn = dataSource.getConnection()) {
            conn.setAutoCommit(false);

            // Step 1: learn the column types via prepared-statement metadata
            // without actually executing the user query (cheap on Postgres).
            Schema schema;
            int[] jdbcTypes;
            try (PreparedStatement ps = conn.prepareStatement(request.query())) {
                ResultSetMetaData md = ps.getMetaData();
                if (md == null) {
                    // Driver couldn't infer metadata without execution -
                    // fall back to LIMIT 1 semantics.
                    ps.setMaxRows(1);
                    try (ResultSet rs = ps.executeQuery()) {
                        md = rs.getMetaData();
                        schema = ArrowSchemaBuilder.fromMetadata(md);
                        jdbcTypes = new int[md.getColumnCount()];
                        for (int i = 0; i < jdbcTypes.length; i++) {
                            jdbcTypes[i] = md.getColumnType(i + 1);
                        }
                    }
                } else {
                    schema = ArrowSchemaBuilder.fromMetadata(md);
                    jdbcTypes = new int[md.getColumnCount()];
                    for (int i = 0; i < jdbcTypes.length; i++) {
                        jdbcTypes[i] = md.getColumnType(i + 1);
                    }
                }
            }

            PGConnection pg = conn.unwrap(PGConnection.class);
            CopyManager copy = pg.getCopyAPI();
            String copySql = copySql(request.query());
            log.debug("Postgres COPY: {}", copySql);

            StreamingParquetWriter writer = new StreamingParquetWriter(schema, output, scratch);
            try {
                try (CopyOut out = copy.copyOut(copySql)) {
                    CsvRowConsumer consumer = new CsvRowConsumer(jdbcTypes.length,
                            writer, jdbcTypes, maxRows);
                    byte[] buf;
                    while ((buf = out.readFromCopy()) != null) {
                        if (Thread.currentThread().isInterrupted()) {
                            out.cancelCopy();
                            throw new InterruptedException("Extraction interrupted");
                        }
                        consumer.accept(buf);
                        if (consumer.isCapReached()) {
                            out.cancelCopy();
                            break;
                        }
                    }
                    consumer.finishIfPartial();
                    log.debug("Postgres COPY rows_written={}", writer.rowsWritten());
                }
            } finally {
                // Order matters: close the writer (flushes Parquet footer)
                // BEFORE asking storage to upload / hash the scratch file.
                writer.close();
                try {
                    conn.commit();
                } catch (java.sql.SQLException sqle) {
                    log.warn("COPY transaction commit failed: {}", sqle.getMessage());
                }
            }
            String finalUri = storage.upload(scratch, output.path());
            ManifestEntry entry = writer.toManifestEntry(finalUri);
            return Collections.singletonList(entry);
        }
    }

    static String copySql(String query) {
        return String.format(COPY_TEMPLATE, query);
    }

    public void testConnection(DataSource dataSource) throws Exception {
        try (Connection conn = dataSource.getConnection();
             var stmt = conn.createStatement();
             var rs = stmt.executeQuery("SELECT 1")) {
            if (!rs.next() || rs.getInt(1) != 1) {
                throw new IllegalStateException("Postgres test SELECT 1 returned no rows");
            }
        }
    }

    /**
     * Incremental CSV parser that materializes rows into the Parquet writer
     * as they arrive from {@link CopyOut#readFromCopy()}. Handles quoted
     * fields, escaped quotes (RFC-4180 style with QUOTE='"', ESCAPE='"'),
     * and newlines inside quoted fields.
     *
     * <p>Because the COPY command uses {@code FORCE_QUOTE *, NULL ''}, every
     * non-NULL field is delivered quoted; an unquoted empty field is the
     * unambiguous NULL marker.
     */
    static final class CsvRowConsumer {

        private final int columnCount;
        private final StreamingParquetWriter writer;
        private final int[] jdbcTypes;
        private final Long maxRows;

        private final ByteArrayOutputStream field = new ByteArrayOutputStream(128);
        private final List<String> row;
        private final List<Boolean> rowWasQuoted;
        private boolean inQuotes = false;
        private boolean fieldEverQuoted = false;
        private boolean capReached = false;
        private long written = 0;

        CsvRowConsumer(int columnCount,
                       StreamingParquetWriter writer,
                       int[] jdbcTypes,
                       Long maxRows) {
            this.columnCount = columnCount;
            this.writer = writer;
            this.jdbcTypes = jdbcTypes;
            this.maxRows = maxRows;
            this.row = new ArrayList<>(columnCount);
            this.rowWasQuoted = new ArrayList<>(columnCount);
        }

        boolean isCapReached() {
            return capReached;
        }

        void accept(byte[] buf) throws IOException {
            int i = 0;
            while (i < buf.length) {
                byte b = buf[i];
                if (inQuotes) {
                    if (b == '"') {
                        // Peek for escaped quote ""
                        if (i + 1 < buf.length && buf[i + 1] == '"') {
                            field.write('"');
                            i += 2;
                            continue;
                        }
                        inQuotes = false;
                        i++;
                        continue;
                    }
                    field.write(b);
                    i++;
                    continue;
                }
                // Not in quotes
                if (b == ',') {
                    finishField();
                    i++;
                    continue;
                }
                if (b == '"') {
                    inQuotes = true;
                    fieldEverQuoted = true;
                    i++;
                    continue;
                }
                if (b == '\n') {
                    finishField();
                    finishRow();
                    i++;
                    if (capReached) {
                        return;
                    }
                    continue;
                }
                if (b == '\r') {
                    // Swallow CR; LF handles row termination.
                    i++;
                    continue;
                }
                field.write(b);
                i++;
            }
        }

        /**
         * Flushes any trailing in-progress row. The final row emitted by
         * Postgres always ends with LF so in normal operation this is a no-op.
         */
        void finishIfPartial() throws IOException {
            if (field.size() > 0 || !row.isEmpty()) {
                finishField();
                if (!row.isEmpty()) {
                    finishRow();
                }
            }
        }

        private void finishField() {
            String raw = field.toString(StandardCharsets.UTF_8);
            boolean wasQuoted = fieldEverQuoted;
            field.reset();
            fieldEverQuoted = false;
            // FORCE_QUOTE * means every non-NULL value is delivered quoted.
            // Therefore an unquoted, empty field is unambiguously NULL and
            // anything else is real data - including a quoted empty string,
            // which decodes to "" (an actual zero-length string).
            if (!wasQuoted && raw.isEmpty()) {
                row.add(null);
            } else {
                row.add(raw);
            }
            rowWasQuoted.add(wasQuoted);
        }

        private void finishRow() throws IOException {
            if (row.size() != columnCount) {
                while (row.size() < columnCount) {
                    row.add(null);
                    rowWasQuoted.add(false);
                }
                while (row.size() > columnCount) {
                    row.remove(row.size() - 1);
                    rowWasQuoted.remove(rowWasQuoted.size() - 1);
                }
            }
            Object[] values = new Object[columnCount];
            for (int i = 0; i < columnCount; i++) {
                values[i] = preCoerce(row.get(i), jdbcTypes[i]);
            }
            writer.writeRow(values);
            row.clear();
            rowWasQuoted.clear();
            written++;
            if (maxRows != null && written >= maxRows) {
                capReached = true;
            }
        }

        /**
         * Lightweight pre-coercion for types where the CSV form is not the
         * natural Java representation the Parquet writer expects.
         */
        private static Object preCoerce(String csv, int jdbcType) {
            if (csv == null) {
                return null;
            }
            switch (jdbcType) {
                case Types.BOOLEAN:
                case Types.BIT:
                    if (csv.length() == 1) {
                        char c = csv.charAt(0);
                        if (c == 't' || c == 'T' || c == '1') {
                            return Boolean.TRUE;
                        }
                        if (c == 'f' || c == 'F' || c == '0') {
                            return Boolean.FALSE;
                        }
                    }
                    return Boolean.parseBoolean(csv);
                case Types.BINARY:
                case Types.VARBINARY:
                case Types.LONGVARBINARY:
                case Types.BLOB: {
                    // Postgres outputs bytea in hex (\x...) by default. Trim
                    // the prefix and hex-decode; on any parse failure fall
                    // back to the raw bytes of the CSV text.
                    String s = csv;
                    if (s.startsWith("\\x") || s.startsWith("\\X")) {
                        s = s.substring(2);
                    }
                    try {
                        int len = s.length() / 2;
                        byte[] bytes = new byte[len];
                        for (int i = 0; i < len; i++) {
                            bytes[i] = (byte) Integer.parseInt(
                                    s.substring(i * 2, i * 2 + 2), 16);
                        }
                        return bytes;
                    } catch (NumberFormatException nfe) {
                        return csv.getBytes(StandardCharsets.UTF_8);
                    }
                }
                default:
                    return csv;
            }
        }
    }
}
