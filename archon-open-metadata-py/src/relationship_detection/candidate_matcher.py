import itertools
from rapidfuzz import fuzz
from src.models import SchemaTable
from src.constants import AppConstants

class CandidateMatcher:
    """
    Stage 1: Analyzes column structures across tables using 
    fuzzy string matching and data-type constraints to identify 
    initial foreign key candidates.
    """
    @staticmethod
    def detect_candidates(tables: list[SchemaTable]) -> list[dict]:
        candidates = []
        for t1, t2 in itertools.combinations(tables, 2):
            for c1 in t1.columns:
                for c2 in t2.columns:
                    dt1 = str(c1.dataType).lower()
                    dt2 = str(c2.dataType).lower()
                    
                    unstructured_types = ['blob', 'clob', 'nclob', 'bfile', 'json', 'jsonb', 'xml', 'bytea', 'raw', 'long raw', 'image', 'binary', 'varbinary']
                    if dt1 in unstructured_types or dt2 in unstructured_types:
                        continue

                    if dt1 != dt2:
                        continue
                        
                    s1 = str(c1.columnName).lower()
                    s2 = str(c2.columnName).lower()
                    score_ratio = fuzz.ratio(s1, s2)
                    score_sort = fuzz.token_sort_ratio(s1, s2)
                    score = max(score_ratio, score_sort)
                    
                    if score >= AppConstants.CANDIDATE_MIN_SCORE:
                        candidates.append({
                            "table_a": t1.tableName,
                            "col_a": c1.columnName,
                            "table_b": t2.tableName,
                            "col_b": c2.columnName,
                            "score": round(score / 100.0, 2)
                        })
                        
        return candidates
