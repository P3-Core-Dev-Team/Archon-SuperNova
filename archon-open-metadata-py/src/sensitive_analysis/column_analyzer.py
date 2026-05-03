import re
from src.models import SchemaTable

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

class SensitiveColumnAnalyzer:
    """
    Metadata layer PII detection. Uses Presidio and NLP to detect
    sensitive columns strictly based on their physical name/metadata.
    """
    @staticmethod
    def detect_sensitive_columns(tables: list[SchemaTable]) -> list[dict]:
        sensitive_columns = []
        for t in tables:
            for col_obj in t.columns:
                if presidio_layer:
                    res = presidio_layer.analyze(text=str(col_obj.columnName).replace("_", " "), language='en')
                    if len(res) > 0:
                        for r in res:
                            sensitive_columns.append({
                                "transientTableName": t.tableName,
                                "columnName": col_obj.columnName,
                                "category": r.entity_type
                            })
                else:
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
        return sensitive_columns
