# Stage 5 Domain Grouping / Clustering Blueprint

To hit the Stage 5 requirement ("Domain Grouping (AUTO - No Hardcoding)"), we need to finalize the Python ML routing by replacing the last mocked components with authentic geometric sentence embeddings and distance clustering. 

## Implementation Plan

### 1. Python ML Engine updates (`main.py`)
- **Table Text Vectorization**: Inside `/api/v1/stage-domain-grouping`, we will intercept the `BulkSchemaRequest` again. We'll join each table's schema (table name + columns) into contextual document strings (e.g., `customer id name email`).
- **Embeddings Calculation**: Natively instantiate `SentenceTransformer('all-MiniLM-L6-v2')` from the `sentence-transformers` library to computationally vectorize each table's schema text into numeric multidimensional tensors.
- **DBSCAN Clustering**: Exactly as you noted, we will map the vectors into `sklearn.cluster.DBSCAN(eps=0.3)` to isolate logical grouping clusters (domains).
- **spaCy Centroid Naming**: For each computed cluster, we will extract the most common textual semantic noun block via the `spaCy` NLP linguistic tags explicitly to dynamically name the logical domain (e.g., "Customer Data" or "Financial Data") instead of random IDs.

### 2. Java DTO and Orchestrator Integration
- **[NEW] `DomainGroupResponse.java`**: Construct the native DTO payload proxy mapping back to `{"domainName": "X", "tables": [...]}`
- **[MODIFY] `PythonMlIntegrationService.java`**: Introduce `extractDomainGroups(jobId, schema)` binding dynamically to `8000/api/v1/stage-domain-grouping`.
- **[MODIFY] `AnalysisService.java`**: Replace the final mock tracking loops `[CLUST]` and `[GRAPH]`, feeding the REST payload seamlessly into `saveDomainGroups(pyRes.getClusters())`.

## Open Questions
Before committing this to the `executeJobRealtime` cycle, some `DBSCAN` outliers might not form a cluster natively if their eps radius doesn't intersect (`label = -1`). Should I bundle all non-clustered tables automatically into an independent "Miscellaneous" domain grouping, or strictly discard them from the final JSON clustering output graph?
