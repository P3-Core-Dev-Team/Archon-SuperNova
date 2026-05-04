package com.archon.openmetadata.job.models;

import lombok.AllArgsConstructor;

import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@AllArgsConstructor
@NoArgsConstructor
public enum OperationType {

    RELATIONSHIP_DETECTION("Relationship Detection"),
    GRAPH_BUILDING_DETECTION("Graph Building Detection"),
    CANDIDATE_FUZZY_MATCHING("Candidate with fuzzy matching"),
    SEMANTIC_ANALYSIS("Semantic Analysis"),
    CARDINALITY_DETECTION_SOURCE_COUNT("Cardinality Detection with source count"),
    TABLE_DOMAIN_GROUPING("Table domain grouping"),
    DATA_CLASSIFICATION_TABLE_TYPE("Data Classification with table type grouping"),
    SENSITIVE_ANALYSIS_TABLE_DATA("Sensitive analysis with table data");

    private String description;
}