from fastapi import APIRouter

from src.models import (
    CardBrandRequest,
    CardinalityFilterRequest,
    CardinalityRefineRequest,
    ClusterRequest,
    ColumnTypeBatchRequest,
    CompositeFkRequest,
    DataQualityRequest,
    NamePairsRequest,
    PiiScanRequest,
    PolymorphicDetectRequest,
    PolymorphicMatchRequest,
    SchemaInsightRequest,
    TableNameListRequest,
)

# Import functional domains
from src.data_based.cardinality.cardinality_classifier import CardinalityClassifier
from src.data_based.clustering.louvain_clusterer import LouvainClusterer
from src.data_based.column_typing.column_type_classifier import ColumnTypeClassifier
from src.data_based.composite_fk.composite_fk_finder import CompositeFkFinder
from src.data_based.data_quality.data_quality_classifier import DataQualityClassifier
from src.data_based.exclusions.exclusion_filter import ExclusionFilter
from src.data_based.name_similarity.name_similarity_scorer import NameSimilarityScorer
from src.data_based.pii_detection.pii_scanner import PiiScanner
from src.data_based.polymorphic_fk.polymorphic_fk_finder import PolymorphicFkFinder
from src.data_based.schema_patterns.schema_pattern_detector import SchemaPatternDetector

router = APIRouter()

"""
    Stage 6 : Schema-design pattern detection
    1. Known-schema fingerprint (AdventureWorks / Northwind / Saleor / …)
    2. Temporal-tracking / CDC pattern
    3. Surrogate-key prevalence
    4. Bridge / junction table detection
    5. Subtype / supertype (polymorphic root) detection
"""
@router.post("/api/v1/data-based/match-known-schema")
def stage_match_known_schema(req: SchemaInsightRequest):
    names = req.tableNames or sorted({str(c.get("table", "")) for c in req.columns if c.get("table")})
    match = SchemaPatternDetector.match_known_schema(names)
    return {"message": "Known-schema fingerprint complete", "fingerprint": match}

@router.post("/api/v1/data-based/detect-temporal")
def stage_detect_temporal(req: SchemaInsightRequest):
    names = req.tableNames or sorted({str(c.get("table", "")) for c in req.columns if c.get("table")})
    out = SchemaPatternDetector.detect_temporal(req.columns, len(names))
    return {"message": "Temporal pattern detection complete", "temporal": out}

@router.post("/api/v1/data-based/surrogate-key-stats")
def stage_surrogate_key_stats(req: SchemaInsightRequest):
    out = SchemaPatternDetector.surrogate_key_stats(req.columns)
    return {"message": "Surrogate-key statistics complete", "surrogate_keys": out}

@router.post("/api/v1/data-based/detect-bridge-tables")
def stage_detect_bridge_tables(req: SchemaInsightRequest):
    out = SchemaPatternDetector.bridge_tables(req.columns, req.edges)
    return {"message": "Bridge-table detection complete", "bridge_tables": out}

@router.post("/api/v1/data-based/detect-polymorphic-roots")
def stage_detect_polymorphic_roots(req: SchemaInsightRequest):
    out = SchemaPatternDetector.subtype_supertype(req.edges)
    return {"message": "Polymorphic-root detection complete", "polymorphic_roots": out}

@router.post("/api/v1/data-based/schema-insights")
def stage_schema_insights(req: SchemaInsightRequest):
    """Bundled report — runs every schema-pattern detector in one call.
    Convenience endpoint for the UI's schema-insights panel."""
    bundle = SchemaPatternDetector.detect_all(
        columns=req.columns,
        edges=req.edges,
        table_names=req.tableNames,
    )
    return {"message": "Schema insights complete", **bundle}

"""
    Stage 7 : Data-quality classification
    1. Per-column null density / all-null
    2. Single-column primary-key duplicates
    3. Whitespace + empty-string findings
    4. Mixed-case collisions
    5. Low-cardinality flags
"""
@router.post("/api/v1/data-based/classify-data-quality")
def stage_classify_data_quality(req: DataQualityRequest):
    findings = DataQualityClassifier.classify_batch(
        rows=req.rows,
        null_threshold=req.nullThreshold,
        low_card_floor=req.lowCardFloor,
        low_card_min_rows=req.lowCardMinRows,
    )
    return {"message": "Data-quality classification complete", "findings": findings}

"""
    Stage 8 : Exclusion filtering — drop logs / temp / backup tables
              before any heavy analysis runs.
"""
@router.post("/api/v1/data-based/filter-exclusions")
def stage_filter_exclusions(req: TableNameListRequest):
    out = ExclusionFilter.filter_tables(req.tableNames)
    return {"message": "Exclusion filtering complete", **out}

"""
    Stage 9 : Column-type classification (Postgres data_type → TypeClass)
              and FK-eligibility predicate.
"""
@router.post("/api/v1/data-based/classify-column-types")
def stage_classify_column_types(req: ColumnTypeBatchRequest):
    out = ColumnTypeClassifier.classify_batch(req.columns)
    return {"message": "Column-type classification complete", "columns": out}

"""
    Stage 10 : Hybrid lex + semantic name similarity.
"""
@router.post("/api/v1/data-based/score-name-similarity")
def stage_score_name_similarity(req: NamePairsRequest):
    out = NameSimilarityScorer.score_pairs(req.pairs)
    return {"message": "Name-similarity scoring complete", "pairs": out}

"""
    Stage 11 : Bayesian PII detection — regex catalogue + name priors +
               validators + IIN/BIN brand classification.
"""
@router.post("/api/v1/data-based/scan-pii")
def stage_scan_pii(req: PiiScanRequest):
    findings = PiiScanner.scan_columns(
        columns=req.columns,
        enable_ner=req.enableNer,
        max_examples=req.maxExamples,
    )
    return {"message": "PII scan complete", "findings": findings}

@router.post("/api/v1/data-based/card-brand")
def stage_card_brand(req: CardBrandRequest):
    return {"message": "Card-brand classification complete", "brand": PiiScanner.card_brand(req.pan)}

"""
    Stage 12 : Weighted Louvain clustering with semantic merge +
               zero-shot domain labelling.  Deterministic (seed=42).
"""
@router.post("/api/v1/data-based/cluster-tables")
def stage_cluster_tables(req: ClusterRequest):
    out = LouvainClusterer.cluster(
        schema_name=req.schemaName,
        tables=req.tables,
        columns=req.columns,
        edges=req.edges,
        pii_findings=req.piiFindings,
        confidence_floor=req.confidenceFloor,
        seed=req.seed,
        semantic_merge_enabled=req.semanticMergeEnabled,
        semantic_merge_threshold=req.semanticMergeThreshold,
        semantic_label_enabled=req.semanticLabelEnabled,
        semantic_label_threshold=req.semanticLabelThreshold,
    )
    return {"message": "Clustering complete", **out}

"""
    Stage 13 : Cardinality refinement — eligibility filter + classifier.
               Caller owns the live source-DB probe.
"""
@router.post("/api/v1/data-based/cardinality-eligible")
def stage_cardinality_eligible(req: CardinalityFilterRequest):
    out = CardinalityClassifier.filter_eligible(req.relationships, req.confidenceFloor)
    return {"message": "Cardinality eligibility complete", "eligible": out}

@router.post("/api/v1/data-based/cardinality-refine")
def stage_cardinality_refine(req: CardinalityRefineRequest):
    out = CardinalityClassifier.refine_batch(req.rows)
    return {"message": "Cardinality refinement complete", "verdicts": out}

"""
    Stage 14 : Composite (multi-column) FK proposal generation.
"""
@router.post("/api/v1/data-based/propose-composite-fks")
def stage_propose_composite_fks(req: CompositeFkRequest):
    proposals = CompositeFkFinder.propose(
        singles=req.singles,
        max_arity=req.maxArity,
        min_singles_containment=req.minSinglesContainment,
        min_name_similarity_floor=req.minNameSimilarityFloor,
    )
    return {"message": "Composite-FK proposals complete", "proposals": proposals}

"""
    Stage 15 : Polymorphic FK detection (Rails / Django ``<x>_type``
               + ``<x>_id`` discriminator pattern).
"""
@router.post("/api/v1/data-based/detect-polymorphic-columns")
def stage_detect_polymorphic_columns(req: PolymorphicDetectRequest):
    out = PolymorphicFkFinder.detect_columns(req.columns)
    return {"message": "Polymorphic-column detection complete", "columns": out}

@router.post("/api/v1/data-based/match-polymorphic-partitions")
def stage_match_polymorphic_partitions(req: PolymorphicMatchRequest):
    out = PolymorphicFkFinder.match_partitions(req.typeColumnValues, req.candidateParents)
    return {"message": "Polymorphic-partition matching complete", **out}
