import spacy

try:
    nlp = spacy.load("en_core_web_sm")
except Exception:
    nlp = None

class RelationshipScorer:
    """
    Stage 2: Applies Semantic NLP Models (spaCy) to boost or
    penalize initial fuzzy matched candidate relationships based
    on contextual similarity.
    """
    @staticmethod
    def score_relationships(candidates: list[dict],minValue: int, maxValue: int) -> list[dict]:
        scored = []
        for cand in candidates:
            col_a = cand.get("col_a", "")
            col_b = cand.get("col_b", "")
            base_score = cand.get("score", 0.0) * 100.0
            
            semantic_boost = 0.0
            if nlp:
                doc_a = nlp(str(col_a).replace("_", " "))
                doc_b = nlp(str(col_b).replace("_", " "))
                if doc_a.vector_norm and doc_b.vector_norm:
                    semantic_boost += (doc_a.similarity(doc_b) * 35.0)
                    
            s_a = str(col_a).lower()
            s_b = str(col_b).lower()
            if (s_a in s_b and len(s_a) >= 2) or (s_b in s_a and len(s_b) >= 2):
                semantic_boost += 15.0
            
            final_score = min(100.0, base_score + semantic_boost)
                
            if minValue <= final_score <= maxValue:
                cand["score"] = round(final_score / 100.0, 2)
                scored.append(cand)
                
        return scored
