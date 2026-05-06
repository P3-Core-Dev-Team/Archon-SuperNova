from dataclasses import asdict
from typing import Optional

from .clustering_core import cluster_schema as _cluster_schema


class LouvainClusterer:
    """
    Stage 12: Weighted Louvain modularity clustering with junction-table
    collapse, semantic merge, archetype tagging (JUNCTION/LOOKUP/FACT/
    DIMENSION/AUDIT) and zero-shot domain labelling.  Pure: takes
    inventory/relationships/PII rows in memory and returns a serialised
    ClusteringResult.  No DB IO — caller persists.
    """

    @staticmethod
    def cluster(
        schema_name: str,
        tables: list[dict],
        columns: list[dict],
        edges: list[dict],
        pii_findings: Optional[list[dict]] = None,
        confidence_floor: float = 0.7,
        seed: int = 42,
        semantic_merge_enabled: bool = True,
        semantic_merge_threshold: float = 0.65,
        semantic_merge_modularity_floor: float = 0.95,
        semantic_label_enabled: bool = True,
        semantic_label_threshold: float = 0.55,
    ) -> dict:
        result = _cluster_schema(
            schema_name=schema_name,
            tables=tables,
            columns=columns,
            edges=edges,
            pii_findings=pii_findings or [],
            confidence_floor=confidence_floor,
            seed=seed,
            semantic_merge_enabled=semantic_merge_enabled,
            semantic_merge_threshold=semantic_merge_threshold,
            semantic_merge_modularity_floor=semantic_merge_modularity_floor,
            semantic_label_enabled=semantic_label_enabled,
            semantic_label_threshold=semantic_label_threshold,
        )
        return {
            "clusters": [
                {
                    **asdict(c),
                    # Tuples become lists naturally via asdict; nothing else needed.
                } for c in result.clusters
            ],
            "table_assignments": [asdict(a) for a in result.table_assignments],
            "junction_collapsed": list(result.junction_collapsed),
            "modularity_score": result.modularity_score,
            "edge_count_post_collapse": result.edge_count_post_collapse,
        }
