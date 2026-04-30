from sentence_transformers import SentenceTransformer
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

embedder = SentenceTransformer('all-MiniLM-L6-v2')
predefined_domains = ["Sales", "Finance", "HR", "ERP", "SAP", "Customer", "Operations"]
domain_embeddings = embedder.encode(predefined_domains)

merged = "employee salary payroll bonus"
cluster_embedding = embedder.encode([merged])
similarities = cosine_similarity(cluster_embedding, domain_embeddings)[0]
best_match_idx = np.argmax(similarities)
print(predefined_domains[best_match_idx], similarities[best_match_idx])
