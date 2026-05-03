class AppConstants:
    # Analysis Thresholds
    CANDIDATE_MIN_SCORE = 55.0
    SEMANTIC_BOOST_MAX = 35.0
    SUBSTRING_BOOST = 15.0
    CARDINALITY_RATIO_THRESHOLD = 0.9

    # Entity Classifications
    ENTITY_CONFIG = "Config"
    ENTITY_HISTORY = "History / Log"
    ENTITY_TEXT = "Text / Translation"
    ENTITY_MASTER = "Master Lookup"
    ENTITY_TRANSACTION_ITEM = "Transaction (Item)"
    ENTITY_TRANSACTION_HEADER = "Transaction (Header)"
    ENTITY_STANDARD = "Standard Entity"

    # Sensitive Categories
    SENSITIVE_FINANCIAL = "FINANCIAL"
    SENSITIVE_PII = "PII"
    SENSITIVE_PHI = "PHI"
    SENSITIVE_CREDENTIAL = "HIGH_RISK_CREDENTIAL"

    # Domain Groups
    DOMAIN_CUSTOMER = "Customer Data"
    DOMAIN_FINANCIAL = "Financial Data"
    DOMAIN_MISC = "Miscellaneous Data"

    PREDEFINED_DOMAINS = [
        "Sales", "Finance", "HR", "ERP", "SAP", "Customer Support", "Operations"
    ]
