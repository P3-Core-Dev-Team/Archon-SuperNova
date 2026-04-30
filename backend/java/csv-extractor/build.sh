#!/usr/bin/env bash
# Build helper for environments without Maven.  Uses javac + the postgres
# JDBC driver from the local M2 cache (or falls back to a known pinned URL).
# For Maven-installed environments, prefer:
#     mvn -q -f backend/java/csv-extractor/pom.xml package
set -euo pipefail

cd "$(dirname "$0")"
PG_VERSION="42.7.3"
PG_JAR_LOCAL="$HOME/.m2/repository/org/postgresql/postgresql/${PG_VERSION}/postgresql-${PG_VERSION}.jar"

if [[ -f "$PG_JAR_LOCAL" ]]; then
    PG_JAR="$PG_JAR_LOCAL"
elif command -v mvn >/dev/null 2>&1; then
    mvn -q dependency:get -Dartifact="org.postgresql:postgresql:${PG_VERSION}" >/dev/null
    PG_JAR="$PG_JAR_LOCAL"
else
    mkdir -p lib
    PG_JAR="lib/postgresql-${PG_VERSION}.jar"
    if [[ ! -f "$PG_JAR" ]]; then
        echo "Downloading $PG_JAR from Maven Central..."
        curl -sSL -o "$PG_JAR" \
            "https://repo1.maven.org/maven2/org/postgresql/postgresql/${PG_VERSION}/postgresql-${PG_VERSION}.jar"
    fi
fi

mkdir -p target/classes
javac -d target/classes -cp "$PG_JAR" \
    src/main/java/com/archon/csvextractor/CsvExtractor.java

cat > target/run.sh <<RUN
#!/usr/bin/env bash
exec java -cp "$(realpath target/classes):$(realpath "$PG_JAR")" \
    com.archon.csvextractor.CsvExtractor "\$@"
RUN
chmod +x target/run.sh

echo "✓ built.  run with:  ./target/run.sh --schema adv --out /tmp/adv-csv"
