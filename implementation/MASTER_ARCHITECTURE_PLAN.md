# Master Architecture Plan: Metadata Intelligence Platform

This repository documents the structural engineering sequence of building a full-stack, real-time Machine Learning schema mapping pipeline. The architecture bridges a Spring Boot JVM orchestrator with a Python FastAPI ML processing script using Angular SSE boundaries.

## Stage 1: Schema Crawling & Relationship Candidate Pairing
- **JVM (`SchemaExtractionService.java`)**: Replaced deprecated tools with native `java.sql.DatabaseMetaData` extraction pulling `tables` and `columns` iteratively.
- **Python ML (`main.py`)**: Intercepted bulk payload. Used O(n²) string intersections via `Valentine` definitions and `RapidFuzz.fuzz.ratio` logic mapped against standard dataset similarities tracking column overlaps.

## Stage 2: Contextual Semantic Weighting
- **Python NLP**: Imported `spaCy (en_core_web_sm)`. Vectorized column strings comparing word tokens to boost relation validity scores natively beyond simple regex similarity string matching parameters.

## Stage 3: Statistical Row Cardinality
- **Payload Design**: The Java Orchestrator encapsulates the authentic database `jdbc://` connection URI inside of the API POST blocks alongside found Candidates.
- **Python SQLAlchemy Binding**: `main.py` securely transforms the URI into a local `postgresql+psycopg2` driver instance dynamically.
- **Metrics Computation**: Executed strictly native sample heuristics loops analyzing real data: `SELECT COUNT(*), COUNT(DISTINCT col_a) FROM table_a`. Evaluated mathematically to resolve precise `1:1`, `1:N`, `N:1` and `M:N` relations bounds.

## Stage 4: PII / High-Risk Sensitive Extraction
- **Presidio ML Layer**: Booted Microsoft's `presidio-analyzer` pipeline across the complete backend extracting sensitive data entity matches mapping to NLP classification regex tokens dynamically.
- **Persistence Hooks**: Caught `SensitiveResponse` entities back structurally in Java passing into PostgreSQL metrics tables.

## Stage 5: Domain Semantic Grouping
- **Sentence-Transformers Vectors**: Grouped table names & properties into text-strings embedded into 384-dimensional spatial metrics natively via `'all-MiniLM-L6-v2'`.
- **DBSCAN Geometric Mapping**: Iterated distance clusters filtering via native epsilon radius bounds matching intersecting schemas automatically.
- **spaCy Centroid Naming**: NLP mapped explicit noun structures representing spatial geometry layers assigning automated literal boundary categories like ("Financial Data", "Miscellaneous Data").

## Stage 6: Central Context ER Graph Processing
- **NetworkX Integration**: Fed all previous ML layer findings (`relationships` and `clusters`) directly to `networkx.Graph()`.
- **UI Serialization Matrix**: Iterated all Edges and Nodes strictly mapped by geometric domains out to standard D3.js and Vis.js JSON shapes: `{"nodes": [], "links": []}` returning to Java payload limits securely.

### Orchestration Refactoring
The primary monolithic JVM handler was ultimately completely destructed isolating boundaries:
- `SseBroadcasterService`: Exclusively proxies User Interface HTTP streaming events securely.
- `PythonMlIntegrationService`: Exclusively hides external ML port REST mappings using strong DTO JSON wrappers.
