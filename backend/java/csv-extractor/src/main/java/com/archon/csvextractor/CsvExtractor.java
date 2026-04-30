package com.archon.csvextractor;

import org.postgresql.PGConnection;
import org.postgresql.copy.CopyManager;

import java.io.BufferedOutputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Standalone Postgres → CSV exporter.  Lists every BASE TABLE in a target
 * schema and runs {@code COPY (SELECT * FROM <schema>.<table>) TO STDOUT
 * (FORMAT CSV, HEADER, FORCE_QUOTE *)} per table, writing one CSV per table
 * to the chosen output directory.  Workers run in parallel on a fixed-size
 * thread pool.
 *
 * Build: {@code mvn -q -f backend/java/csv-extractor/pom.xml package}<br>
 * Run:   {@code java -jar backend/java/csv-extractor/target/csv-extractor.jar
 *               --schema adv --out ./adv-csv [--threads 4] ...}
 *
 * <p>Defaults match the local-dev configuration used by the rest of the
 * repo: {@code localhost:5432}, database {@code test}, user
 * {@code adsuser}, password {@code Ads@3421}.  Override per CLI flag, or
 * pass {@code --password env://VAR} to read from an environment variable.
 */
public final class CsvExtractor {

    public static void main(String[] args) throws Exception {
        Args opts = Args.parse(args);
        if (opts.help) { Args.printUsage(); return; }

        Path outDir = Path.of(opts.outDir);
        Files.createDirectories(outDir);

        // List target tables on a single short-lived connection.
        List<String> tables;
        try (Connection conn = openConnection(opts)) {
            tables = listBaseTables(conn, opts.schema);
        }
        if (tables.isEmpty()) {
            System.err.printf("[csv-extractor] schema %s has no base tables in %s/%s%n",
                    opts.schema, opts.host, opts.database);
            return;
        }
        System.out.printf("[csv-extractor] schema=%s tables=%d threads=%d out=%s%n",
                opts.schema, tables.size(), opts.threads, outDir.toAbsolutePath());

        // Fixed-size pool — each worker opens its own JDBC connection
        // (PostgreSQL Connection is not thread-safe to share for COPY).
        ExecutorService pool = Executors.newFixedThreadPool(Math.max(1, opts.threads));
        AtomicLong totalRows = new AtomicLong(0);
        AtomicLong totalBytes = new AtomicLong(0);
        List<Future<TableResult>> futures = new ArrayList<>(tables.size());
        Instant t0 = Instant.now();

        for (String table : tables) {
            futures.add(pool.submit(new ExtractTask(opts, table, outDir)));
        }
        pool.shutdown();

        // Drain results in submission order so the output log reads top-down.
        int succeeded = 0, failed = 0;
        for (int i = 0; i < futures.size(); i++) {
            String table = tables.get(i);
            try {
                TableResult r = futures.get(i).get();
                totalRows.addAndGet(r.rows);
                totalBytes.addAndGet(r.bytes);
                System.out.printf("[csv-extractor]   ✓ %-50s rows=%-10d bytes=%-10d %s%n",
                        table, r.rows, r.bytes, r.path.getFileName());
                succeeded++;
            } catch (Exception ex) {
                failed++;
                System.err.printf("[csv-extractor]   ✗ %-50s FAILED: %s%n",
                        table, rootMessage(ex));
            }
        }
        if (!pool.awaitTermination(60, TimeUnit.SECONDS)) {
            pool.shutdownNow();
        }

        Duration elapsed = Duration.between(t0, Instant.now());
        System.out.printf("[csv-extractor] done in %.1fs · ok=%d fail=%d · total %s rows / %s bytes%n",
                elapsed.toMillis() / 1000.0, succeeded, failed,
                Long.toString(totalRows.get()), formatBytes(totalBytes.get()));
        if (failed > 0) System.exit(1);
    }

    // ----------------------------------------------------------------------
    // Worker
    // ----------------------------------------------------------------------

    /** Run the {@code COPY ... TO STDOUT} for a single table. */
    private record ExtractTask(Args opts, String table, Path outDir)
            implements Callable<TableResult> {

        @Override
        public TableResult call() throws Exception {
            Path csv = outDir.resolve(opts.schema + "__" + table + ".csv");
            String copySql = "COPY (SELECT * FROM "
                    + quoteIdent(opts.schema) + "." + quoteIdent(table)
                    + ") TO STDOUT WITH (FORMAT CSV, HEADER, FORCE_QUOTE *, NULL '')";
            long rows;
            try (Connection conn = openConnection(opts);
                 OutputStream raw = Files.newOutputStream(csv);
                 OutputStream out = new BufferedOutputStream(raw, 1 << 20 /* 1MiB */)) {
                CopyManager copy = conn.unwrap(PGConnection.class).getCopyAPI();
                // copyOut returns the ROW count; the file size on disk is
                // the byte count (read after the stream closes).
                rows = copy.copyOut(copySql, out);
            }
            long bytes = Files.size(csv);
            return new TableResult(table, csv, rows, bytes);
        }
    }

    private record TableResult(String table, Path path, long rows, long bytes) {}

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    private static Connection openConnection(Args o) throws Exception {
        String url = String.format("jdbc:postgresql://%s:%d/%s", o.host, o.port, o.database);
        String pwd = resolvePassword(o.password);
        Connection c = DriverManager.getConnection(url, o.user, pwd);
        c.setAutoCommit(false);
        return c;
    }

    /** Accepts a literal string or {@code env://VAR_NAME} for env-variable
     * resolution.  Mirrors the contract used by the Python mock extractor. */
    private static String resolvePassword(String raw) {
        if (raw == null) return "";
        if (raw.startsWith("env://")) {
            String var = raw.substring("env://".length());
            String val = System.getenv(var);
            if (val == null || val.isEmpty()) {
                throw new IllegalStateException("env var " + var + " is unset");
            }
            return val;
        }
        return raw;
    }

    private static List<String> listBaseTables(Connection c, String schema) throws Exception {
        String sql = "SELECT table_name FROM information_schema.tables "
                   + "WHERE table_schema = ? AND table_type = 'BASE TABLE' "
                   + "ORDER BY table_name";
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, schema);
            try (ResultSet rs = ps.executeQuery()) {
                List<String> out = new ArrayList<>();
                while (rs.next()) out.add(rs.getString(1));
                return out;
            }
        }
    }

    /** Double-quote a Postgres identifier; double any embedded quote. */
    private static String quoteIdent(String s) {
        return "\"" + s.replace("\"", "\"\"") + "\"";
    }

    private static String rootMessage(Throwable t) {
        Throwable cur = t;
        while (cur.getCause() != null && cur.getCause() != cur) cur = cur.getCause();
        return cur.getClass().getSimpleName() + ": " + cur.getMessage();
    }

    private static String formatBytes(long n) {
        if (n < 1024) return n + " B";
        if (n < 1024 * 1024) return String.format("%.1f KiB", n / 1024.0);
        if (n < 1024L * 1024 * 1024) return String.format("%.1f MiB", n / (1024.0 * 1024));
        return String.format("%.2f GiB", n / (1024.0 * 1024 * 1024));
    }

    // ----------------------------------------------------------------------
    // CLI parsing
    // ----------------------------------------------------------------------

    static final class Args {
        String host = "localhost";
        int    port = 5432;
        String database = "test";
        String user = "adsuser";
        String password = "Ads@3421";
        String schema = "adv";
        String outDir = "./csv-out";
        int    threads = Math.max(2, Runtime.getRuntime().availableProcessors() / 2);
        boolean help;

        static Args parse(String[] args) {
            Args a = new Args();
            for (int i = 0; i < args.length; i++) {
                String k = args[i];
                switch (k) {
                    case "--host"     -> a.host = args[++i];
                    case "--port"     -> a.port = Integer.parseInt(args[++i]);
                    case "--db", "--database" -> a.database = args[++i];
                    case "--user"     -> a.user = args[++i];
                    case "--password" -> a.password = args[++i];
                    case "--schema"   -> a.schema = args[++i];
                    case "--out"      -> a.outDir = args[++i];
                    case "--threads"  -> a.threads = Integer.parseInt(args[++i]);
                    case "-h", "--help"   -> a.help = true;
                    default -> {
                        System.err.println("Unknown arg: " + k);
                        a.help = true;
                    }
                }
            }
            return a;
        }

        static void printUsage() {
            System.out.println("""
                Usage: java -jar csv-extractor.jar [options]

                Postgres → CSV exporter.  Lists every BASE TABLE in --schema and
                writes one CSV file per table to --out using a worker pool.

                Options
                  --host <h>          Postgres host        (default: localhost)
                  --port <p>          Postgres port        (default: 5432)
                  --db <name>         Database name        (default: test)
                  --user <u>          Postgres user        (default: adsuser)
                  --password <p>      Password literal, OR env://VAR  (default: Ads@3421)
                  --schema <s>        Source schema        (default: adv)
                  --out <dir>         Output directory     (default: ./csv-out)
                  --threads <n>       Worker pool size     (default: half of CPU cores)
                  -h, --help          Print this help

                Examples
                  java -jar csv-extractor.jar --schema adv --out ./adv-csv
                  java -jar csv-extractor.jar --schema saleor --threads 8 --password env://PG_PWD
                """);
        }
    }

    private CsvExtractor() {}
}
