"""
schema_patterns.py — high-level schema-design pattern detection.

Surfaces five things the underlying pipeline already has the data for
but doesn't presently advertise:

  1. ``match_known_schema``   — fingerprint the table-name set against
                                a small dictionary of well-known
                                schemas (AdventureWorks, Northwind,
                                Saleor, etc.) and return the closest
                                match by Jaccard overlap.
  2. ``detect_temporal``      — count how many tables carry a
                                ``modified_date`` / ``updated_at`` /
                                ``last_modified`` column; surface as
                                a "supports CDC / temporal tracking"
                                insight.
  3. ``surrogate_key_stats``  — fraction of tables whose primary key
                                is an integer-typed ``*_id``; surface
                                as a design-pattern callout.
  4. ``bridge_tables``        — list of detected JUNCTION-archetype
                                tables (already produced by
                                clustering.py — we just project the
                                names for the UI).
  5. ``subtype_supertype``    — tables that share an ``*_id`` FK to
                                the same parent (e.g. customer /
                                employee / vendor all referencing
                                ``business_entity_id``); surface as a
                                polymorphic-root candidate set.

All functions are pure — no DB IO, no SQLAlchemy.  Caller (``api/main.py``
``GET /api/jobs/{id}/insights``) loads inventory + relationships rows
and feeds them in.  Idempotent and side-effect-free; unit-testable
without a database.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# 1. Known-schema fingerprint
# ---------------------------------------------------------------------------
#
# Tiny dictionary — only the load-bearing tables, not every table — so the
# fingerprint is robust to schema variants.  Each entry's value is the
# canonical anchor set; matching is by Jaccard intersection over the
# observed table-name set.  Add new schemas at the bottom; never rename
# without updating tests.
KNOWN_SCHEMAS: dict[str, set[str]] = {
    "AdventureWorks": {
        "business_entity", "person", "customer", "employee", "vendor",
        "address", "address_type", "country_region", "state_province",
        "business_entity_address", "business_entity_contact",
        "credit_card", "person_credit_card",
        "sales_order_header", "sales_order_detail", "sales_person",
        "sales_territory", "sales_reason", "currency", "currency_rate",
        "product", "product_category", "product_subcategory",
        "product_inventory", "product_model", "product_review",
        "product_vendor", "purchase_order_header", "purchase_order_detail",
        "department", "shift", "store",
    },
    "Northwind": {
        "categories", "customers", "employees", "order_details",
        "orders", "products", "shippers", "suppliers", "territories",
        "region", "employee_territories",
    },
    "Saleor": {
        "account_user", "checkout_checkout", "checkout_checkoutline",
        "order_order", "order_orderline", "product_product",
        "product_productvariant", "product_category",
        "warehouse_warehouse", "shipping_shippingmethod",
        "discount_voucher", "channel_channel",
    },
    "DVDRental": {
        "actor", "address", "category", "city", "country", "customer",
        "film", "film_actor", "film_category", "inventory", "language",
        "payment", "rental", "staff", "store",
    },
    "WordPress": {
        "wp_posts", "wp_postmeta", "wp_users", "wp_usermeta",
        "wp_options", "wp_terms", "wp_term_relationships",
        "wp_term_taxonomy", "wp_comments", "wp_commentmeta",
    },
    "Drupal": {
        "node", "node_field_data", "users", "users_field_data",
        "taxonomy_term_data", "file_managed", "block_content",
        "menu_link_content",
    },
    "Magento": {
        "catalog_product_entity", "catalog_category_entity",
        "sales_order", "sales_order_item", "customer_entity",
        "quote", "quote_item", "store", "store_website",
    },
}


def match_known_schema(
    table_names: Iterable[str],
    *,
    min_overlap: float = 0.30,
) -> Optional[dict[str, Any]]:
    """Find the closest-matching known schema by Jaccard overlap.

    Returns ``{name, confidence, matched, missing, extra, total}`` for
    the best match above ``min_overlap``, else ``None``.

    Confidence is the Jaccard index of (observed ∩ known) / (observed ∪ known)
    — sensitive both to coverage (matched a lot of expected tables) and
    to noise (avoids matching just because the observed set has an extra
    100 random tables).
    """
    observed = {t.lower() for t in table_names if t}
    if not observed:
        return None

    best: Optional[dict[str, Any]] = None
    for name, expected in KNOWN_SCHEMAS.items():
        expected_lc = {e.lower() for e in expected}
        intersect = observed & expected_lc
        if not intersect:
            continue
        union = observed | expected_lc
        jaccard = len(intersect) / len(union) if union else 0.0
        if jaccard < min_overlap:
            continue
        if best is None or jaccard > best["confidence"]:
            missing = sorted(expected_lc - observed)
            # Extras: capped at 25 — most schemas have a long tail of
            # auxiliary tables (audit, _hist, _backup) that aren't in
            # the anchor set; reporting all of them is noise.
            extra = sorted(observed - expected_lc)
            best = {
                "name": name,
                "confidence": round(jaccard, 4),
                "matched": sorted(intersect),
                "missing": missing,
                "extra_count": len(extra),
                "extra_sample": extra[:25],
                "anchor_size": len(expected_lc),
                "observed_size": len(observed),
            }
    return best


# ---------------------------------------------------------------------------
# 2. Temporal-tracking pattern (modified_date / updated_at / last_modified)
# ---------------------------------------------------------------------------


_TEMPORAL_NAME_RE = re.compile(
    r"^(modified_date|modified_at|updated_at|updated_date|last_modified|"
    r"last_updated|last_changed|date_modified)$",
    re.IGNORECASE,
)


def detect_temporal(
    columns: Iterable[dict[str, Any]],
    total_tables: int,
) -> dict[str, Any]:
    """Count how many tables carry a temporal-tracking column.

    Input ``columns`` is the per-column inventory list from the API
    response (each entry has at minimum ``table`` and ``column``).
    Returns ``{tracked_tables, total_tables, fraction, supports_cdc}``.

    ``supports_cdc`` flips True when ≥75% of tables have a temporal
    column — the threshold below which the pattern is incidental.
    """
    by_table: dict[str, bool] = defaultdict(bool)
    for c in columns:
        if _TEMPORAL_NAME_RE.match(str(c.get("column", ""))):
            by_table[str(c.get("table", ""))] = True
    tracked = sum(1 for v in by_table.values() if v)
    total = max(1, int(total_tables))
    frac = tracked / total
    return {
        "tracked_tables": tracked,
        "total_tables": int(total_tables),
        "fraction": round(frac, 4),
        "supports_cdc": frac >= 0.75,
    }


# ---------------------------------------------------------------------------
# 3. Surrogate-key prevalence
# ---------------------------------------------------------------------------


_SURROGATE_PK_RE = re.compile(r".*_id$|^id$", re.IGNORECASE)
_INT_TYPE_RE = re.compile(
    r"^(int|integer|bigint|smallint|serial|bigserial|tinyint)\b",
    re.IGNORECASE,
)


def surrogate_key_stats(
    columns: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Per-table surrogate-key prevalence.

    Looks at every column flagged ``is_pk=True``; counts how many are
    surrogate-shaped (name matches ``*_id`` / ``id``) and how many of
    those are integer-typed.  Returns
    ``{tables_with_pk, surrogate_count, integer_count, surrogate_pct,
    integer_pct}``.
    """
    pk_tables: set[str] = set()
    surrogate_pks_per_table: dict[str, bool] = {}
    integer_pks_per_table: dict[str, bool] = {}
    for c in columns:
        if not c.get("is_pk"):
            continue
        tname = str(c.get("table", ""))
        cname = str(c.get("column", ""))
        dtype = str(c.get("data_type", ""))
        pk_tables.add(tname)
        is_surrogate = bool(_SURROGATE_PK_RE.match(cname))
        is_integer = bool(_INT_TYPE_RE.match(dtype))
        # Per-table accumulator: at least ONE PK column matches the
        # surrogate / integer shape.  Composite PKs count as surrogate
        # if any member is `*_id`-shaped.
        surrogate_pks_per_table[tname] = (
            surrogate_pks_per_table.get(tname, False) or is_surrogate
        )
        integer_pks_per_table[tname] = (
            integer_pks_per_table.get(tname, False) or is_integer
        )
    n = max(1, len(pk_tables))
    surrogate = sum(1 for v in surrogate_pks_per_table.values() if v)
    integer = sum(1 for v in integer_pks_per_table.values() if v)
    return {
        "tables_with_pk": len(pk_tables),
        "surrogate_count": surrogate,
        "integer_count": integer,
        "surrogate_pct": round(surrogate / n, 4),
        "integer_pct": round(integer / n, 4),
    }


# ---------------------------------------------------------------------------
# 4. Bridge-table list
# ---------------------------------------------------------------------------


def bridge_tables(
    columns: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect bridge / junction tables.

    A bridge is a SMALL table whose columns are PRIMARILY two-or-three
    FK shapes pointing to different parents — typical for many-to-many
    junctions (``business_entity_address``, ``person_credit_card``,
    ``employee_department_history``).

    Heuristic — all of:
      * 2 ≤ distinct-parent count ≤ 3 (true bridges have at most 3
        parents — anything beyond that is a fact / event table that
        happens to have many FKs, NOT a junction)
      * non-FK columns ≤ 2 (a real junction carries the join keys plus
        at most a ``modified_date`` and maybe a small payload like
        ``qty`` or ``role``; fact tables have many measure columns
        and would exceed this).

    Returns a list of ``{table, fk_count, total_cols, parents}`` dicts
    sorted by ascending FK count then table name (M:N first, then
    M:N:N).
    """
    cols_per_table: dict[str, int] = defaultdict(int)
    for c in columns:
        cols_per_table[str(c.get("table", ""))] += 1

    fk_parents: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        from_t = str(e.get("from", ""))
        to_t = str(e.get("to", ""))
        if from_t and to_t and from_t != to_t:
            fk_parents[from_t].add(to_t)

    out: list[dict[str, Any]] = []
    for table, parents in fk_parents.items():
        fk_count = len(parents)
        total_cols = cols_per_table.get(table, 0)
        if total_cols == 0:
            continue
        # Bridge requires exactly 2 or 3 distinct parents — beyond
        # that it's a fact / hub table.
        if not (2 <= fk_count <= 3):
            continue
        # And the table must be small: non-FK columns ≤ 2.  Counts
        # FK columns as 1-per-parent (the multi-FK candidate-graph
        # noise is filtered out; same column to multiple parents only
        # counts once).
        non_fk = total_cols - fk_count
        if non_fk > 2:
            continue
        out.append({
            "table": table,
            "fk_count": fk_count,
            "total_cols": total_cols,
            "parents": sorted(parents),
        })
    out.sort(key=lambda r: (r["fk_count"], r["table"]))
    return out


# ---------------------------------------------------------------------------
# 5. Subtype / supertype detection (polymorphic root)
# ---------------------------------------------------------------------------


def subtype_supertype(
    edges: Iterable[dict[str, Any]],
    *,
    min_subtypes: int = 2,
) -> list[dict[str, Any]]:
    """Detect polymorphic-root patterns.

    A "supertype" is a parent table referenced by N >= ``min_subtypes``
    children whose FK column name matches the parent's natural-key
    shape (``<parent>_id`` or singular form).

    The natural-key gate is critical: without it, FK-candidate noise
    where the SAME child column has multiple validated parents
    (``email_address.business_entity_id`` matching both
    ``business_entity`` and ``business_entity_address``) inflates
    every shared column into a fake polymorphic root.  We only count
    an edge when the FK column is the canonical name for its parent.

    Typical hits: ``customer / employee / vendor`` all referencing
    ``business_entity.business_entity_id``.  Returns
    ``[{supertype, fk_column, subtypes, count}]`` sorted by subtype
    count desc.
    """
    # parent_table -> child_fk_col -> set(child_table)
    by_parent: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in edges:
        from_t = str(e.get("from", ""))
        to_t = str(e.get("to", ""))
        # ``label`` is "child_col → parent_col"; we want the child column.
        label = str(e.get("label", "") or "")
        child_col = ""
        if " → " in label:
            child_col = label.split(" → ", 1)[0].strip()
        elif "->" in label:
            child_col = label.split("->", 1)[0].strip()
        if not (from_t and to_t and child_col):
            continue
        # Natural-key gate: the FK column name must match the parent's
        # canonical shape — ``<parent>_id`` (or its trailing-s
        # singular).  Filters out spurious FK-candidate matches where
        # a column happens to have values that overlap a non-canonical
        # parent.
        canon = _canonical_parent_keys(to_t)
        if child_col.lower() not in canon:
            continue
        by_parent[(to_t, child_col)].add(from_t)

    out: list[dict[str, Any]] = []
    for (supertype, fk_col), subtypes in by_parent.items():
        if len(subtypes) < min_subtypes:
            continue
        out.append({
            "supertype": supertype,
            "fk_column": fk_col,
            "subtypes": sorted(subtypes),
            "count": len(subtypes),
        })
    out.sort(key=lambda r: (-r["count"], r["supertype"]))
    return out


def _canonical_parent_keys(parent: str) -> set[str]:
    """Names a child FK column would canonically have when it points
    to ``parent``.  Lowercase; covers the common variants:

      * ``<parent>_id``            (most common: business_entity_id)
      * ``<singular_parent>_id``   (orders → order_id)
      * ``id``                     (rare; only when child re-uses the
                                    PK name verbatim)
    """
    p = parent.lower()
    out = {p + "_id", "id"}
    # Singularise trailing s / es — Northwind etc. use plural table
    # names (``orders`` → ``order_id``).
    if p.endswith("ies") and len(p) > 3:
        out.add(p[:-3] + "y_id")
    if p.endswith("s") and not p.endswith("ss") and len(p) > 1:
        out.add(p[:-1] + "_id")
    return out
