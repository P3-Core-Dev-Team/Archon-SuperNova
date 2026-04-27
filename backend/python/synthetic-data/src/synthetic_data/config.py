"""Pydantic models describing all 30 tables and their column specifications."""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TypeClass(str, Enum):
    INT_NARROW = "INT_NARROW"      # int32 - smaller tables
    INT_WIDE = "INT_WIDE"          # int64 - larger tables
    UUID = "UUID"                   # UUID string
    STRING_SHORT = "STRING_SHORT"   # short strings: code, status, email, phone
    STRING_LONG = "STRING_LONG"     # free text: reviews, tickets
    DATE = "DATE"                   # date only
    TIMESTAMP = "TIMESTAMP"         # datetime with tz
    BOOL = "BOOL"
    FLOAT = "FLOAT"
    BINARY = "BINARY"               # bytes


class PiiType(str, Enum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    CREDIT_CARD_LAST4 = "CREDIT_CARD_LAST4"
    IBAN = "IBAN"
    DOB = "DOB"
    FULL_NAME = "FULL_NAME"
    FIRST_NAME = "FIRST_NAME"
    LAST_NAME = "LAST_NAME"
    ADDRESS = "ADDRESS"
    API_KEY = "API_KEY"
    FREE_TEXT_PII = "FREE_TEXT_PII"


class ForeignKey(BaseModel):
    parent_table: str
    parent_column: str
    null_pct: float = 0.0


class ColumnSpec(BaseModel):
    name: str
    type_class: TypeClass
    nullable: bool = False
    null_pct: float = 0.0
    pii_types: list[PiiType] = Field(default_factory=list)
    pii_rate: float = 0.0
    fk: Optional[ForeignKey] = None
    is_pk: bool = False
    is_fk_eligible: bool = True
    generator_kind: str = "default"  # default, zipfian, lognormal, diurnal, self_ref, etc.


class TableSpec(BaseModel):
    name: str
    row_count: int
    columns: list[ColumnSpec]
    excluded: bool = False
    exclusion_reason: Optional[str] = None

    def col(self, name: str) -> ColumnSpec:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"Column {name!r} not found in table {self.name!r}")


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

CUSTOMERS = TableSpec(
    name="customers",
    row_count=50_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="external_id", type_class=TypeClass.UUID, generator_kind="uuid"),
        ColumnSpec(name="first_name", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.FIRST_NAME], pii_rate=1.0),
        ColumnSpec(name="last_name", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.LAST_NAME], pii_rate=1.0),
        ColumnSpec(name="email", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.EMAIL], pii_rate=1.0),
        ColumnSpec(name="phone", type_class=TypeClass.STRING_SHORT, nullable=True, null_pct=0.15,
                   pii_types=[PiiType.PHONE], pii_rate=0.85),
        ColumnSpec(name="dob", type_class=TypeClass.DATE, pii_types=[PiiType.DOB], pii_rate=1.0),
        ColumnSpec(name="is_active", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP, generator_kind="growth_curve"),
        ColumnSpec(name="updated_at", type_class=TypeClass.TIMESTAMP),
    ]
)

ADDRESSES = TableSpec(
    name="addresses",
    row_count=80_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.02,
                   fk=ForeignKey(parent_table="customers", parent_column="id", null_pct=0.02),
                   pii_rate=0.0),
        ColumnSpec(name="street", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.ADDRESS], pii_rate=1.0),
        ColumnSpec(name="city", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.ADDRESS], pii_rate=1.0),
        ColumnSpec(name="state", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="postal_code", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.ADDRESS], pii_rate=1.0),
        ColumnSpec(name="country_code", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="is_primary", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

CATEGORIES = TableSpec(
    name="categories",
    row_count=50,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_NARROW, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="slug", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="description", type_class=TypeClass.STRING_LONG, is_fk_eligible=False),
        ColumnSpec(name="parent_category_id", type_class=TypeClass.INT_NARROW, nullable=True, null_pct=0.2,
                   fk=ForeignKey(parent_table="categories", parent_column="id", null_pct=0.2),
                   generator_kind="self_ref"),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

PRODUCTS = TableSpec(
    name="products",
    row_count=5_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="sku", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="description", type_class=TypeClass.STRING_LONG, is_fk_eligible=False),
        ColumnSpec(name="category_id", type_class=TypeClass.INT_NARROW,
                   fk=ForeignKey(parent_table="categories", parent_column="id")),
        ColumnSpec(name="price", type_class=TypeClass.FLOAT, generator_kind="lognormal"),
        ColumnSpec(name="is_active", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

WAREHOUSES = TableSpec(
    name="warehouses",
    row_count=20,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_NARROW, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="city", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="country_code", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="is_active", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

INVENTORY = TableSpec(
    name="inventory",
    row_count=5_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="product_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="products", parent_column="id")),
        ColumnSpec(name="quantity_on_hand", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="quantity_reserved", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="reorder_level", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="updated_at", type_class=TypeClass.TIMESTAMP),
    ]
)

WAREHOUSE_STOCK = TableSpec(
    name="warehouse_stock",
    row_count=100_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="product_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="products", parent_column="id")),
        ColumnSpec(name="warehouse_id", type_class=TypeClass.INT_NARROW,
                   fk=ForeignKey(parent_table="warehouses", parent_column="id")),
        ColumnSpec(name="quantity", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="tracking_number", type_class=TypeClass.STRING_SHORT,
                   generator_kind="fake_cc_luhn_fail"),  # looks like CC but fails Luhn
        ColumnSpec(name="updated_at", type_class=TypeClass.TIMESTAMP),
    ]
)

ORDERS = TableSpec(
    name="orders",
    row_count=500_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE, generator_kind="zipfian",
                   fk=ForeignKey(parent_table="customers", parent_column="id")),
        ColumnSpec(name="shipping_address_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="addresses", parent_column="id")),
        ColumnSpec(name="status", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="currency", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="total_amount", type_class=TypeClass.FLOAT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP, generator_kind="business_hours"),
        ColumnSpec(name="updated_at", type_class=TypeClass.TIMESTAMP),
    ]
)

ORDER_ITEMS = TableSpec(
    name="order_items",
    row_count=2_000_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="order_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="orders", parent_column="id")),
        ColumnSpec(name="product_id", type_class=TypeClass.INT_WIDE, generator_kind="zipfian",
                   fk=ForeignKey(parent_table="products", parent_column="id")),
        ColumnSpec(name="quantity", type_class=TypeClass.INT_WIDE, generator_kind="lognormal"),
        ColumnSpec(name="unit_price", type_class=TypeClass.FLOAT),
        ColumnSpec(name="discount_pct", type_class=TypeClass.FLOAT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

PAYMENTS = TableSpec(
    name="payments",
    row_count=500_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="transaction_id", type_class=TypeClass.UUID, generator_kind="uuid"),
        ColumnSpec(name="order_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="orders", parent_column="id")),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="customers", parent_column="id")),
        ColumnSpec(name="amount", type_class=TypeClass.FLOAT),
        ColumnSpec(name="currency", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="card_number_last4", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.CREDIT_CARD_LAST4], pii_rate=1.0),
        ColumnSpec(name="card_number_raw", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.CREDIT_CARD], pii_rate=1.0),
        ColumnSpec(name="iban", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.IBAN], pii_rate=1.0),
        ColumnSpec(name="status", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

# ---------------------------------------------------------------------------
# Identity / Auth
# ---------------------------------------------------------------------------

ROLES = TableSpec(
    name="roles",
    row_count=20,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_NARROW, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="description", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

USERS = TableSpec(
    name="users",
    row_count=10_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.30,
                   fk=ForeignKey(parent_table="customers", parent_column="id", null_pct=0.30)),
        ColumnSpec(name="username", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="email", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.EMAIL], pii_rate=1.0),
        ColumnSpec(name="dob", type_class=TypeClass.DATE, nullable=True, null_pct=0.1,
                   pii_types=[PiiType.DOB], pii_rate=0.9),
        ColumnSpec(name="password_hash", type_class=TypeClass.BINARY, is_fk_eligible=False),
        ColumnSpec(name="is_active", type_class=TypeClass.BOOL),
        ColumnSpec(name="is_verified", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

USER_ROLES = TableSpec(
    name="user_roles",
    row_count=15_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="user_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="users", parent_column="id")),
        ColumnSpec(name="role_id", type_class=TypeClass.INT_NARROW,
                   fk=ForeignKey(parent_table="roles", parent_column="id")),
        ColumnSpec(name="granted_at", type_class=TypeClass.TIMESTAMP),
    ]
)

USER_SESSIONS = TableSpec(
    name="user_sessions",
    row_count=200_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="session_id", type_class=TypeClass.UUID, generator_kind="uuid"),
        ColumnSpec(name="user_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="users", parent_column="id")),
        ColumnSpec(name="started_at", type_class=TypeClass.TIMESTAMP, generator_kind="diurnal"),
        ColumnSpec(name="ended_at", type_class=TypeClass.TIMESTAMP, nullable=True, null_pct=0.2),
        ColumnSpec(name="ip_address", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="user_agent", type_class=TypeClass.STRING_SHORT),
    ]
)

API_TOKENS = TableSpec(
    name="api_tokens",
    row_count=2_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="user_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="users", parent_column="id")),
        ColumnSpec(name="token", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False,
                   pii_types=[PiiType.API_KEY], pii_rate=1.0, generator_kind="api_token"),
        ColumnSpec(name="secret_hash", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False,
                   generator_kind="sha256_hash"),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="expires_at", type_class=TypeClass.TIMESTAMP, nullable=True, null_pct=0.3),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

# ---------------------------------------------------------------------------
# Operational / HR
# ---------------------------------------------------------------------------

EMPLOYEE_RECORDS = TableSpec(
    name="employee_records",
    row_count=1_500,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="employee_id", type_class=TypeClass.STRING_SHORT,
                   generator_kind="fake_ssn_999"),  # looks like SSN but 999 prefix
        ColumnSpec(name="full_name", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.FULL_NAME], pii_rate=1.0),
        ColumnSpec(name="ssn", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.SSN], pii_rate=1.0),
        ColumnSpec(name="dob", type_class=TypeClass.DATE, pii_types=[PiiType.DOB], pii_rate=1.0),
        ColumnSpec(name="work_email", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.EMAIL], pii_rate=1.0),
        ColumnSpec(name="personal_email", type_class=TypeClass.STRING_SHORT, nullable=True, null_pct=0.40,
                   pii_types=[PiiType.EMAIL], pii_rate=0.6),
        ColumnSpec(name="phone", type_class=TypeClass.STRING_SHORT,
                   pii_types=[PiiType.PHONE], pii_rate=1.0),
        ColumnSpec(name="hire_date", type_class=TypeClass.DATE),
        ColumnSpec(name="department_id", type_class=TypeClass.INT_NARROW, nullable=True, null_pct=0.05),
        ColumnSpec(name="manager_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.033,
                   fk=ForeignKey(parent_table="employee_records", parent_column="id", null_pct=0.033),
                   generator_kind="self_ref"),
        ColumnSpec(name="salary", type_class=TypeClass.FLOAT),
        ColumnSpec(name="is_active", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

DEPARTMENTS = TableSpec(
    name="departments",
    row_count=30,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_NARROW, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="head_employee_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.1,
                   fk=ForeignKey(parent_table="employee_records", parent_column="id", null_pct=0.1)),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

TICKETS = TableSpec(
    name="tickets",
    row_count=50_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="customers", parent_column="id")),
        ColumnSpec(name="assigned_to", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.1,
                   fk=ForeignKey(parent_table="employee_records", parent_column="id", null_pct=0.1)),
        ColumnSpec(name="subject", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False),
        ColumnSpec(name="status", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="priority", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
        ColumnSpec(name="updated_at", type_class=TypeClass.TIMESTAMP),
    ]
)

TICKET_MESSAGES = TableSpec(
    name="ticket_messages",
    row_count=200_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="ticket_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="tickets", parent_column="id")),
        ColumnSpec(name="author_user_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.05,
                   fk=ForeignKey(parent_table="users", parent_column="id", null_pct=0.05)),
        ColumnSpec(name="body", type_class=TypeClass.STRING_LONG, is_fk_eligible=False,
                   pii_types=[PiiType.FREE_TEXT_PII], pii_rate=0.06,
                   generator_kind="free_text_pii"),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

REVIEWS = TableSpec(
    name="reviews",
    row_count=30_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="product_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="products", parent_column="id")),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE,
                   fk=ForeignKey(parent_table="customers", parent_column="id")),
        ColumnSpec(name="rating", type_class=TypeClass.INT_NARROW),
        ColumnSpec(name="title", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False),
        ColumnSpec(name="body", type_class=TypeClass.STRING_LONG, is_fk_eligible=False,
                   pii_types=[PiiType.FREE_TEXT_PII], pii_rate=0.06,
                   generator_kind="free_text_pii"),
        ColumnSpec(name="is_verified", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

# ---------------------------------------------------------------------------
# Noise tables
# ---------------------------------------------------------------------------

AUDIT_LOG = TableSpec(
    name="audit_log",
    row_count=500_000,
    excluded=True,
    exclusion_reason="log_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="table_name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="operation", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="user_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.05),
        ColumnSpec(name="old_values", type_class=TypeClass.STRING_LONG, is_fk_eligible=False),
        ColumnSpec(name="new_values", type_class=TypeClass.STRING_LONG, is_fk_eligible=False),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

ACCESS_LOG = TableSpec(
    name="access_log",
    row_count=500_000,
    excluded=True,
    exclusion_reason="log_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="user_id", type_class=TypeClass.INT_WIDE, nullable=True, null_pct=0.1),
        ColumnSpec(name="ip_address", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="method", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="path", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False),
        ColumnSpec(name="status_code", type_class=TypeClass.INT_NARROW),
        ColumnSpec(name="response_time_ms", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

TEMP_IMPORT_BATCH = TableSpec(
    name="temp_import_batch",
    row_count=10_000,
    excluded=True,
    exclusion_reason="temp_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="batch_id", type_class=TypeClass.UUID, generator_kind="uuid"),
        ColumnSpec(name="source_system", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="record_data", type_class=TypeClass.STRING_LONG, is_fk_eligible=False),
        ColumnSpec(name="status", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

TMP_STAGING_ORDERS = TableSpec(
    name="tmp_staging_orders",
    row_count=5_000,
    excluded=True,
    exclusion_reason="tmp_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="external_order_id", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="raw_json", type_class=TypeClass.STRING_LONG, is_fk_eligible=False),
        ColumnSpec(name="processed", type_class=TypeClass.BOOL),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

ORDERS_BAK_20240101 = TableSpec(
    name="orders_bak_20240101",
    row_count=100_000,
    excluded=True,
    exclusion_reason="bak_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="customer_id", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="total_amount", type_class=TypeClass.FLOAT),
        ColumnSpec(name="status", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

CUSTOMERS_ARCHIVE = TableSpec(
    name="customers_archive",
    row_count=20_000,
    excluded=True,
    exclusion_reason="archive_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="first_name", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.FIRST_NAME], pii_rate=1.0),
        ColumnSpec(name="last_name", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.LAST_NAME], pii_rate=1.0),
        ColumnSpec(name="email", type_class=TypeClass.STRING_SHORT, pii_types=[PiiType.EMAIL], pii_rate=1.0),
        ColumnSpec(name="archived_at", type_class=TypeClass.TIMESTAMP),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

USER_EVENTS = TableSpec(
    name="user_events",
    row_count=1_000_000,
    excluded=True,
    exclusion_reason="events_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="user_id", type_class=TypeClass.INT_WIDE),
        ColumnSpec(name="event_type", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="event_data", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False),
        ColumnSpec(name="session_id", type_class=TypeClass.UUID),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

ETL_IMPORT_QUEUE = TableSpec(
    name="etl_import_queue",
    row_count=500,
    excluded=True,
    exclusion_reason="etl_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="source_table", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="target_table", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="status", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="error_msg", type_class=TypeClass.STRING_SHORT, nullable=True, null_pct=0.8, is_fk_eligible=False),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

MIGRATIONS = TableSpec(
    name="migrations",
    row_count=50,
    excluded=True,
    exclusion_reason="migrations_pattern",
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_NARROW, is_pk=True, generator_kind="sequential"),
        ColumnSpec(name="version", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="description", type_class=TypeClass.STRING_SHORT, is_fk_eligible=False),
        ColumnSpec(name="applied_at", type_class=TypeClass.TIMESTAMP),
    ]
)

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

WIDE_DENORMALIZED = TableSpec(
    name="wide_denormalized",
    row_count=1_000,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True, generator_kind="sequential"),
        # 249 additional columns generated dynamically in the generator
    ]
    + [
        ColumnSpec(name=f"col_{i:03d}", type_class=TypeClass.FLOAT, nullable=(i % 5 == 0), null_pct=0.05)
        for i in range(1, 250)
    ]
)

EMPTY_TABLE = TableSpec(
    name="empty_table",
    row_count=0,
    columns=[
        ColumnSpec(name="id", type_class=TypeClass.INT_WIDE, is_pk=True),
        ColumnSpec(name="name", type_class=TypeClass.STRING_SHORT),
        ColumnSpec(name="created_at", type_class=TypeClass.TIMESTAMP),
    ]
)

# ---------------------------------------------------------------------------
# Registry — ordered by dependency layer
# ---------------------------------------------------------------------------

ALL_TABLES: list[TableSpec] = [
    # L0 — no FK deps
    CUSTOMERS,
    CATEGORIES,
    ROLES,
    WAREHOUSES,
    EMPLOYEE_RECORDS,
    # L1 — depend on L0
    ADDRESSES,
    PRODUCTS,
    USERS,
    INVENTORY,
    WAREHOUSE_STOCK,
    DEPARTMENTS,
    # L2 — depend on L0/L1
    ORDERS,
    USER_SESSIONS,
    API_TOKENS,
    USER_ROLES,
    TICKETS,
    # L3 — depend on L2
    ORDER_ITEMS,
    TICKET_MESSAGES,
    REVIEWS,
    # L4 — depends on L3 (order subtotals)
    PAYMENTS,
    # Noise
    AUDIT_LOG,
    ACCESS_LOG,
    TEMP_IMPORT_BATCH,
    TMP_STAGING_ORDERS,
    ORDERS_BAK_20240101,
    CUSTOMERS_ARCHIVE,
    USER_EVENTS,
    ETL_IMPORT_QUEUE,
    MIGRATIONS,
    # Edge
    WIDE_DENORMALIZED,
    EMPTY_TABLE,
]

TABLE_REGISTRY: dict[str, TableSpec] = {t.name: t for t in ALL_TABLES}
