from fastapi import APIRouter
from src.models import (
    BulkSchemaRequest,
    CardinalityRequest
)

# Import functional domains
from src.relationship_detection.candidate_matcher import CandidateMatcher
from src.relationship_detection.semantic_scorer import RelationshipScorer
from src.data_classification.entity_classifier import EntityClassifier
from src.data_grouping.domain_grouper import DomainGrouper
from src.sensitive_analysis.column_analyzer import SensitiveColumnAnalyzer
from src.sample_data_detection.cardinality_extractor import CardinalityExtractor
from src.sample_data_sensitive_analysis.data_analyzer import SampleDataSensitiveAnalyzer

router = APIRouter()
"""
    Stage 1 : Analyse the column and detect relationship
    1. Column datatype and fuzz score
    2. Semantic analysis 
"""
@router.post("/api/v1/metadata/detect-candidates")
def stage_candidate_detection(req: BulkSchemaRequest):
    candidates = CandidateMatcher.detect_candidates(req.tables, req.minValue, req.maxValue)
    return {"message": "Candidate detection complete", "candidates": candidates}

@router.post("/api/v1/metadata/score-relationships")
def stage_relationship_scoring(req: CandidateResponse):
    scored = RelationshipScorer.score_relationships(req.candidates, req.minValue, req.maxValue)
    return {"message": "Relationship scoring complete", "candidates": scored}

"""
    Stage 2 : Identifying the cardinality of the relationships
    1. Column count and target column coutn to analyze the relationship 
"""
@router.post("/api/v1/data/extract-cardinality")
def stage_cardinality(req: CardinalityRequest):
    relationships = CardinalityExtractor.extract_cardinality(req.connection, req.candidates, req.minValue, req.maxValue)
    return {"message": "Cardinality extraction complete", "relationships": relationships}

"""
    Stage 3 : Sensitive Analysis of a data
    1. Column sensitiveness based on risk and severity
"""
@router.post("/api/v1/metadata/detect-sensitive")
def stage_sensitive_detection(req: BulkSchemaRequest):
    sensitive_columns = SensitiveColumnAnalyzer.detect_sensitive_columns(req.tables, req.minValue, req.maxValue)
    return {"message": "Saved sensitive columns", "sensitive_columns": sensitive_columns}

"""
    Stage 4 : Table Clustering
    1. Table Grouping or clustering into one group to categorise
"""
@router.post("/api/v1/metadata/group-domains")
def stage_domain_grouping(req: BulkSchemaRequest):
    clusters = DomainGrouper.group_domains(req.tables, (req.schemaCrawlerRelationships or []) + (req.mlRelationships or []), req.minValue, req.maxValue)
    return {"message": "Saved domain grouping", "clusters": clusters}

@router.post("/api/v1/metadata/classify-entities")
def stage_entity_classification(req: BulkSchemaRequest):
    classifications = EntityClassifier.classify_entities(req.tables, (req.schemaCrawlerRelationships or []) + (req.mlRelationships or []), req.minValue, req.maxValue)
    return {"message": "Classification complete", "classifications": classifications}

"""
  Stage 5 : Sensitive Table Data Analysis
  1. Analysis the columns with sensitiveness against duck db validation into local
"""
@router.post("/api/v1/data/sensitive-analysis")
def stage_data_sensitive_analysis(req: dict):
    findings = SampleDataSensitiveAnalyzer.scan_sample_data(req.get("connection", {}), req.get("tables", []))
    return {"message": "Sample data analysis complete", "findings": findings}
