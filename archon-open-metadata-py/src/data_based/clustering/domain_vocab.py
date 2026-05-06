"""
Fixed business-domain vocabulary for zero-shot cluster labelling.

The hybrid clustering pass (clustering._zero_shot_label) embeds each
cluster's centroid via the same SentenceTransformer model that
name_similarity.py uses, then compares it to every vocabulary term in
:data:`DOMAINS` via cosine similarity.  The closest match above
``RelationshipsConfig.semantic_label_threshold`` becomes the cluster's
``semantic_label`` — surfaced in the API response and rendered as a
subtitle on cluster cards.

Vocabulary design notes
-----------------------
* Synonyms are flattened into a single string per term so the embedded
  vector reflects the broader concept (e.g. "human resources employees
  staff personnel" rather than just "hr").  Without this, "hr" alone
  embeds to a vector dominated by abbreviation noise and rarely scores
  high against table-name centroids.
* The list is intentionally short (~13 entries) and English-only.  Each
  term is broad enough to cover several adjacent table archetypes — we
  don't aim to label every cluster, only the ones with clear semantic
  signal.  When no term hits the threshold the cluster keeps its
  algorithmic name (anchor / token-prefix / fallback).
* "Audit" / "Configuration" are deliberate catch-alls for clusters that
  Louvain often groups together but that aren't a "business domain" in
  the BI-glossary sense — labelling them this way at least tells the
  user what they're looking at.

To extend: append a row to :data:`DOMAINS`.  Don't remove rows without
also re-running the cluster regression tests.
"""

from __future__ import annotations

# Each entry is (canonical_label, search_text).  ``canonical_label`` is
# what the UI renders; ``search_text`` is what we embed.  Multi-word
# search text gives the embedding model more signal than single tokens.

DOMAINS: tuple[tuple[str, str], ...] = (
    ("Sales",                "sales orders invoices revenue customers"),
    ("Finance",              "finance accounting general ledger transactions"),
    ("Customer Management",  "customer contact account person profile"),
    ("Inventory",            "inventory stock warehouse product catalog"),
    ("Product",              "product catalog item sku category"),
    ("Order Management",     "order purchase shipment fulfillment"),
    ("Human Resources",      "human resources employees staff personnel hr"),
    ("Audit",                "audit log history change tracking"),
    ("Configuration",        "configuration settings policy rule parameters"),
    ("Logistics",            "shipping logistics delivery transport route"),
    ("Security",             "security authentication permission role access"),
    ("Billing",              "billing invoice payment receipt charge"),
    ("Marketing",            "marketing campaign lead promotion"),
    ("Vendor Management",    "vendor supplier procurement partner"),
)


def domain_labels() -> list[str]:
    """Canonical labels (what the UI shows).  Order matches DOMAINS."""
    return [label for label, _ in DOMAINS]


def domain_search_texts() -> list[str]:
    """Search texts (what gets embedded).  Order matches DOMAINS."""
    return [text for _, text in DOMAINS]
