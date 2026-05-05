from src.models import SchemaTable

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

class DomainGrouper:
    """
    Groups tables logically into domains (Sales, Finance, etc.) using
    Sentence-Transformers clustering against NLP text representations of schemas.
    """
    @staticmethod
    def group_domains(tables: list[SchemaTable], relationships: list[dict]) -> list[dict]:
        clusters_rep = []
        if embedder and DBSCAN and np and tables:
            texts = []
            table_names = []
            
            for t in tables:
                table_names.append(t.tableName)
                desc = t.tableName.replace("_", " ") + " " + " ".join([c.columnName.replace("_", " ") for c in t.columns])
                texts.append(desc)
                
            embeddings = embedder.encode(texts)
            clustering = DBSCAN(eps=0.5, min_samples=1).fit(embeddings)
            labels = clustering.labels_
            
            try:
                import networkx as nx
                G = nx.Graph()
                for i, t_name in enumerate(table_names):
                    G.add_node(i, label=labels[i])
                for rel in relationships:
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
            except Exception:
                pass

            unique_labels = set(labels)
            for lbl in unique_labels:
                group_tables = []
                group_docs = []
                for i, l in enumerate(labels):
                    if l == lbl:
                        group_tables.append(table_names[i])
                        group_docs.append(texts[i])
                
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
                clusters_rep.append({"domainName": domain_name, "tableNames": group_tables})
        else:
            grouped = {}
            for t in tables:
                name = t.tableName.lower()
                if "cust" in name or "user" in name or "client" in name:
                    grouped.setdefault("Customer Data", []).append(t.tableName)
                elif "pay" in name or "inv" in name or "fin" in name:
                    grouped.setdefault("Financial Data", []).append(t.tableName)
                else:
                    grouped.setdefault("Miscellaneous Data", []).append(t.tableName)
            clusters_rep = [{"domainName": k, "tableNames": v} for k, v in grouped.items()]

        return clusters_rep
