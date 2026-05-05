from src.models import SchemaTable

class EntityClassifier:
    """
    Classifies tables based on structure and edge connectivity into:
    Transaction, Master, Config, History, or Text translations.
    """
    @staticmethod
    def classify_entities(tables: list[SchemaTable], relationships: list[dict]) -> list[dict]:
        classifications = []
        outbound_edges = {}
        inbound_edges = {}
        
        for r in relationships:
            t1, t2 = r.get("sourceTableName"), r.get("targetTableName")
            if t1 and t2:
                outbound_edges[t1] = outbound_edges.get(t1, 0) + 1
                inbound_edges[t2] = inbound_edges.get(t2, 0) + 1

        for table in tables:
            tname = table.tableName.lower()
            cols = [c.columnName.lower() for c in table.columns]
            table_type = "Standard Entity"
            
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
            
        return classifications
