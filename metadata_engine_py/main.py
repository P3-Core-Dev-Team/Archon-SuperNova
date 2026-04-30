from fastapi import FastAPI
from pydantic import BaseModel
import requests

import itertools
from rapidfuzz import fuzz
import spacy

# Load NLP model globally for semantic checks
try:
    nlp = spacy.load("en_core_web_sm")
except Exception:
    nlp = None

app = FastAPI()

class SchemaColumn(BaseModel):
    columnName: str
    dataType: str
    length: int | None = None

class SchemaTable(BaseModel):
    tableName: str
    columns: list[SchemaColumn]

class BulkSchemaRequest(BaseModel):
    schemaName: str | None = None
    tables: list[SchemaTable]
    schemaCrawlerRelationships: list[dict] | None = None
    mlRelationships: list[dict] | None = None

@app.post("/api/v1/stage-candidate-detection")
def stage_candidate_detection(req: BulkSchemaRequest):
    candidates = []
    
    # 🔹 Stage 1: Schema Crawling + Candidate Detection
    # Valentine → match columns
    for t1, t2 in itertools.combinations(req.tables, 2):
        for c1 in t1.columns:
            for c2 in t2.columns:
                # Optimize speed: Hard drop incompatible physical datatypes before Fuzzy Math, unless it's a known numeric/string variation
                dt1 = str(c1.dataType).lower()
                dt2 = str(c2.dataType).lower()
                
                # Hard drop unstructured/binary types from RDBMS (Oracle, Postgres, SQL Server)
                unstructured_types = ['blob', 'clob', 'nclob', 'bfile', 'json', 'jsonb', 'xml', 'bytea', 'raw', 'long raw', 'image', 'binary', 'varbinary']
                if dt1 in unstructured_types or dt2 in unstructured_types:
                    continue

                # Enforce strict data type match, otherwise ignore it completely
                if dt1 != dt2:
                    continue
                    
                # RapidFuzz → similarity score
                s1 = str(c1.columnName).lower()
                s2 = str(c2.columnName).lower()
                score_ratio = fuzz.ratio(s1, s2)
                score_sort = fuzz.token_sort_ratio(s1, s2)
                score = max(score_ratio, score_sort)
                
                # Valentine Coma threshold abstraction
                if score >= 55.0:
                    candidates.append({
                        "table_a": t1.tableName,
                        "col_a": c1.columnName,
                        "table_b": t2.tableName,
                        "col_b": c2.columnName,
                        "score": round(score / 100.0, 2)
                    })
                    
    return {"message": "Candidate detection complete", "candidates": candidates}

@app.post("/api/v1/stage-relationship-scoring")
def stage_relationship_scoring(req: dict):
    # 🔹 Stage 2: Relationship Scoring (Semantic + Context)
    scored = []
    candidates = req.get("candidates", [])
    
    for cand in candidates:
        col_a = cand.get("col_a", "")
        col_b = cand.get("col_b", "")
        # score holds rapidfuzz baseline from stage 1
        base_score = cand.get("score", 0.0) * 100.0
        
        # ⚙️ Features: spaCy semantic similarity / entity recognition
        semantic_boost = 0.0
        if nlp:
            doc_a = nlp(str(col_a).replace("_", " "))
            doc_b = nlp(str(col_b).replace("_", " "))
            if doc_a.vector_norm and doc_b.vector_norm:
                semantic_boost += (doc_a.similarity(doc_b) * 35.0) # Boost up to 35 points
                
        # Substring / abbreviation logic
        s_a = str(col_a).lower()
        s_b = str(col_b).lower()
        if (s_a in s_b and len(s_a) >= 2) or (s_b in s_a and len(s_b) >= 2):
            semantic_boost += 15.0
        
        final_score = min(100.0, base_score + semantic_boost)
            
        if final_score >= 55.0:
            cand["score"] = round(final_score / 100.0, 2)
            scored.append(cand)
            
    return {"message": "Relationship scoring complete", "candidates": scored}

class ConnectionDetails(BaseModel):
    url: str
    username: str
    password: str

class CardinalityRequest(BaseModel):
    connection: ConnectionDetails
    candidates: list[dict]

from sqlalchemy import create_engine, text

@app.post("/api/v1/stage-cardinality")
def stage_cardinality(req: CardinalityRequest):
    # Mapping JDBC String into SQLAlchemy Postgres Connection Engine
    jdbc = req.connection.url
    host_db = jdbc.split("jdbc:postgresql://")[1]
    db_uri = f"postgresql+psycopg2://{req.connection.username}:{req.connection.password}@{host_db}"
    
    # Mocking Database Engine locally for resilience down-stream if DB fails
    mocking_layer = False
    engine = None
    try:
        engine = create_engine(db_uri)
    except Exception:
        mocking_layer = True

    results = []
    
    for cand in req.candidates:
        t_a = cand.get("table_a")
        c_a = cand.get("col_a")
        t_b = cand.get("table_b")
        c_b = cand.get("col_b")
        score = cand.get("score")
        
        cardinality = "1:1"
        
        if not mocking_layer and engine:
            try:
                with engine.connect() as conn:
                    # 🔹 Stage 3: Cardinality (COUNT-Based)
                    qa = text(f"SELECT COUNT(*), COUNT(DISTINCT {c_a}) FROM {t_a};")
                    qb = text(f"SELECT COUNT(*), COUNT(DISTINCT {c_b}) FROM {t_b};")
                    
                    res_a = conn.execute(qa).fetchone()
                    res_b = conn.execute(qb).fetchone()
                    
                    # Logic Mapping (Approximation logic via ratio overlap)
                    ratio_a = res_a[1] / max(res_a[0], 1)
                    ratio_b = res_b[1] / max(res_b[0], 1)
                    
                    if ratio_a > 0.9 and ratio_b < 0.9: cardinality = "1:N"
                    elif ratio_a < 0.9 and ratio_b > 0.9: cardinality = "N:1"
                    elif ratio_a < 0.9 and ratio_b < 0.9: cardinality = "M:N"
            except Exception:
                # Fallback to simulated mapping
                if "id" in c_a and "id" not in c_b: cardinality = "1:N"
                elif "id" not in c_a and "id" in c_b: cardinality = "N:1"
        else:
            if "id" in c_a and "id" not in c_b: cardinality = "1:N"
            elif "id" not in c_a and "id" in c_b: cardinality = "N:1"
        
        results.append({
            "sourceTableName": t_a,
            "sourceColumn": c_a,
            "targetTableName": t_b,
            "targetColumn": c_b,
            "score": score,
            "cardinality": cardinality
        })
        
    return {"message": "Cardinality extraction complete", "relationships": results}

try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    presidio_layer = AnalyzerEngine()
    
    financial_recognizer = PatternRecognizer(supported_entity="FINANCIAL_SALARY", patterns=[Pattern("salary_pattern", r"(?i)\b(salary|wage|compensation|bonus|pay|amount)\b", 0.8)])
    credential_recognizer = PatternRecognizer(supported_entity="HIGH_RISK_CREDENTIAL", patterns=[Pattern("credential_pattern", r"(?i)\b(password|passwd|pwd|secret|api_key|token)\b", 0.8)])
    phi_recognizer = PatternRecognizer(supported_entity="PHI_HEALTH_RECORD", patterns=[Pattern("phi_pattern", r"(?i)\b(health|medical|diagnosis|disease|blood)\b", 0.8)])
    
    presidio_layer.registry.add_recognizer(financial_recognizer)
    presidio_layer.registry.add_recognizer(credential_recognizer)
    presidio_layer.registry.add_recognizer(phi_recognizer)
except Exception:
    presidio_layer = None

@app.post("/api/v1/stage-sensitive-detection")
def stage_sensitive_detection(req: BulkSchemaRequest):
    # 🔹 Stage 4: Sensitive Detection / PII Mapping (Presidio + spaCy NLP)
    sensitive_columns = []
    
    for t in req.tables:
        for col_obj in t.columns:
            if presidio_layer:
                # Features: PII detection, regex matching, NLP classification
                res = presidio_layer.analyze(text=str(col_obj.columnName).replace("_", " "), language='en')
                if len(res) > 0:
                    for r in res:
                        sensitive_columns.append({
                            "transientTableName": t.tableName,
                            "columnName": col_obj.columnName,
                            "category": r.entity_type
                        })
            else:
                import re
                # Mock fallback if native package dependencies aren't accessible
                mock_hit = False
                c_low = str(col_obj.columnName).lower()
                
                if re.search(r"\b(salary|finance|amount|pay)\b", c_low):
                    mock_hit = True
                    t_cat = "FINANCIAL"
                elif re.search(r"\b(email|phone|name|address)\b", c_low):
                    mock_hit = True
                    t_cat = "PII"
                elif re.search(r"\b(health|ssn|dob|medical)\b", c_low):
                    mock_hit = True
                    t_cat = "PHI"
                elif re.search(r"\b(password|pwd|secret|key)\b", c_low):
                    mock_hit = True
                    t_cat = "HIGH_RISK_CREDENTIAL"
                    
                if mock_hit:
                    sensitive_columns.append({
                        "transientTableName": t.tableName,
                        "columnName": col_obj.columnName,
                        "category": t_cat
                    })
                    
    return {"message": "Saved sensitive columns", "sensitive_columns": sensitive_columns}

try:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import DBSCAN
    import numpy as np
    import torch
    torch.set_num_threads(4)
    embedder = SentenceTransformer('all-MiniLM-L6-v2')
except Exception:
    embedder = None
    DBSCAN = None
    np = None

@app.post("/api/v1/stage-domain-grouping")
def stage_domain_grouping(req: BulkSchemaRequest):
    # 🔹 Stage 5: Domain Grouping (Sentence-Transformers + DBSCAN API)
    clusters_rep = []
    
    if embedder and DBSCAN and np and req.tables:
        texts = []
        table_names = []
        
        # 1. Convert table → text
        for t in req.tables:
            table_names.append(t.tableName)
            desc = t.tableName.replace("_", " ") + " " + " ".join([c.columnName.replace("_", " ") for c in t.columns])
            texts.append(desc)
            
        # 2. Generate embeddings natively using vectorizer
        embeddings = embedder.encode(texts)
        
        # 3. Compute similarity and Cluster tables exactly via eps radius
        clustering = DBSCAN(eps=0.5, min_samples=1).fit(embeddings)
        
        labels = clustering.labels_
        
        # ✔ Enhance using relationship graph (NetworkX)
        try:
            import networkx as nx
            G = nx.Graph()
            for i, t_name in enumerate(table_names):
                G.add_node(i, label=labels[i])
            
            all_rels = (req.schemaCrawlerRelationships or []) + (req.mlRelationships or [])
            for rel in all_rels:
                t_a = rel.get("sourceTableName")
                t_b = rel.get("targetTableName")
                if t_a in table_names and t_b in table_names:
                    i_a = table_names.index(t_a)
                    i_b = table_names.index(t_b)
                    G.add_edge(i_a, i_b)
                    
            for cc in nx.connected_components(G):
                cc_labels = [labels[n] for n in cc if labels[n] != -1]
                if cc_labels:
                    from collections import Counter
                    dominant_label = Counter(cc_labels).most_common(1)[0][0]
                    for n in cc:
                        labels[n] = dominant_label
        except Exception as e:
            print(f"[DEBUG] NetworkX Graph enhancement failed: {e}", flush=True)

        unique_labels = set(labels)
        
        for lbl in unique_labels:
            group_tables = []
            group_docs = []
            for i, l in enumerate(labels):
                if l == lbl:
                    group_tables.append(table_names[i])
                    group_docs.append(texts[i])
            
            # 4. Label clusters via Zero-Shot Embedding against predefined domains
            domain_name = f"Anonymous_Domain_{lbl}"
            if lbl == -1:
                domain_name = "Miscellaneous Data"
            else:
                predefined_domains = ["Sales", "Finance", "HR", "ERP", "SAP", "Customer Support", "Operations"]
                merged = " ".join(group_docs)
                
                try:
                    from sklearn.metrics.pairwise import cosine_similarity
                    domain_embeddings = embedder.encode(predefined_domains)
                    cluster_embedding = embedder.encode([merged])
                    
                    similarities = cosine_similarity(cluster_embedding, domain_embeddings)[0]
                    best_match_idx = np.argmax(similarities)
                    
                    if similarities[best_match_idx] > 0.15:
                        domain_name = predefined_domains[best_match_idx] + " Data"
                    else:
                        domain_name = "Miscellaneous Data"
                except Exception:
                    domain_name = f"Cluster_{lbl} Data"
                
            clusters_rep.append({
                "domainName": domain_name,
                "tableNames": group_tables
            })
    else:
        # Generic Dynamic Fallback
        grouped = {}
        if req.tables:
            for t in req.tables:
                name = t.tableName.lower()
                if "cust" in name or "user" in name or "client" in name:
                    grouped.setdefault("Customer Data", []).append(t.tableName)
                elif "pay" in name or "inv" in name or "fin" in name:
                    grouped.setdefault("Financial Data", []).append(t.tableName)
                else:
                    grouped.setdefault("Miscellaneous Data", []).append(t.tableName)
        clusters_rep = [{"domainName": k, "tableNames": v} for k, v in grouped.items()]

    return {"message": "Saved domain grouping", "clusters": clusters_rep}

class ContextGraphRequest(BaseModel):
    relationships: list[dict]
    clusters: list[dict]

try:
    import networkx as nx
except ImportError:
    nx = None

@app.post("/api/v1/stage-relationship-context-graph")
def stage_relationship_context_graph(req: ContextGraphRequest):
    # 🔹 Stage 6: Relationship Context Graph (NetworkX native layout computation)
    if nx is None:
        # Fallback to simulated graph structure tracking
        return {
            "message": "Saved relationship context graph (Mocked Setup)",
            "graph": {
                "nodes": [{"id": "customer", "group": 1}, {"id": "orders", "group": 1}],
                "links": [{"source": "customer", "target": "orders", "value": 1}]
            }
        }
        
    G = nx.Graph()
    
    # Process Cluster grouping map lookup
    cluster_map = {}
    domain_group_idx = 1
    for cluster in req.clusters:
        domain_name = cluster.get("domainName", "Unknown")
        for table in cluster.get("tableNames", []):
            cluster_map[table] = {"domain": domain_name, "groupId": domain_group_idx}
        domain_group_idx += 1
        
    # Map raw nodes based on provided Cardinality limits dynamically
    for rel in req.relationships:
        t_a = rel.get("sourceTableName")
        t_b = rel.get("targetTableName")
        score = rel.get("score", 0)
        
        if not G.has_node(t_a):
            G.add_node(t_a, **cluster_map.get(t_a, {"domain": "Miscellaneous", "groupId": 0}))
        if not G.has_node(t_b):
            G.add_node(t_b, **cluster_map.get(t_b, {"domain": "Miscellaneous", "groupId": 0}))
            
        G.add_edge(t_a, t_b, weight=score, cardinality=rel.get("cardinality", "unknown"))
    
    # Serialize to strictly typed JSON format matching standard UI layers: { "nodes": [], "links": [] }
    output_nodes = []
    output_links = []
    
    for node, data in G.nodes(data=True):
        output_nodes.append({
            "id": node,
            "group": data.get("groupId", 0),
            "domain": data.get("domain", "Miscellaneous")
        })
        
    for u, v, data in G.edges(data=True):
        output_links.append({
            "source": u,
            "target": v,
            "value": data.get("weight", 1),
            "cardinality": data.get("cardinality", "")
        })

    return {"message": "Saved relationship context graph", "graph": {"nodes": output_nodes, "links": output_links}}

@app.post("/api/v1/stage-entity-classification")
def stage_entity_classification(req: BulkSchemaRequest):
    classifications = []
    
    outbound_edges = {}
    inbound_edges = {}
    for r in (req.schemaCrawlerRelationships or []) + (req.mlRelationships or []):
        t1, t2 = r.get("sourceTableName"), r.get("targetTableName")
        if t1 and t2:
            outbound_edges[t1] = outbound_edges.get(t1, 0) + 1
            inbound_edges[t2] = inbound_edges.get(t2, 0) + 1

    for table in req.tables:
        tname = table.tableName.lower()
        cols = [c.columnName.lower() for c in table.columns]
        
        table_type = "Standard Entity" # Default
        
        if "config" in tname or "settings" in tname or "prop" in tname:
            table_type = "Config"
        elif tname.endswith("_hist") or tname.endswith("_history") or tname.endswith("_log") or tname.endswith("_audit"):
            table_type = "History / Log"
        elif tname.endswith("_text") or tname.endswith("_tl") or tname.endswith("_i18n"):
            table_type = "Text / Translation"
        elif tname.endswith("_type") or tname.endswith("_status") or tname.endswith("_category") or tname.endswith("_code"):
            table_type = "Master Lookup"
        elif outbound_edges.get(table.tableName, 0) == 0 and inbound_edges.get(table.tableName, 0) >= 1 and len(cols) <= 8:
            table_type = "Master Lookup"
        elif tname.endswith("_item") or tname.endswith("_detail") or tname.endswith("_line"):
            table_type = "Transaction (Item)"
        elif "header" in tname or "order" in tname or "invoice" in tname:
            table_type = "Transaction (Header)"
        elif outbound_edges.get(table.tableName, 0) >= 2 and len(cols) >= 8:
            table_type = "Transaction (Header)"
        
        classifications.append({
            "tableName": table.tableName,
            "tableType": table_type
        })
        
    return {"message": "Classification complete", "classifications": classifications}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
