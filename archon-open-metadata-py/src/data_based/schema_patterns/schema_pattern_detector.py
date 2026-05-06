import re
from collections import defaultdict
from typing import Any, Iterable, Optional


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

_TEMPORAL_NAME_RE = re.compile(
    r"^(modified_date|modified_at|updated_at|updated_date|last_modified|"
    r"last_updated|last_changed|date_modified)$",
    re.IGNORECASE,
)
_SURROGATE_PK_RE = re.compile(r".*_id$|^id$", re.IGNORECASE)
_INT_TYPE_RE = re.compile(
    r"^(int|integer|bigint|smallint|serial|bigserial|tinyint)\b",
    re.IGNORECASE,
)


class SchemaPatternDetector:
    """
    Stage 6: Surfaces five high-level schema-design patterns the
    underlying inventory + relationships rows already encode but don't
    explicitly advertise: known-schema fingerprint, temporal/CDC
    tracking, surrogate-key prevalence, bridge tables, polymorphic root.

    All entry points are pure — no DB IO, no SQLAlchemy.  Caller passes
    the already-loaded inventory + relationships shape.
    """

    @staticmethod
    def match_known_schema(
        table_names: Iterable[str],
        min_overlap: float = 0.30,
    ) -> Optional[dict[str, Any]]:
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

    @staticmethod
    def detect_temporal(
        columns: Iterable[dict[str, Any]],
        total_tables: int,
    ) -> dict[str, Any]:
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

    @staticmethod
    def surrogate_key_stats(
        columns: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
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

    @staticmethod
    def bridge_tables(
        columns: Iterable[dict[str, Any]],
        edges: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
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
            if not (2 <= fk_count <= 3):
                continue
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

    @staticmethod
    def subtype_supertype(
        edges: Iterable[dict[str, Any]],
        min_subtypes: int = 2,
    ) -> list[dict[str, Any]]:
        by_parent: dict[tuple[str, str], set[str]] = defaultdict(set)
        for e in edges:
            from_t = str(e.get("from", ""))
            to_t = str(e.get("to", ""))
            label = str(e.get("label", "") or "")
            child_col = ""
            if " → " in label:
                child_col = label.split(" → ", 1)[0].strip()
            elif "->" in label:
                child_col = label.split("->", 1)[0].strip()
            if not (from_t and to_t and child_col):
                continue
            canon = SchemaPatternDetector._canonical_parent_keys(to_t)
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

    @staticmethod
    def _canonical_parent_keys(parent: str) -> set[str]:
        p = parent.lower()
        out = {p + "_id", "id"}
        if p.endswith("ies") and len(p) > 3:
            out.add(p[:-3] + "y_id")
        if p.endswith("s") and not p.endswith("ss") and len(p) > 1:
            out.add(p[:-1] + "_id")
        return out

    @staticmethod
    def detect_all(
        columns: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        table_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Run every detector and return the bundled report — the shape
        consumed by the SuperNova UI's schema-insights panel."""
        names = table_names or sorted({str(c.get("table", "")) for c in columns if c.get("table")})
        return {
            "fingerprint": SchemaPatternDetector.match_known_schema(names),
            "temporal": SchemaPatternDetector.detect_temporal(columns, len(names)),
            "surrogate_keys": SchemaPatternDetector.surrogate_key_stats(columns),
            "bridge_tables": SchemaPatternDetector.bridge_tables(columns, edges),
            "polymorphic_roots": SchemaPatternDetector.subtype_supertype(edges),
        }
