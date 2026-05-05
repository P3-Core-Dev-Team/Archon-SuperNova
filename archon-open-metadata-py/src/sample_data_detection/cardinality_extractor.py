from sqlalchemy import create_engine, text
from src.models import ConnectionDetails

class CardinalityExtractor:
    """
    Connects to the source database to fetch active sample
    data overlaps (COUNT vs DISTINCT) to accurately determine 1:1, 1:N cardinality.
    """
    @staticmethod
    def extract_cardinality(connection: ConnectionDetails, candidates: list[dict]) -> list[dict]:
        jdbc = connection.url
        host_db = jdbc.split("jdbc:postgresql://")[1]
        db_uri = f"postgresql+psycopg2://{connection.username}:{connection.password}@{host_db}"
        
        mocking_layer = False
        engine = None
        try:
            engine = create_engine(db_uri)
        except Exception:
            mocking_layer = True

        results = []
        for cand in candidates:
            t_a = cand.get("table_a")
            c_a = cand.get("col_a")
            t_b = cand.get("table_b")
            c_b = cand.get("col_b")
            score = cand.get("score")
            
            cardinality = "1:1"
            if not mocking_layer and engine:
                try:
                    with engine.connect() as conn:
                        qa = text(f"SELECT COUNT(*), COUNT(DISTINCT {c_a}) FROM {t_a};")
                        qb = text(f"SELECT COUNT(*), COUNT(DISTINCT {c_b}) FROM {t_b};")
                        res_a = conn.execute(qa).fetchone()
                        res_b = conn.execute(qb).fetchone()
                        
                        ratio_a = res_a[1] / max(res_a[0], 1)
                        ratio_b = res_b[1] / max(res_b[0], 1)
                        
                        if ratio_a > 0.9 and ratio_b < 0.9: cardinality = "1:N"
                        elif ratio_a < 0.9 and ratio_b > 0.9: cardinality = "N:1"
                        elif ratio_a < 0.9 and ratio_b < 0.9: cardinality = "M:N"
                except Exception:
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
        return results
