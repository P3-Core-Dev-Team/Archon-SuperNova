# csv-extractor

Tiny standalone Java tool that walks a Postgres schema and writes one CSV
per table on a worker pool.  Uses `COPY (SELECT * FROM <schema>.<table>)
TO STDOUT (FORMAT CSV, HEADER, FORCE_QUOTE *, NULL '')` — fast, exact, no
JDBC ResultSet round-tripping.

## Build

With Maven installed:

```bash
mvn -q -f backend/java/csv-extractor/pom.xml package
java -jar backend/java/csv-extractor/target/csv-extractor.jar --help
```

Without Maven (uses the local `~/.m2` jar or downloads from Maven Central):

```bash
./backend/java/csv-extractor/build.sh
./backend/java/csv-extractor/target/run.sh --help
```

## Run

```bash
./target/run.sh \
  --schema adv \
  --out /tmp/adv-csv \
  --threads 6
```

Defaults match the local-dev rig: `localhost:5432`, db `test`, user
`adsuser`, password `Ads@3421`, schema `adv`, output `./csv-out`,
threads = half of CPU cores.  All overridable per CLI flag.

Pass `--password env://VAR` to read the password from an environment
variable instead of the command line (mirrors the same scheme used by
the Python pipeline / mock extractor).

## Output

One file per table, named `<schema>__<table>.csv`:

```
$ head -2 /tmp/adv-csv/adv__phone_number_type.csv
phone_number_type_id,name,modified_date
"1","Cell","2026-04-25 16:56:02.197317+05:30"
```

Header row first, every value double-quoted (`FORCE_QUOTE *`), NULLs
emitted as empty unquoted fields (`NULL ''`).

## Threading

Each worker opens its own JDBC connection (Postgres connections aren't
thread-safe to share for COPY).  The fixed-size pool defaults to half of
the host's CPU cores; bump with `--threads` for parallel CSV writes when
the source DB and disk can keep up.

## Footprint

- 1 source file (~280 LOC)
- 1 dependency: `postgresql 42.7.3` (the JDBC driver)
- Fat-jar build via maven-assembly-plugin (~1.2 MB output)
