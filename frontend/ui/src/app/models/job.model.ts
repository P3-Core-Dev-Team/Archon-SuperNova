export type SourceDbType = 'postgres' | 'mysql' | 'sqlserver' | 'oracle';

export interface JobRequest {
  label: string;
  db_type: SourceDbType;
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
  schema: string;
}

export type JobStatusValue = 'queued' | 'running' | 'succeeded' | 'failed';

export interface Job {
  job_id: string;
  label: string;
  schema_name: string;
  status: JobStatusValue;
  submitted_at: string;
  started_at?: string;
  ended_at?: string;
  current_phase?: string;
  progress?: Record<string, unknown>;
  error?: string;
  relationships_count?: number;
  pii_count?: number;
  cluster_count?: number;
}

export interface RelationshipNode {
  id: string;
  label: string;
  /** Edge degree (legacy field, retained for backwards compatibility). */
  value: number;
  /** Actual row count from ``tbl_inventory.row_count_estimate``.
   * Prefer this over ``value`` when displaying table weight. */
  row_count?: number;
}

export interface RelationshipEdge {
  from: string;
  to: string;
  label: string;
  containment: number | null;
  cardinality: string;
  confidence: number | null;
}

export interface RelationshipGraph {
  schema: string;
  nodes: RelationshipNode[];
  edges: RelationshipEdge[];
  total_edges: number;
  total_tables: number;
}

export interface PiiFinding {
  table_name: string;
  column_name: string;
  pii_type: string;
  detector: string;
  match_count: number;
  sample_count: number;
  match_rate: number;
  validated: boolean;
  name_prior: boolean;
  score: number | null;
  redacted_examples: unknown[];
  /** IIN/BIN-derived issuer breakdown for CC_NUMBER findings.  Empty
   * (or absent) for every other PII type.  Sorted by descending count. */
  provider_breakdown?: { brand: string; count: number; share: number }[];
  /** Regulation tags lifted from the PatternDef catalog
   * (``["PCI"]``, ``["GDPR", "CCPA"]``, etc.).  The UI uses this to
   * render group badges — e.g. all PCI cardholder-data findings
   * (CC_NUMBER, CARD_HOLDER_NAME, CARD_CVV) light up the same chip. */
  regulated?: string[];
}

export interface PiiTable {
  schema: string;
  findings: PiiFinding[];
  total: number;
}

/** One row from GET /api/jobs/{id}/data_quality. */
export interface DataQualityFinding {
  table_name: string;
  column_name: string;
  /** Stable string from discovery.data_quality.IssueType. */
  issue_type:
    | 'NULL_HEAVY'
    | 'ALL_NULL'
    | 'DUPLICATE_PK'
    | 'LEADING_TRAILING_WHITESPACE'
    | 'EMPTY_STRING'
    | 'MIXED_CASE'
    | 'LOW_CARDINALITY';
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  count: number;
  sample_rows: number;
  fraction: number;
  samples: string[];
}

export interface DataQualityResponse {
  schema: string;
  total: number;
  findings: DataQualityFinding[];
}

export interface JobSummary {
  job_id: string;
  schema_name: string;
  tables: number;
  rows_total: number;
  relationships_count: number;
  pii_findings_count: number;
  duration_seconds: number | null;
  phase_complete: string[];
  expected_fks?: number;
  matched_fks?: number;
  recall?: number | null;
  precision?: number | null;
}

export interface SchemaInfo {
  schema_name: string;
  table_count: number;
}

export interface SchemaList {
  source: {
    host: string;
    port: number;
    database: string;
    user: string;
  };
  schemas: SchemaInfo[];
  total: number;
}

// ERD card view (B2): column-level inventory for the dbdiagram-style ERD.
export interface ColumnInfo {
  table: string;
  column: string;
  ordinal: number;
  data_type: string;
  is_pk: boolean;
  is_fk: boolean;
  /** Null fraction in [0, 1] from sampled fingerprint pass.  Null
   * when the fingerprint phase didn't profile this column. */
  null_pct?: number | null;
}

export interface JobColumns {
  schema: string;
  tables: string[];
  columns: ColumnInfo[];
  total_columns: number;
  total_tables: number;
}

// Cluster-engine sprint (CL-3): cluster list + detail models.

export interface Cluster {
  cluster_id: number;
  name: string;
  table_count: number;
  intra_edges: number;
  inter_edges: number;
  archetype_distribution: Record<string, number>;
  modularity_contribution: number;
  pii_table_count: number;
  subject_kinds: string[];
}

export interface ClusterPairEdge {
  from: number;   // cluster_local_id
  to: number;     // cluster_local_id
  count: number;  // number of FK edges spanning this pair
}

export interface ClusterList {
  schema: string;
  total_clusters: number;
  modularity: number;
  junctions_collapsed: number;
  clusters: Cluster[];
  /** Macro cluster-graph: pair-counts of cross-cluster FK edges. */
  cluster_edges?: ClusterPairEdge[];
}

export interface ClusterMemberTable {
  table_id: number;
  table_name: string;
  row_count: number;
  archetype: string;
  subject_kinds: string[] | null;
}

export interface ClusterEdge {
  from: string;
  to: string;
  child_column: string;
  parent_column: string;
  confidence: number;
  cardinality: string;
}

export interface ClusterPiiFinding {
  table_name: string;
  column_name: string;
  pii_type: string;
  score: number;
  validated: boolean;
}

export interface ClusterBridgeTable {
  table_name: string;
  to_cluster_id: number | null;
  to_cluster_name: string;
}

export interface ClusterDetail {
  cluster_id: number;
  name: string;
  tables: ClusterMemberTable[];
  edges: ClusterEdge[];
  pii_findings: ClusterPiiFinding[];
  /** Tables OUTSIDE this cluster that have an FK edge to/from a member.
   *  Rendered as ghost cards in the cluster ERD. */
  bridge_tables: ClusterBridgeTable[];
  /** FK edges where exactly one endpoint is in this cluster (the other
   *  endpoint is a bridge_tables entry). */
  cross_cluster_edges: ClusterEdge[];
}
