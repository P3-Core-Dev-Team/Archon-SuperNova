"""
clustering.py - Cluster-engine algorithmic core for the Discovery pipeline.

This module is the *pure* core of the cluster engine: it consumes plain
list-of-dicts inputs (rows from ``tbl_inventory``, ``col_inventory``,
``relationships``, ``pii_findings``) and returns frozen dataclasses describing
the clusters.  No database writes; no orchestrator wiring; no I/O.  The DB
persistence layer (CL-2) calls :func:`cluster_schema` and is responsible for
turning the returned :class:`ClusteringResult` into rows in
``tbl_clusters`` / ``cluster_membership``.

Algorithm overview
------------------
The pipeline runs per-schema and follows Council-1's accepted recommendation:

1.  **Build a column-level edge list** from ``relationships`` filtered to the
    target schema and to ``confidence >= confidence_floor``.
2.  **Project edges to the table level**, summing weights when multiple FK
    column pairs link the same table pair (standard Louvain convention).
3.  **Tag every table** with an archetype - JUNCTION / LOOKUP / FACT /
    DIMENSION / AUDIT - using row-count, degree, PK, and naming heuristics.
4.  **Junction-collapse**: for every JUNCTION node, drop the node from the
    graph and emit a synthetic MANY_TO_MANY edge between its two
    MANY_TO_ONE parents whose weight is ``min(left_edge, right_edge)``.
    The cardinality_factor is *not* re-applied (already baked in to the
    parent edges).
5.  **Compute final edge weights** per the formula::

        weight = confidence
                 * cardinality_factor                         # 1.0 / 0.3
                 + 0.15 if same schema                         # always 1 here
                 + 0.10 if both endpoints share a PII type     # via pii_findings

    NB: under :func:`cluster_schema` every edge is intra-schema, so the
    schema_bonus is constant.  It is included for future cross-schema use.
6.  **Weighted Louvain modularity** via :func:`networkx.algorithms.community.
    louvain_communities` (NetworkX 3.x; ``python-louvain`` is *not* required).
    Deterministic ``seed=42`` is threaded through.
7.  **Cluster naming** uses a four-rule cascade:

       Rule 1: shared non-`public` schema -> use the schema verbatim.
       Rule 2: anchor table (highest weighted-degree FACT/DIMENSION) ->
                ``<table_singular>_cluster`` (strips trailing 's').
       Rule 3: lexical prefix - if >=60% of members share token-1 ->
                ``<token-1>_cluster``.
       Rule 4: ``cluster_<id>``.

    NB: because :func:`cluster_schema` is single-schema, Rule 1 fires for any
    non-public input and the rest only run when ``schema_name == 'public'``.
    Tests exercising rules 2-4 must therefore use ``schema_name='public'``.
8.  **Junction reattachment**: collapsed JUNCTION tables are placed in the
    cluster of their *dominant* parent - the parent with the higher
    post-collapse weighted degree; ties are broken by smaller ``table_id``.
9.  **Modularity decomposition**: total modularity comes from
    :func:`networkx.algorithms.community.modularity`; per-cluster contribution
    is computed manually as ``Q_c = sigma_in/(2m) - (sigma_tot/(2m))**2``.

PII bonus source
----------------
The spec parameter ``pii_findings`` is the source of record.  We build
``table_id -> set[pii_type]`` by grouping pii_findings rows.  ``subject_kinds``
on ``tbl_inventory`` is *not* consulted because it is not part of the
function input contract.

Complexity
----------
Louvain is O(E log V) per pass, with a constant number of passes in practice.
All other steps are O(V + E).

Determinism
-----------
``seed=42`` is the only source of randomness used by Louvain; archetype
tagging, naming, and modularity are deterministic.  Iteration over dicts
relies on Python's insertion-ordered dict (3.7+) plus explicit ``sorted()``
calls at every aggregation point so a re-run on the same input produces the
same clusters.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import networkx as nx
from networkx.algorithms.community import louvain_communities, modularity


# ---------------------------------------------------------------------------
# Public dataclasses (consumed by CL-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusteredTable:
    """One table's cluster assignment plus its archetype."""

    table_id: int
    table_name: str
    schema_name: str
    cluster_id: int
    archetype: str  # FACT | DIMENSION | LOOKUP | JUNCTION | AUDIT | EMPTY


@dataclass(frozen=True)
class Cluster:
    """One cluster: name, members, edge stats, modularity contribution.

    ``semantic_label`` is the optional zero-shot business-domain tag
    (e.g. ``"Sales"``, ``"Customer Management"``) attached by the
    hybrid clustering pass when the cluster's centroid embedding scores
    above ``RelationshipsConfig.semantic_label_threshold`` against the
    fixed vocabulary in ``domain_vocab.DOMAINS``.  ``None`` for clusters
    that didn't reach the threshold or when the SentenceTransformer
    model is unavailable (the pass silently skips in that case).
    """

    cluster_id: int
    name: str
    schema_name: str | None
    table_ids: tuple[int, ...]
    archetype_distribution: dict[str, int]
    intra_edge_count: int
    inter_edge_count: int
    modularity_contribution: float
    semantic_label: str | None = None


@dataclass(frozen=True)
class ClusteringResult:
    """Container returned by :func:`cluster_schema`."""

    clusters: tuple[Cluster, ...]
    table_assignments: tuple[ClusteredTable, ...]
    junction_collapsed: tuple[int, ...]
    modularity_score: float
    edge_count_post_collapse: int


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------


_CARDINALITY_FACTOR: dict[str, float] = {
    "ONE_TO_ONE": 1.0,
    "ONE_TO_MANY": 1.0,
    "MANY_TO_ONE": 1.0,
    "MANY_TO_MANY": 0.3,
}
_DEFAULT_CARDINALITY_FACTOR = 1.0

_SCHEMA_BONUS = 0.15
_PII_BONUS = 0.10

_AUDIT_NAME_RE = re.compile(r"(audit|history|log|change|event)", re.IGNORECASE)
# Audit-style suffix columns used when classifying junctions: a junction may
# carry only a PK and these "noise" columns and still count as a junction.
_AUDIT_COL_RE = re.compile(
    r"^(created_at|updated_at|created_by|updated_by|deleted_at|"
    r"created_on|updated_on|last_update|last_updated|last_modified|"
    r"version|row_version|etl_loaded_at)$",
    re.IGNORECASE,
)

_LOOKUP_ROW_CAP = 100


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _column_to_table(columns: list[dict]) -> dict[int, int]:
    """Build ``column_id -> table_id`` from ``col_inventory`` rows."""
    return {int(c["column_id"]): int(c["table_id"]) for c in columns}


def _pk_column_ids(columns: list[dict]) -> set[int]:
    """Return the set of column_ids that are declared or implicit PKs."""
    out: set[int] = set()
    for c in columns:
        if bool(c.get("is_pk")) or bool(c.get("is_implicit_pk")):
            out.add(int(c["column_id"]))
    return out


def _table_pii_types(
    pii_findings: list[dict],
    col_to_table: dict[int, int],
) -> dict[int, set[str]]:
    """
    Group ``pii_findings`` to ``table_id -> {pii_type, ...}``.

    pii_findings rows may carry ``table_id`` directly (table-level findings
    such as IDENTITY_BUNDLE) or only ``column_id`` (column-level findings,
    where the schema leaves ``table_id`` NULL).  This helper resolves the
    column-level case via the supplied ``col_to_table`` map so callers don't
    need to denormalise.
    """
    out: dict[int, set[str]] = defaultdict(set)
    for f in pii_findings:
        tid = f.get("table_id")
        if tid is None:
            cid = f.get("column_id")
            if cid is not None:
                tid = col_to_table.get(int(cid))
        if tid is None:
            continue
        ptype = f.get("pii_type")
        if not ptype:
            continue
        out[int(tid)].add(str(ptype))
    return dict(out)


def _row_count(table: dict) -> int:
    """Coerce ``row_count_estimate`` to int (None -> 0)."""
    rc = table.get("row_count_estimate")
    if rc is None:
        return 0
    try:
        return int(rc)
    except (TypeError, ValueError):
        return 0


def _columns_by_table(columns: list[dict]) -> dict[int, list[dict]]:
    """Group ``col_inventory`` rows by ``table_id``."""
    out: dict[int, list[dict]] = defaultdict(list)
    for c in columns:
        out[int(c["table_id"])].append(c)
    return dict(out)


# ---------------------------------------------------------------------------
# Edge weight + table-edge projection
# ---------------------------------------------------------------------------


def _edge_weight(
    confidence: float,
    cardinality: str,
    same_schema: bool,
    shared_pii: bool,
) -> float:
    """Compute the weight per Council-1's formula."""
    cf = _CARDINALITY_FACTOR.get(cardinality, _DEFAULT_CARDINALITY_FACTOR)
    w = float(confidence) * cf
    if same_schema:
        w += _SCHEMA_BONUS
    if shared_pii:
        w += _PII_BONUS
    return w


def _project_edges(
    edges: list[dict],
    *,
    col_to_table: dict[int, int],
    schema_tables: set[int],
    table_pii: dict[int, set[str]],
    confidence_floor: float,
) -> tuple[
    list[tuple[int, int, float, str]],  # (parent_tid, child_tid, weight, cardinality)
    dict[tuple[int, int], list[dict]],   # (parent_tid, child_tid) -> raw edge rows
]:
    """
    Project column-level ``relationships`` rows to table-level edges.

    Returns the table edge list plus a mapping from each (parent_tid, child_tid)
    to the underlying raw rows (used by junction detection so we can read the
    original column-level cardinality / parent column).

    Weights for multiple column pairs linking the same (parent_tid, child_tid)
    are summed - the standard Louvain convention.
    """
    # Aggregate to (parent, child) directed pairs.  We keep parent->child
    # direction since junction detection cares about MANY_TO_ONE outbound
    # edges.  When we hand the graph to NetworkX we'll use Graph (undirected)
    # so summing over directions still yields the right Louvain weight.
    #
    # We track (per-cardinality-group weight) so we can identify the *dominant*
    # cardinality for junction detection, while still emitting the *total*
    # weight (summed across cardinality groups) for Louvain.  This avoids
    # discarding column-edges whose cardinality differs from the heaviest
    # group on the same table-pair.
    bucket_w: dict[tuple[int, int, str], float] = defaultdict(float)
    bucket_rows: dict[tuple[int, int], list[dict]] = defaultdict(list)

    for e in edges:
        conf = e.get("confidence")
        if conf is None or float(conf) < confidence_floor:
            continue

        cccol = e.get("child_col_id")
        pcol = e.get("parent_col_id")
        if cccol is None or pcol is None:
            continue

        ctid = col_to_table.get(int(cccol))
        ptid = col_to_table.get(int(pcol))
        if ctid is None or ptid is None:
            continue
        if ctid == ptid:
            # Self-loop: skip (a self-FK is a soft hierarchy that adds no
            # cluster signal and breaks Louvain's degree calc).
            continue
        if ctid not in schema_tables or ptid not in schema_tables:
            continue

        cardinality = str(e.get("cardinality") or "")
        shared_pii = bool(
            table_pii.get(ctid, set()) & table_pii.get(ptid, set())
        )
        w = _edge_weight(
            confidence=float(conf),
            cardinality=cardinality,
            same_schema=True,  # always intra-schema in this entry point
            shared_pii=shared_pii,
        )
        bucket_w[(ptid, ctid, cardinality)] += w
        bucket_rows[(ptid, ctid)].append(e)

    # Collapse cardinality dimension: total weight is the *sum* across
    # cardinality groups; the cardinality emitted is the dominant (heaviest)
    # group.  Sum-of-weights is the standard Louvain convention for parallel
    # edges.
    sum_w: dict[tuple[int, int], float] = defaultdict(float)
    dom_w: dict[tuple[int, int], tuple[float, str]] = {}
    for (ptid, ctid, cardinality), w in bucket_w.items():
        sum_w[(ptid, ctid)] += w
        prev = dom_w.get((ptid, ctid))
        if prev is None or w > prev[0]:
            dom_w[(ptid, ctid)] = (w, cardinality)

    out_edges = sorted(
        (
            (ptid, ctid, sum_w[(ptid, ctid)], dom_w[(ptid, ctid)][1])
            for (ptid, ctid) in sum_w.keys()
        ),
        key=lambda x: (x[0], x[1]),
    )
    return out_edges, dict(bucket_rows)


# ---------------------------------------------------------------------------
# Archetype tagging
# ---------------------------------------------------------------------------


def _archetype_for(
    *,
    table: dict,
    cols: list[dict],
    out_edges: list[tuple[int, int, float, str]],  # outbound: this table is child
    in_edges: list[tuple[int, int, float, str]],   # inbound: this table is parent
    pk_col_ids: set[int],
    fk_col_ids: set[int],
    parent_row_counts: list[int],
    fact_row_threshold: int,
    schema_median_rows: float,
) -> str:
    """
    Tag a single table per Council-1 typology rules.

    Order of evaluation matters:
      1. JUNCTION  - very narrow, two MANY_TO_ONE PK outbounds.
      2. LOOKUP    - small, fan-in many.
      3. FACT      - large with many MANY_TO_ONE outbounds.
      4. AUDIT     - regex name + at least one FACT/DIM inbound.
      5. fallback  - FACT/DIM by row count vs schema median.
    """
    name = str(table.get("table_name") or "")
    row_count = _row_count(table)

    # ----- JUNCTION ----------------------------------------------------
    n_cols = len(cols)
    n_out = len(out_edges)
    n_in = len(in_edges)
    is_junction = False
    if n_cols <= 4 and n_out >= 2:
        # All outbound edges are MANY_TO_ONE OR ONE_TO_ONE (composite-PK FKs
        # are tagged 1:1 by the cardinality detector even though they're the
        # M:N junction-shape edge — see review note from CL-1).
        all_join_card = all(
            card in ("MANY_TO_ONE", "ONE_TO_ONE")
            for (_, _, _, card) in out_edges
        )
        # ...from PK columns.  We approximate "from PK" by checking whether
        # this table's PK columns are involved in the underlying child_col.
        has_pk = any(
            int(c["column_id"]) in pk_col_ids for c in cols
        )
        # No descriptive non-PK columns (only PK + FK + audit).  Catches
        # classic junctions (`order_id`, `product_id`, optional `created_at`)
        # while excluding tables that carry payload data alongside the FKs.
        # Row-count signal removed (post-review): sparse M:M junctions like
        # `special_offer_product` (115 rows ≪ products 500) failed the old
        # `row_count > max(parent_row_counts)` predicate universally.  The
        # narrow-shape + clean-descriptive predicates are sufficient.
        non_pk_descriptive = [
            c for c in cols
            if int(c["column_id"]) not in pk_col_ids
            and int(c["column_id"]) not in fk_col_ids
            and not _AUDIT_COL_RE.match(str(c.get("column_name") or ""))
        ]
        descriptive_clean = len(non_pk_descriptive) == 0
        if all_join_card and has_pk and descriptive_clean:
            is_junction = True
    if is_junction:
        return "JUNCTION"

    # ----- LOOKUP ------------------------------------------------------
    if row_count > 0 and row_count < _LOOKUP_ROW_CAP and n_in >= 3 and n_out <= 1:
        return "LOOKUP"

    # ----- FACT (top quartile + many MANY_TO_ONE outbounds) ----------
    n_mto_out = sum(1 for (_, _, _, c) in out_edges if c == "MANY_TO_ONE")
    if row_count >= fact_row_threshold and n_mto_out >= 3:
        return "FACT"

    # ----- DIMENSION (middle band) -----------------------------------
    # We say "middle" = strictly less than top quartile; AND >=1 inbound; AND
    # <=2 outbound.
    if (
        row_count < fact_row_threshold
        and n_in >= 1
        and n_out <= 2
        and row_count >= schema_median_rows / 4
    ):
        # ----- AUDIT check (subsumes some DIMENSIONs) ----------------
        if _AUDIT_NAME_RE.search(name) and n_in >= 1:
            return "AUDIT"
        return "DIMENSION"

    # ----- AUDIT (last-chance regex match) ---------------------------
    if _AUDIT_NAME_RE.search(name) and n_in >= 1:
        return "AUDIT"

    # ----- Fallback: FACT/DIM by size --------------------------------
    if row_count >= schema_median_rows:
        return "FACT"
    return "DIMENSION"


def _quartile_threshold(values: Iterable[int], q: float) -> float:
    """Return the q-th quantile of *values* (linear interpolation)."""
    arr = sorted(int(v) for v in values if v is not None)
    if not arr:
        return 0.0
    if q <= 0:
        return float(arr[0])
    if q >= 1:
        return float(arr[-1])
    pos = q * (len(arr) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(arr) - 1)
    frac = pos - lo
    return arr[lo] + (arr[hi] - arr[lo]) * frac


# ---------------------------------------------------------------------------
# Junction collapse
# ---------------------------------------------------------------------------


def _collapse_junctions(
    *,
    junction_tids: set[int],
    table_edges: list[tuple[int, int, float, str]],
    edge_rows: dict[tuple[int, int], list[dict]],
) -> tuple[list[tuple[int, int, float, str]], dict[int, tuple[int, int]]]:
    """
    Drop JUNCTION nodes from the table-edge list and emit synthetic
    MANY_TO_MANY edges between each JUNCTION's two parents.

    Returns the rewritten edge list plus a map ``junction_tid -> (p1, p2)``
    used later to re-attach junctions to the dominant parent's cluster.
    """
    # For every junction, its OUT edges (parent_tid -> junction_tid where
    # junction is the *child* in our directed bucket) capture the M:1 parents.
    # In the project_edges bucket we stored (ptid, ctid) so junction's outs are
    # rows with ctid == junction.
    junction_parents: dict[int, list[tuple[int, float]]] = defaultdict(list)
    edges_kept: list[tuple[int, int, float, str]] = []

    for (ptid, ctid, w, card) in table_edges:
        if ctid in junction_tids:
            junction_parents[ctid].append((ptid, w))
            continue
        if ptid in junction_tids:
            # In rare cases the junction also has inbound edges (other tables
            # FK into it).  Drop them too - they're junk for clustering.
            continue
        edges_kept.append((ptid, ctid, w, card))

    junction_to_parents: dict[int, tuple[int, int]] = {}
    synthetic: list[tuple[int, int, float, str]] = []
    for jtid, parents in junction_parents.items():
        # Need exactly two distinct parents to emit a synthetic M:M edge.
        # If a junction has more than two we collapse pairwise on the two with
        # highest weight (rare case; documented).
        parents_sorted = sorted(parents, key=lambda x: (-x[1], x[0]))
        unique: list[tuple[int, float]] = []
        seen: set[int] = set()
        for ptid, w in parents_sorted:
            if ptid in seen:
                continue
            seen.add(ptid)
            unique.append((ptid, w))
        if len(unique) < 2:
            continue
        p1, w1 = unique[0]
        p2, w2 = unique[1]
        a, b = sorted((p1, p2))  # canonical order
        synthetic.append((a, b, min(w1, w2), "MANY_TO_MANY"))
        junction_to_parents[jtid] = (a, b)

    return edges_kept + synthetic, junction_to_parents


# ---------------------------------------------------------------------------
# Cluster naming
# ---------------------------------------------------------------------------


def _singularise(token: str) -> str:
    """Trim a single trailing 's' to make a table name singular-ish."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _semantic_merge(
    communities: list[set[int]],
    G: "nx.Graph",
    table_by_id: dict[int, dict],
    *,
    threshold: float,
    modularity_floor: float,
) -> list[set[int]]:
    """Merge Louvain communities that are semantically similar AND
    structurally connected.

    Three-step pass on top of ``louvain_communities``:

    1. Compute a centroid embedding per community by averaging
       :func:`name_similarity._embed` over each member's ``table_name``.
       Communities with zero embeddable members are skipped.
    2. For each PAIR of communities ``(c_i, c_j)`` connected by at
       least one inter-community edge in ``G``, compute the cosine
       similarity between centroids.
    3. Greedily merge pairs whose similarity ≥ ``threshold`` AND whose
       merge keeps the global modularity within ``modularity_floor`` of
       the pre-merge value (typically 0.95 ≡ tolerate ≤5% drop).
       Pairs are processed in descending similarity order so the
       strongest semantic links are tested first.

    Returns the post-merge community list.  The original Louvain output
    is returned unchanged when:

    * the SentenceTransformer model isn't available (semantic
      similarity returns ``None`` for every pair),
    * fewer than two communities exist, or
    * no pair clears both the threshold and the modularity guard.

    No DB writes, no logging — pure function so the test suite can
    monkey-patch ``_embed`` deterministically.
    """
    # Guard rails ---------------------------------------------------
    if threshold <= 0.0 or len(communities) < 2:
        return communities

    # Lazy-import to avoid forcing sentence-transformers on every
    # caller of this module (and to give name_similarity a chance to
    # flip SEMANTIC_AVAILABLE on first contact).
    try:
        from discovery.name_similarity import _embed
        import numpy as np
    except ImportError:
        return communities

    # Step 1 — centroid per community.  Skip communities with no
    # embeddable member; their centroid would be undefined.
    centroids: dict[int, "np.ndarray"] = {}
    for idx, members in enumerate(communities):
        vecs = []
        for tid in members:
            tname = table_by_id.get(tid, {}).get("table_name")
            if not tname:
                continue
            v = _embed(str(tname))
            if v is not None:
                vecs.append(v)
        if vecs:
            centroids[idx] = np.mean(vecs, axis=0)

    if len(centroids) < 2:
        return communities  # not enough to merge

    # Step 2 — pre-compute community membership lookup so we can
    # detect inter-community edges in O(1) per edge.
    member_to_idx: dict[int, int] = {}
    for idx, members in enumerate(communities):
        for tid in members:
            member_to_idx[tid] = idx

    inter_pairs: dict[tuple[int, int], int] = {}  # (i, j)<->edge_count
    for u, v in G.edges():
        a = member_to_idx.get(u)
        b = member_to_idx.get(v)
        if a is None or b is None or a == b:
            continue
        if a not in centroids or b not in centroids:
            continue
        key = (a, b) if a < b else (b, a)
        inter_pairs[key] = inter_pairs.get(key, 0) + 1

    if not inter_pairs:
        return communities

    # Step 3 — score each candidate pair, sort by similarity DESC,
    # greedily merge while the modularity guard holds.
    def _cos(a, b) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom <= 0.0:
            return 0.0
        return float(np.dot(a, b) / denom)

    candidates: list[tuple[float, int, int, int]] = []  # (sim, i, j, edge_count)
    for (i, j), edge_count in inter_pairs.items():
        sim = _cos(centroids[i], centroids[j])
        if sim >= threshold:
            candidates.append((sim, i, j, edge_count))
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    if not candidates:
        return communities

    # Modularity baseline.
    base_mod: float
    try:
        base_mod = float(modularity(G, communities, weight="weight"))
    except Exception:
        base_mod = 0.0
    # If base_mod is non-positive, the guard collapses to "any merge that
    # doesn't make modularity worse than zero" — practically always
    # accepts.  That's fine; the threshold itself is the gate.
    floor = base_mod * modularity_floor if base_mod > 0 else float("-inf")

    # Disjoint-set / union-find so a chain of acceptable merges
    # collapses correctly.
    parent = list(range(len(communities)))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _materialise() -> list[set[int]]:
        groups: dict[int, set[int]] = {}
        for idx, members in enumerate(communities):
            root = _find(idx)
            groups.setdefault(root, set()).update(members)
        return list(groups.values())

    merged_count = 0
    for sim, i, j, _edges in candidates:
        ri, rj = _find(i), _find(j)
        if ri == rj:
            continue  # already merged via a previous candidate
        # Tentatively union and re-evaluate modularity.
        parent[ri] = rj
        try:
            trial_mod = float(modularity(G, _materialise(), weight="weight"))
        except Exception:
            trial_mod = base_mod  # leave alone on numeric quirks
        if trial_mod >= floor:
            merged_count += 1
        else:
            # Revert this merge.
            parent[ri] = ri

    if merged_count == 0:
        return communities
    return _materialise()


def _zero_shot_label(
    member_tables: list[dict],
    *,
    threshold: float,
) -> str | None:
    """Pick the closest business-domain term for one cluster.

    Embeds the cluster's table-name list (concatenated) and each
    vocabulary term, returns the term with the highest cosine
    similarity provided it clears ``threshold``.  ``None`` when the
    model is unavailable, the cluster has no embeddable tables, or no
    term clears the threshold.

    Vocabulary is :data:`discovery.domain_vocab.DOMAINS` — fourteen
    English business-domain entries with synonym-expanded search text.
    """
    if threshold <= 0.0 or not member_tables:
        return None
    try:
        from discovery.domain_vocab import DOMAINS
        from discovery.name_similarity import _embed
        import numpy as np
    except ImportError:
        return None

    # Build a single embedding from the cluster's most-connected members
    # (capped at 6 to avoid drowning the signal with long tails).
    names = [str(t.get("table_name", "")) for t in member_tables[:6] if t.get("table_name")]
    if not names:
        return None
    name_vec = _embed(" ".join(names))
    if name_vec is None:
        return None

    best_label: str | None = None
    best_sim = threshold  # only beat the threshold counts
    for label, search_text in DOMAINS:
        v = _embed(search_text)
        if v is None:
            continue
        denom = float(np.linalg.norm(name_vec) * np.linalg.norm(v))
        if denom <= 0.0:
            continue
        sim = float(np.dot(name_vec, v) / denom)
        if sim > best_sim:
            best_sim = sim
            best_label = label
    return best_label


def _name_cluster(
    *,
    cluster_id: int,
    member_tables: list[dict],
    schema_name: str,
    archetypes: dict[int, str],
    weighted_degree: dict[int, float],
) -> tuple[str, str | None]:
    """
    Apply the naming cascade.  Returns (cluster_name, schema_name_or_none).

    Post-review fix: when ``cluster_schema`` is called per-schema (the typical
    case), every cluster trivially shares the schema, so the original Rule 1
    ("non-public schema → schema name verbatim") collapsed every cluster onto
    the same name.  We now run Rule 1 ONLY when the cluster spans multiple
    schemas (e.g. a future cross-schema invocation).  In single-schema mode
    the cascade falls through to Rule 2 (anchor table) → Rule 3 (lexical
    prefix) → Rule 4 (cluster_<id>) so distinct subject areas get distinct
    names like ``customer_cluster``, ``product_cluster``, ``employee_cluster``.
    """
    schemas = {str(t["schema_name"]) for t in member_tables}
    only_schema = next(iter(schemas)) if len(schemas) == 1 else None

    # Rule 1: only fires when this cluster spans MULTIPLE schemas (the
    # multi-source case).  Single-schema runs fall through.
    if not only_schema:
        # Multi-schema cluster: pick the most-represented non-public schema.
        bag_s: dict[str, int] = defaultdict(int)
        for s in schemas:
            if s != "public":
                bag_s[s] += sum(1 for t in member_tables if t["schema_name"] == s)
        if bag_s:
            top_schema, _ = max(bag_s.items(), key=lambda kv: (kv[1], kv[0]))
            return top_schema, None

    # Rule 2: anchor table = highest weighted-degree FACT or DIMENSION.
    anchor_candidates = [
        t for t in member_tables
        if archetypes.get(int(t["table_id"])) in {"FACT", "DIMENSION"}
    ]
    if anchor_candidates:
        anchor = max(
            anchor_candidates,
            key=lambda t: (
                weighted_degree.get(int(t["table_id"]), 0.0),
                # deterministic tie-break: lower table_id first ranks higher
                -int(t["table_id"]),
            ),
        )
        anchor_token = _singularise(str(anchor["table_name"]))
        if anchor_token:
            return f"{anchor_token}_cluster", only_schema

    # Rule 3: lexical prefix - >=60% share token-1.
    tokens = [str(t["table_name"]).split("_")[0] for t in member_tables if t.get("table_name")]
    if tokens:
        bag: dict[str, int] = defaultdict(int)
        for tk in tokens:
            bag[tk] += 1
        top_token, top_count = max(bag.items(), key=lambda kv: (kv[1], kv[0]))
        if top_count / len(tokens) >= 0.6:
            return f"{top_token}_cluster", only_schema

    # Rule 4: fallback — single-table singleton clusters or fully ambiguous.
    # When there's a non-public schema, suffix it for readability so two
    # singletons across different schemas don't collide.
    if only_schema and only_schema != "public":
        return f"{only_schema}_cluster_{cluster_id}", only_schema
    return f"cluster_{cluster_id}", only_schema


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def cluster_schema(
    schema_name: str,
    *,
    tables: list[dict],
    columns: list[dict],
    edges: list[dict],
    pii_findings: list[dict],
    confidence_floor: float = 0.7,
    seed: int = 42,
    semantic_merge_enabled: bool = True,
    semantic_merge_threshold: float = 0.65,
    semantic_merge_modularity_floor: float = 0.95,
    semantic_label_enabled: bool = True,
    semantic_label_threshold: float = 0.55,
) -> ClusteringResult:
    """
    Run the full cluster-engine pipeline for a single schema.

    Parameters
    ----------
    schema_name:
        The name of the schema being clustered (drives Rule-1 naming).
    tables:
        Rows from ``tbl_inventory``; must include ``table_id``, ``schema_name``,
        ``table_name``, ``row_count_estimate``.
    columns:
        Rows from ``col_inventory``; must include ``column_id``, ``table_id``,
        ``column_name``, ``is_pk`` (and ``is_implicit_pk`` when available).
    edges:
        Rows from ``relationships``; must include ``child_col_id``,
        ``parent_col_id``, ``cardinality``, ``confidence``.
    pii_findings:
        Rows from ``pii_findings``; only ``table_id`` and ``pii_type`` are
        consumed.  Findings missing ``table_id`` are ignored.
    confidence_floor:
        Edges with ``confidence < confidence_floor`` are dropped before any
        graph work happens.  Defaults to 0.7 per Council-1.
    seed:
        Deterministic Louvain seed.  Defaults to 42.

    Returns
    -------
    ClusteringResult
        Frozen dataclass that CL-2 turns into DB rows.
    """
    # ----- 0. Filter input rows to *this* schema --------------------
    schema_tables_all = [t for t in tables if t.get("schema_name") == schema_name]

    # Pull out zero-record tables into a dedicated "empty" cluster.  These
    # tables can't participate in containment-based FK validation (no values
    # to test), so they otherwise become a long tail of meaningless
    # singletons — grouping them under one labelled cluster keeps the
    # cluster graph readable and signals "this group needs other evidence
    # (DDL parsing, new data, ...) before it can be analysed".
    empty_table_ids: set[int] = {
        int(t["table_id"]) for t in schema_tables_all if _row_count(t) == 0
    }
    schema_tables = [
        t for t in schema_tables_all if int(t["table_id"]) not in empty_table_ids
    ]
    schema_table_ids: set[int] = {int(t["table_id"]) for t in schema_tables}

    # column->table map; we keep the full map (cross-schema) so that an edge
    # row can be filtered by whether *both* endpoints land in our schema.
    col_to_table = _column_to_table(columns)
    pk_col_ids = _pk_column_ids(columns)
    cols_by_table = _columns_by_table(columns)
    table_pii = _table_pii_types(pii_findings, col_to_table)

    if not schema_tables_all:
        return ClusteringResult(
            clusters=tuple(),
            table_assignments=tuple(),
            junction_collapsed=tuple(),
            modularity_score=0.0,
            edge_count_post_collapse=0,
        )

    # Schema has only empty tables: short-circuit to the empty cluster only.
    if not schema_tables:
        empty_cluster, empty_assignments = _build_empty_cluster(
            cluster_id=0,
            schema_name=schema_name,
            empty_tables=schema_tables_all,
        )
        return ClusteringResult(
            clusters=(empty_cluster,) if empty_cluster else tuple(),
            table_assignments=tuple(empty_assignments),
            junction_collapsed=tuple(),
            modularity_score=0.0,
            edge_count_post_collapse=0,
        )

    # ----- 1. Project column-level edges to table-level ------------
    table_edges, edge_rows = _project_edges(
        edges,
        col_to_table=col_to_table,
        schema_tables=schema_table_ids,
        table_pii=table_pii,
        confidence_floor=confidence_floor,
    )

    # FK column ids (any column appearing on the child side of any qualifying
    # edge - used by junction archetype detection so FK columns don't count
    # as "descriptive payload").
    fk_col_ids: set[int] = set()
    for e in edges:
        conf = e.get("confidence")
        if conf is None or float(conf) < confidence_floor:
            continue
        cccol = e.get("child_col_id")
        if cccol is not None:
            fk_col_ids.add(int(cccol))

    # ----- 2. Archetype tagging -----------------------------------
    # Pre-compute schema-wide stats.
    row_counts = [_row_count(t) for t in schema_tables]
    sorted_rows = sorted(row_counts)
    schema_median = (
        sorted_rows[len(sorted_rows) // 2] if sorted_rows else 0
    )
    fact_threshold = int(_quartile_threshold(row_counts, 0.75))

    # Index outbound / inbound edges per table (cardinality preserved).
    out_idx: dict[int, list[tuple[int, int, float, str]]] = defaultdict(list)
    in_idx: dict[int, list[tuple[int, int, float, str]]] = defaultdict(list)
    for (ptid, ctid, w, card) in table_edges:
        # ``ctid`` is the child / outbound source in our convention; the
        # outbound MANY_TO_ONE goes from child to parent.  So ctid -> out_idx.
        out_idx[ctid].append((ptid, ctid, w, card))
        in_idx[ptid].append((ptid, ctid, w, card))

    archetypes: dict[int, str] = {}
    table_by_id: dict[int, dict] = {int(t["table_id"]): t for t in schema_tables}
    for tid, table in table_by_id.items():
        cols = cols_by_table.get(tid, [])
        outs = out_idx.get(tid, [])
        ins = in_idx.get(tid, [])
        parent_rcs = [
            _row_count(table_by_id.get(p, {}))
            for (p, _c, _w, _card) in outs
            if p in table_by_id
        ]
        archetypes[tid] = _archetype_for(
            table=table,
            cols=cols,
            out_edges=outs,
            in_edges=ins,
            pk_col_ids=pk_col_ids,
            fk_col_ids=fk_col_ids,
            parent_row_counts=parent_rcs,
            fact_row_threshold=fact_threshold,
            schema_median_rows=float(schema_median),
        )

    junction_tids = {tid for tid, arc in archetypes.items() if arc == "JUNCTION"}

    # ----- 3. Junction collapse ----------------------------------
    edges_post, junction_to_parents = _collapse_junctions(
        junction_tids=junction_tids,
        table_edges=table_edges,
        edge_rows=edge_rows,
    )

    # ----- 4. Build the undirected, weighted graph for Louvain ---
    G = nx.Graph()
    # Add every non-junction table as a node so isolates also get a community.
    for tid in schema_table_ids:
        if tid in junction_tids:
            continue
        G.add_node(tid)
    # Sum-of-weights when (a, b) appears multiple times after collapse.
    edge_w_sum: dict[tuple[int, int], float] = defaultdict(float)
    for (a, b, w, _card) in edges_post:
        if a in junction_tids or b in junction_tids:
            continue
        u, v = (a, b) if a < b else (b, a)
        edge_w_sum[(u, v)] += float(w)
    for (u, v), w in edge_w_sum.items():
        G.add_edge(u, v, weight=w)

    edge_count_post_collapse = G.number_of_edges()

    # ----- 5. Louvain ----------------------------------------------
    if G.number_of_nodes() == 0:
        communities: list[set[int]] = []
        modularity_score = 0.0
    else:
        communities = louvain_communities(G, weight="weight", seed=seed)
        # If isolates returned: louvain_communities still yields singletons.
        # Sort communities deterministically by smallest member so that
        # cluster_id assignment is reproducible across runs.
        communities = [set(c) for c in communities]

        # ----- 5b. Hybrid semantic merge -------------------------------
        # Optional pass: combine clusters whose centroid embeddings are
        # similar enough AND that share at least one inter-cluster
        # FK edge in G.  Modularity guard rejects merges that would
        # tank the global modularity beyond the configured floor.
        # Silently no-ops when disabled or when sentence-transformers
        # is unavailable.
        if semantic_merge_enabled and len(communities) >= 2:
            communities = _semantic_merge(
                communities,
                G,
                table_by_id,
                threshold=semantic_merge_threshold,
                modularity_floor=semantic_merge_modularity_floor,
            )

        communities.sort(key=lambda c: (min(c), len(c)))
        if G.number_of_edges() == 0:
            modularity_score = 0.0
        else:
            modularity_score = float(modularity(G, communities, weight="weight"))

    # ----- 6. Weighted degree (for naming + dominant parent) ------
    weighted_degree: dict[int, float] = {n: 0.0 for n in G.nodes}
    for u, v, data in G.edges(data=True):
        w = float(data.get("weight", 0.0))
        weighted_degree[u] += w
        weighted_degree[v] += w

    # ----- 7. Junction reattachment ------------------------------
    # Map junction_tid -> chosen parent tid (dominant by weighted degree;
    # lower table_id breaks ties).
    junction_assignment: dict[int, int] = {}
    for jtid, (p1, p2) in junction_to_parents.items():
        d1 = weighted_degree.get(p1, 0.0)
        d2 = weighted_degree.get(p2, 0.0)
        if d1 > d2 or (d1 == d2 and p1 < p2):
            junction_assignment[jtid] = p1
        else:
            junction_assignment[jtid] = p2
    # Junctions with fewer than two parents (rare) attach to themselves -
    # become a singleton cluster downstream.
    for jtid in junction_tids:
        if jtid not in junction_assignment:
            junction_assignment[jtid] = jtid
            communities.append({jtid})

    # ----- 8. Build node->cluster_id map and Cluster records -----
    node_to_cluster: dict[int, int] = {}
    for cid, members in enumerate(communities):
        for n in members:
            node_to_cluster[n] = cid
    # Junctions follow their dominant parent.
    for jtid, parent in junction_assignment.items():
        if parent in node_to_cluster:
            node_to_cluster[jtid] = node_to_cluster[parent]
        elif jtid in node_to_cluster:
            pass
        else:
            # Fallback: orphan junction becomes its own cluster.
            new_cid = len(communities)
            communities.append({jtid})
            node_to_cluster[jtid] = new_cid

    # Re-aggregate membership including junctions.
    members_by_cluster: dict[int, list[int]] = defaultdict(list)
    for tid in schema_table_ids:
        cid = node_to_cluster.get(tid)
        if cid is None:
            # Total isolate that fell out of Louvain (shouldn't happen, but
            # be defensive): assign to its own new cluster.
            cid = len(communities)
            communities.append({tid})
            node_to_cluster[tid] = cid
        members_by_cluster[cid].append(tid)

    # ----- 9. Compute per-cluster modularity contribution + edge --
    total_w = sum(d.get("weight", 0.0) for _, _, d in G.edges(data=True))
    two_m = 2.0 * total_w if total_w > 0 else 0.0

    clusters_out: list[Cluster] = []
    for cid in sorted(members_by_cluster.keys()):
        member_tids = sorted(members_by_cluster[cid])
        member_rows = [table_by_id[t] for t in member_tids if t in table_by_id]

        # Archetype distribution
        adist: dict[str, int] = defaultdict(int)
        for t in member_tids:
            adist[archetypes.get(t, "DIMENSION")] += 1

        # Intra/Inter edge counts and modularity contribution.
        # Standard decomposition (NetworkX `modularity` reference):
        #     Q_c = L_c / m  -  (D_c / 2m)^2
        # where L_c is the weighted *count* of intra-cluster edges (each edge
        # counted ONCE) and D_c is the sum of weighted degrees of nodes in
        # the cluster (each intra edge contributes 2*w to D_c, each inter
        # edge contributes 1*w; same as `sum_in + sum_tot/2` per nx).
        # Our `total_w` is the sum of edge weights, i.e. m, so we divide
        # L_c by m and D_c by 2m.
        member_set = set(member_tids)
        intra = 0
        inter = 0
        L_c = 0.0   # sum of intra-cluster edge weights (each counted once)
        D_c = 0.0   # sum of weighted degrees of nodes in the cluster
        for u, v, data in G.edges(data=True):
            w = float(data.get("weight", 0.0))
            u_in = u in member_set
            v_in = v in member_set
            if u_in and v_in:
                intra += 1
                L_c += w
                D_c += 2 * w
            elif u_in or v_in:
                inter += 1
                D_c += w
        if total_w > 0:
            qc = (L_c / total_w) - (D_c / two_m) ** 2
        else:
            qc = 0.0

        cname, schema_for_cluster = _name_cluster(
            cluster_id=cid,
            member_tables=member_rows,
            schema_name=schema_name,
            archetypes=archetypes,
            weighted_degree=weighted_degree,
        )

        # Zero-shot business-domain label.  Cosine similarity between
        # the cluster's table-name centroid and a fixed vocabulary;
        # None when the model is unavailable or no term clears the
        # threshold.  Never overrides ``cname`` — the UI renders the
        # label as a subtitle.
        sem_label: str | None = None
        if semantic_label_enabled and member_rows:
            sem_label = _zero_shot_label(
                member_rows,
                threshold=semantic_label_threshold,
            )

        clusters_out.append(
            Cluster(
                cluster_id=cid,
                name=cname,
                schema_name=schema_for_cluster,
                table_ids=tuple(member_tids),
                archetype_distribution=dict(adist),
                intra_edge_count=intra,
                inter_edge_count=inter,
                modularity_contribution=float(qc),
                semantic_label=sem_label,
            )
        )

    # ----- 10. Re-number clusters to 0..N-1 (after sort) ---------
    # Communities were sorted by min-member earlier; cluster_ids in the
    # ``clusters`` list are already 0-indexed via enumeration.  Re-emit table
    # assignments using the canonical id.
    cluster_id_remap: dict[int, int] = {}
    final_clusters: list[Cluster] = []
    for new_id, c in enumerate(sorted(clusters_out, key=lambda c: c.cluster_id)):
        cluster_id_remap[c.cluster_id] = new_id
        final_clusters.append(
            Cluster(
                cluster_id=new_id,
                name=c.name,
                schema_name=c.schema_name,
                table_ids=c.table_ids,
                archetype_distribution=c.archetype_distribution,
                intra_edge_count=c.intra_edge_count,
                inter_edge_count=c.inter_edge_count,
                modularity_contribution=c.modularity_contribution,
                semantic_label=c.semantic_label,
            )
        )

    # ----- 11. Per-table assignments -----------------------------
    assignments: list[ClusteredTable] = []
    for tid in sorted(schema_table_ids):
        t = table_by_id[tid]
        cid_old = node_to_cluster.get(tid)
        if cid_old is None:
            continue
        cid_new = cluster_id_remap.get(cid_old, cid_old)
        assignments.append(
            ClusteredTable(
                table_id=tid,
                table_name=str(t["table_name"]),
                schema_name=str(t["schema_name"]),
                cluster_id=cid_new,
                archetype=archetypes.get(tid, "DIMENSION"),
            )
        )

    # ----- 12. Append the "empty tables" cluster (if any) --------
    if empty_table_ids:
        empty_rows = [
            t for t in schema_tables_all
            if int(t["table_id"]) in empty_table_ids
        ]
        empty_cluster, empty_assignments = _build_empty_cluster(
            cluster_id=len(final_clusters),
            schema_name=schema_name,
            empty_tables=empty_rows,
        )
        if empty_cluster is not None:
            final_clusters.append(empty_cluster)
            assignments.extend(empty_assignments)

    return ClusteringResult(
        clusters=tuple(final_clusters),
        table_assignments=tuple(assignments),
        junction_collapsed=tuple(sorted(junction_tids)),
        modularity_score=float(modularity_score),
        edge_count_post_collapse=int(edge_count_post_collapse),
    )


def _build_empty_cluster(
    *,
    cluster_id: int,
    schema_name: str,
    empty_tables: list[dict],
) -> tuple[Cluster | None, list[ClusteredTable]]:
    """Build the "<schema>_empty_tables" cluster + its per-table assignments.

    Returns ``(None, [])`` when the input list is empty.  Otherwise returns
    a single Cluster containing all the empty tables tagged with archetype
    ``EMPTY``.  No edges are computed (these tables can't participate in
    containment-validated FKs by definition), so intra/inter counts and
    modularity contribution are all zero.
    """
    if not empty_tables:
        return None, []
    member_tids = sorted(int(t["table_id"]) for t in empty_tables)
    cluster = Cluster(
        cluster_id=cluster_id,
        name=f"{schema_name}_empty_tables",
        schema_name=schema_name,
        table_ids=tuple(member_tids),
        archetype_distribution={"EMPTY": len(member_tids)},
        intra_edge_count=0,
        inter_edge_count=0,
        modularity_contribution=0.0,
    )
    assignments = [
        ClusteredTable(
            table_id=int(t["table_id"]),
            table_name=str(t["table_name"]),
            schema_name=str(t["schema_name"]),
            cluster_id=cluster_id,
            archetype="EMPTY",
        )
        for t in sorted(empty_tables, key=lambda r: int(r["table_id"]))
    ]
    return cluster, assignments
