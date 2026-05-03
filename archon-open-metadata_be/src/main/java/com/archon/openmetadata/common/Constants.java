package com.archon.openmetadata.common;

public final class Constants {

    private Constants() {} // Prevent instantiation

    // Analysis Thresholds
    public static final double CANDIDATE_MIN_SCORE = 55.0;
    public static final double SEMANTIC_BOOST_MAX = 35.0;
    public static final double SUBSTRING_BOOST = 15.0;
    public static final double CARDINALITY_RATIO_THRESHOLD = 0.9;

    // Entity Classifications
    public static final String ENTITY_CONFIG = "Config";
    public static final String ENTITY_HISTORY = "History / Log";
    public static final String ENTITY_TEXT = "Text / Translation";
    public static final String ENTITY_MASTER = "Master Lookup";
    public static final String ENTITY_TRANSACTION_ITEM = "Transaction (Item)";
    public static final String ENTITY_TRANSACTION_HEADER = "Transaction (Header)";
    public static final String ENTITY_STANDARD = "Standard Entity";

    // Sensitive Categories
    public static final String SENSITIVE_FINANCIAL = "FINANCIAL";
    public static final String SENSITIVE_PII = "PII";
    public static final String SENSITIVE_PHI = "PHI";
    public static final String SENSITIVE_CREDENTIAL = "HIGH_RISK_CREDENTIAL";

    // Domain Groups
    public static final String DOMAIN_CUSTOMER = "Customer Data";
    public static final String DOMAIN_FINANCIAL = "Financial Data";
    public static final String DOMAIN_MISC = "Miscellaneous Data";
}
