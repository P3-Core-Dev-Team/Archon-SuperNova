"""
Seed an AdventureWorks-shaped dataset into Postgres schema ``adv``.

Differences from the prior seeders (HR / e-commerce):

* **Primary keys are declared** via ``PRIMARY KEY`` constraints in CREATE TABLE.
  This is the realistic AdventureWorks shape — every table has a declared PK.
  Phase 1 inventory will populate ``col_inventory.is_pk = true`` for these
  columns, which lets the ``require_parent_pk`` precision gate function as
  designed (one of the tests AW is uniquely good for).
* **Foreign keys are NOT declared.** The pipeline's job is to discover them.
* AdventureWorks-style table names (``Person``, ``BusinessEntity``,
  ``SalesOrderHeader``, etc., lower-cased per Postgres convention) and
  thematic FK structure: ``BusinessEntity`` is a parent of ``Person`` /
  ``Vendor`` / ``Store``; ``SalesOrderHeader`` joins ``Customer`` ↔
  ``Address`` ↔ ``CreditCard`` ↔ ``SalesPerson``.

Total: ~36 thematic tables, ~50+ undeclared FK relationships, ~700K rows.

Usage:
    python3 seed_postgres_adv.py
    python3 seed_postgres_adv.py --schema adv --reset
"""
from __future__ import annotations

import argparse
import random
import secrets
import string
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from seed_postgres_500 import (
    DSN, FIRST_NAMES, LAST_NAMES, CITIES, COUNTRIES, CURRENCIES, SEED,
    Table, conn_cur, copy_rows, gen_email, gen_iban, gen_phone_e164,
    luhn_card,
)

random.seed(SEED)

CTX: dict[str, list] = {}


# ---------------------------------------------------------- generators

def t_country_region(rng: random.Random):
    countries_list = [("US", "United States"), ("CA", "Canada"),
                      ("GB", "United Kingdom"), ("DE", "Germany"),
                      ("FR", "France"), ("AU", "Australia"),
                      ("JP", "Japan"), ("MX", "Mexico"),
                      ("BR", "Brazil"), ("IN", "India"),
                      ("SG", "Singapore"), ("NL", "Netherlands"),
                      ("ES", "Spain"), ("IT", "Italy"),
                      ("KR", "South Korea")]
    rows = [(c, n, datetime.now(timezone.utc)) for c, n in countries_list]
    CTX["country_codes"] = [r[0] for r in rows]
    return rows


def t_state_province(rng: random.Random):
    rows = []
    next_id = 1
    for cc in CTX["country_codes"]:
        n_states = rng.randint(8, 30)
        for i in range(n_states):
            code = "".join(rng.choices(string.ascii_uppercase, k=2))
            rows.append((next_id, code, cc, f"State{i:02d}",
                         datetime.now(timezone.utc)))
            next_id += 1
    CTX["state_province_ids"] = [r[0] for r in rows]
    return rows


def t_address_type(rng: random.Random):
    types = ["Home", "Main Office", "Billing", "Shipping", "Archive"]
    rows = [(i + 1, n, datetime.now(timezone.utc))
            for i, n in enumerate(types)]
    CTX["address_type_ids"] = [r[0] for r in rows]
    return rows


def t_address(rng: random.Random):
    rows = []
    sps = CTX["state_province_ids"]
    for i in range(1, 30001):
        rows.append((
            i,
            f"{rng.randint(1, 9999)} {rng.choice(LAST_NAMES).title()} St",
            None,
            rng.choice(CITIES),
            rng.choice(sps),
            f"{rng.randint(10000, 99999)}",
            datetime.now(timezone.utc),
        ))
    CTX["address_ids"] = [r[0] for r in rows]
    return rows


def t_business_entity(rng: random.Random):
    rows = [(i, datetime.now(timezone.utc)) for i in range(1, 30001)]
    CTX["business_entity_ids"] = [r[0] for r in rows]
    return rows


def t_business_entity_address(rng: random.Random):
    bes = CTX["business_entity_ids"]
    addrs = CTX["address_ids"]
    ats = CTX["address_type_ids"]
    rows = []
    for i, be in enumerate(bes[:25000], start=1):
        rows.append((be, rng.choice(addrs), rng.choice(ats),
                     datetime.now(timezone.utc)))
    return rows


def t_person(rng: random.Random):
    bes = CTX["business_entity_ids"][:25000]
    rows = []
    for be in bes:
        first = rng.choice(FIRST_NAMES).title()
        last = rng.choice(LAST_NAMES).title()
        rows.append((
            be,
            rng.choice(["IN", "EM", "SP", "SC", "VC", "GC"]),
            rng.choice(["true", "false"]),
            None,
            first,
            None,
            last,
            None,
            rng.randint(0, 5),
            datetime.now(timezone.utc),
        ))
    CTX["person_ids"] = bes
    return rows


def t_email_address(rng: random.Random):
    """One row per Person (subset)."""
    persons = CTX["person_ids"]
    rows = []
    next_id = 1
    for be in persons[:20000]:
        rows.append((
            be, next_id,
            gen_email(rng, rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)),
            datetime.now(timezone.utc),
        ))
        next_id += 1
    return rows


def t_person_phone(rng: random.Random):
    persons = CTX["person_ids"]
    rows = []
    for be in persons[:18000]:
        rows.append((
            be, gen_phone_e164(rng), 1,
            datetime.now(timezone.utc),
        ))
    return rows


def t_phone_number_type(rng: random.Random):
    types = ["Cell", "Home", "Work"]
    return [(i + 1, n, datetime.now(timezone.utc))
            for i, n in enumerate(types)]


def t_password(rng: random.Random):
    persons = CTX["person_ids"]
    rows = []
    for be in persons[:15000]:
        rows.append((
            be,
            secrets.token_hex(64),       # password_hash (high entropy)
            secrets.token_hex(8),        # password_salt
            datetime.now(timezone.utc),
        ))
    return rows


def t_department(rng: random.Random):
    depts = ["Engineering", "Tool Design", "Sales", "Marketing",
             "Purchasing", "Research and Development", "Production",
             "Production Control", "Human Resources", "Finance",
             "Information Services", "Document Control", "Quality Assurance",
             "Facilities and Maintenance", "Shipping and Receiving",
             "Executive"]
    rows = [(i + 1, n, rng.choice(["Manufacturing", "Sales and Marketing",
                                    "Engineering", "Inventory Management",
                                    "Research and Development",
                                    "Quality Assurance", "Executive General"]),
             datetime.now(timezone.utc))
            for i, n in enumerate(depts)]
    CTX["department_ids"] = [r[0] for r in rows]
    return rows


def t_shift(rng: random.Random):
    shifts = [("Day", "07:00:00", "15:00:00"),
              ("Evening", "15:00:00", "23:00:00"),
              ("Night", "23:00:00", "07:00:00")]
    rows = [(i + 1, n, s, e, datetime.now(timezone.utc))
            for i, (n, s, e) in enumerate(shifts)]
    CTX["shift_ids"] = [r[0] for r in rows]
    return rows


def t_employee(rng: random.Random):
    """Employee.BusinessEntityID is both PK and FK to Person.BusinessEntityID — 1:1."""
    persons = CTX["person_ids"][:1000]
    rows = []
    for be in persons:
        rows.append((
            be,
            f"NID{be:06d}",                        # national_id_number
            f"acme\\user{be}",                      # login_id
            f"OU=Engineering;OU=Adventure Works",  # organizational_node
            rng.randint(0, 4),                      # organization_level
            f"Engineer {rng.randint(1, 5)}",       # job_title
            (date.today() - timedelta(days=rng.randint(22*365, 65*365))),  # birth_date
            rng.choice(["S", "M"]),                # marital_status
            rng.choice(["M", "F"]),                # gender
            (date.today() - timedelta(days=rng.randint(30, 365*30))),  # hire_date
            rng.choice(["true", "false"]),          # salaried_flag
            rng.randint(0, 99),                     # vacation_hours
            rng.randint(0, 80),                     # sick_leave_hours
            "true",                                # current_flag
            datetime.now(timezone.utc),
        ))
    CTX["employee_ids"] = persons
    return rows


def t_employee_department_history(rng: random.Random):
    emps = CTX["employee_ids"]
    deps = CTX["department_ids"]
    shs = CTX["shift_ids"]
    rows = []
    next_id = 1
    for emp in emps:
        n_changes = rng.choice([1, 1, 2])
        for _ in range(n_changes):
            start = date.today() - timedelta(days=rng.randint(30, 365*15))
            end = (start + timedelta(days=rng.randint(180, 365*4))
                   if rng.random() < 0.6 else None)
            rows.append((next_id, emp, rng.choice(deps), rng.choice(shs),
                         start, end, datetime.now(timezone.utc)))
            next_id += 1
    return rows


def t_employee_pay_history(rng: random.Random):
    emps = CTX["employee_ids"]
    rows = []
    next_id = 1
    for emp in emps:
        n_changes = rng.choice([1, 2, 3])
        for _ in range(n_changes):
            rows.append((
                next_id, emp,
                date.today() - timedelta(days=rng.randint(30, 365*10)),
                round(rng.uniform(20.0, 150.0), 4),    # rate
                rng.choice([1, 2, 3]),                  # pay_frequency
                datetime.now(timezone.utc),
            ))
            next_id += 1
    return rows


def t_product_category(rng: random.Random):
    cats = ["Bikes", "Components", "Clothing", "Accessories"]
    rows = [(i + 1, n, datetime.now(timezone.utc))
            for i, n in enumerate(cats)]
    CTX["product_category_ids"] = [r[0] for r in rows]
    return rows


def t_product_subcategory(rng: random.Random):
    cats = CTX["product_category_ids"]
    sub_names = ["Mountain Bikes", "Road Bikes", "Touring Bikes",
                 "Handlebars", "Saddles", "Wheels", "Tires", "Brakes",
                 "Cranksets", "Chains", "Pedals",
                 "Bib-Shorts", "Caps", "Gloves", "Jerseys", "Shorts",
                 "Socks", "Tights", "Vests",
                 "Bike Racks", "Bike Stands", "Bottles and Cages",
                 "Cleaners", "Fenders", "Helmets", "Hydration Packs",
                 "Lights", "Locks", "Pumps", "Tires and Tubes"]
    rows = []
    for i, name in enumerate(sub_names, start=1):
        rows.append((i, rng.choice(cats), name, datetime.now(timezone.utc)))
    CTX["product_subcategory_ids"] = [r[0] for r in rows]
    return rows


def t_product_model(rng: random.Random):
    names = [f"Model {i:03d}" for i in range(1, 121)]
    rows = []
    for i, n in enumerate(names, start=1):
        rows.append((i, n, None, None, datetime.now(timezone.utc)))
    CTX["product_model_ids"] = [r[0] for r in rows]
    return rows


def t_product(rng: random.Random):
    subs = CTX["product_subcategory_ids"]
    models = CTX["product_model_ids"]
    rows = []
    for i in range(1, 501):
        rows.append((
            i,
            f"Product {i}",
            f"PN-{i:06d}",
            "true",
            "false",
            rng.randint(0, 1000),
            rng.randint(500, 1500),
            round(rng.uniform(5, 5000), 4),
            round(rng.uniform(1, 4500), 4),
            f"SZ-{rng.randint(38, 62)}",
            "CM",
            round(rng.uniform(0.1, 50), 2),
            "G",
            rng.randint(0, 365),                  # days_to_manufacture
            rng.choice(["L", "M", "H"]),           # product_line
            rng.choice(["L", "M", "H"]),           # class
            rng.choice(["S", "M", "T"]),           # style
            rng.choice(subs),
            rng.choice(models),
            (date.today() - timedelta(days=rng.randint(0, 365*5))),
            None,
            datetime.now(timezone.utc),
        ))
    CTX["product_ids"] = [r[0] for r in rows]
    return rows


def t_location(rng: random.Random):
    names = ["Tool Crib", "Sheet Metal Racks", "Paint Shop", "Paint Storage",
             "Metal Storage", "Miscellaneous Storage", "Frame Forming",
             "Frame Welding", "Debur and Polish", "Paint", "Specialized Paint",
             "Subassembly", "Final Assembly", "Mechanical", "Finished Goods Storage"]
    rows = []
    for i, n in enumerate(names, start=1):
        rows.append((i, n, round(rng.uniform(10.0, 30.0), 4),
                     round(rng.uniform(2.0, 16.0), 4),
                     datetime.now(timezone.utc)))
    CTX["location_ids"] = [r[0] for r in rows]
    return rows


def t_product_inventory(rng: random.Random):
    prods = CTX["product_ids"]
    locs = CTX["location_ids"]
    rows = []
    for prod in prods:
        for loc in rng.sample(locs, k=rng.randint(1, 4)):
            rows.append((prod, loc, f"AB{rng.randint(1, 9999):04d}",
                         rng.randint(0, 5),
                         rng.randint(0, 1000),
                         datetime.now(timezone.utc)))
    return rows


def t_product_review(rng: random.Random):
    prods = CTX["product_ids"]
    rows = []
    for i in range(1, 1001):
        rows.append((
            i, rng.choice(prods),
            f"{rng.choice(FIRST_NAMES).title()} {rng.choice(LAST_NAMES).title()}",
            datetime.now(timezone.utc),
            gen_email(rng, "rev", "iewer"),
            rng.randint(1, 5),
            f"Review comment {i}",
            datetime.now(timezone.utc),
        ))
    return rows


def t_product_cost_history(rng: random.Random):
    prods = CTX["product_ids"]
    rows = []
    for prod in prods:
        for _ in range(rng.randint(1, 3)):
            rows.append((
                prod,
                date.today() - timedelta(days=rng.randint(30, 365*5)),
                date.today() - timedelta(days=rng.randint(0, 60))
                  if rng.random() < 0.5 else None,
                round(rng.uniform(1.0, 4500.0), 4),
                datetime.now(timezone.utc),
            ))
    return rows


def t_vendor(rng: random.Random):
    """Vendor.BusinessEntityID is both PK and FK to BusinessEntity."""
    bes = CTX["business_entity_ids"][25000:25200]   # 200 vendors
    rows = []
    for be in bes:
        rows.append((
            be,
            f"VEND{be:06d}",
            f"Vendor {be}",
            rng.randint(1, 5),
            "true",
            "true",
            None,
            datetime.now(timezone.utc),
        ))
    CTX["vendor_ids"] = bes
    return rows


def t_product_vendor(rng: random.Random):
    prods = CTX["product_ids"]
    vens = CTX["vendor_ids"]
    rows = []
    for prod in prods:
        for ven in rng.sample(vens, k=rng.randint(1, 3)):
            rows.append((
                prod, ven,
                rng.randint(0, 1000),
                rng.randint(0, 365),
                rng.randint(1, 1000),
                rng.randint(1, 4),
                round(rng.uniform(1.0, 4500.0), 4),
                None,
                "EA",
                datetime.now(timezone.utc),
            ))
    return rows


def t_ship_method(rng: random.Random):
    methods = [("XRQ - TRUCK GROUND", 3.95, 0.99),
               ("ZY - EXPRESS", 9.95, 1.99),
               ("OVERNIGHT J-FAST", 21.95, 4.99),
               ("OVERSEAS - DELUXE", 29.95, 9.99),
               ("CARGO TRANSPORT 5", 8.99, 1.49)]
    rows = []
    for i, (n, base, rate) in enumerate(methods, start=1):
        rows.append((i, n, base, rate, datetime.now(timezone.utc)))
    CTX["ship_method_ids"] = [r[0] for r in rows]
    return rows


def t_purchase_order_header(rng: random.Random):
    emps = CTX["employee_ids"][:200]
    vens = CTX["vendor_ids"]
    sms = CTX["ship_method_ids"]
    rows = []
    for i in range(1, 5001):
        rows.append((
            i,
            rng.randint(1, 4),                      # revision_number
            rng.randint(1, 4),                      # status
            rng.choice(emps),
            rng.choice(vens),
            rng.choice(sms),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365*3)),
            None,
            round(rng.uniform(1000, 100000), 4),
            round(rng.uniform(50, 8000), 4),
            round(rng.uniform(20, 3000), 4),
            datetime.now(timezone.utc),
        ))
    CTX["purchase_order_ids"] = [r[0] for r in rows]
    return rows


def t_purchase_order_detail(rng: random.Random):
    pohs = CTX["purchase_order_ids"]
    prods = CTX["product_ids"]
    rows = []
    next_id = 1
    for poh in pohs:
        n = rng.randint(1, 5)
        for _ in range(n):
            rows.append((
                poh, next_id,
                date.today() - timedelta(days=rng.randint(0, 30)),
                rng.randint(1, 100),
                rng.choice(prods),
                round(rng.uniform(1.0, 1000.0), 4),
                round(rng.uniform(10.0, 50000.0), 4),
                rng.randint(0, 100),
                rng.randint(0, 50),
                datetime.now(timezone.utc),
            ))
            next_id += 1
    return rows


def t_sales_territory(rng: random.Random):
    territories = [("Northwest", "US"), ("Northeast", "US"),
                   ("Central", "US"), ("Southwest", "US"),
                   ("Southeast", "US"), ("Canada", "CA"),
                   ("France", "FR"), ("Germany", "DE"),
                   ("Australia", "AU"), ("United Kingdom", "GB")]
    rows = []
    for i, (n, cc) in enumerate(territories, start=1):
        rows.append((
            i, n, cc,
            rng.choice(["NA", "EU", "PAC"]),
            round(rng.uniform(1e7, 1e9), 4),
            round(rng.uniform(1e7, 1e9), 4),
            rng.randint(100, 10000),
            rng.randint(80, 9000),
            datetime.now(timezone.utc),
        ))
    CTX["territory_ids"] = [r[0] for r in rows]
    return rows


def t_sales_person(rng: random.Random):
    """Subset of Employee."""
    emps = rng.sample(CTX["employee_ids"], k=20)
    terrs = CTX["territory_ids"]
    rows = []
    for emp in emps:
        rows.append((
            emp,
            rng.choice(terrs),
            round(rng.uniform(50000, 250000), 4),     # sales_quota
            round(rng.uniform(5000, 50000), 4),       # bonus
            round(rng.uniform(0.05, 0.20), 4),        # commission_pct
            round(rng.uniform(1e6, 1e8), 4),          # sales_ytd
            round(rng.uniform(1e6, 1e8), 4),          # sales_lastyear
            datetime.now(timezone.utc),
        ))
    CTX["sales_person_ids"] = emps
    return rows


def t_credit_card(rng: random.Random):
    """CreditCard.card_number is Luhn-valid (PII)."""
    rows = []
    for i in range(1, 20001):
        rows.append((
            i,
            rng.choice(["Vista", "Distinguish", "ColonialVoice", "SuperiorCard"]),
            luhn_card(rng),                            # card_number (PII!)
            rng.randint(1, 12),
            2024 + rng.randint(0, 6),
            datetime.now(timezone.utc),
        ))
    CTX["credit_card_ids"] = [r[0] for r in rows]
    return rows


def t_person_credit_card(rng: random.Random):
    persons = CTX["person_ids"]
    ccs = CTX["credit_card_ids"]
    rows = []
    for cc in ccs:
        rows.append((
            rng.choice(persons), cc, datetime.now(timezone.utc),
        ))
    return rows


def t_store(rng: random.Random):
    bes = CTX["business_entity_ids"][25200:25900]   # 700 stores
    sps = CTX["sales_person_ids"]
    rows = []
    for be in bes:
        rows.append((
            be,
            f"Store {be}",
            rng.choice(sps),
            None,
            datetime.now(timezone.utc),
        ))
    CTX["store_ids"] = bes
    return rows


def t_customer(rng: random.Random):
    persons = CTX["person_ids"]
    stores = CTX["store_ids"]
    terrs = CTX["territory_ids"]
    rows = []
    for i in range(1, 20001):
        # 60% individual customers (PersonID), 40% stores (StoreID)
        if rng.random() < 0.6:
            person = rng.choice(persons)
            store = None
        else:
            person = None
            store = rng.choice(stores)
        rows.append((
            i, person, store, rng.choice(terrs),
            f"AW{i:08d}",                              # account_number
            datetime.now(timezone.utc),
        ))
    CTX["customer_ids"] = [r[0] for r in rows]
    return rows


def t_special_offer(rng: random.Random):
    descs = ["No Discount", "Volume Discount 11 to 14",
             "Volume Discount 15 to 24", "Volume Discount 25 to 40",
             "Volume Discount 41 to 60", "Volume Discount over 60",
             "Mountain-100 Clearance Sale", "Sport Helmet Discount-2002",
             "Road-650 Overstock"]
    rows = []
    for i, d in enumerate(descs, start=1):
        rows.append((
            i, d,
            round(rng.uniform(0, 0.50), 4),           # discount_pct
            rng.choice(["No Discount", "Reseller", "Customer", "Volume"]),
            rng.choice(["All", "Reseller", "Retail", "Specialty"]),
            rng.randint(0, 999) if i > 1 else 0,
            rng.randint(1000, 10000) if i > 1 else 1,
            datetime.now(timezone.utc) - timedelta(days=rng.randint(60, 365*3)),
            datetime.now(timezone.utc) + timedelta(days=rng.randint(60, 365*2)),
            datetime.now(timezone.utc),
        ))
    CTX["special_offer_ids"] = [r[0] for r in rows]
    return rows


def t_special_offer_product(rng: random.Random):
    sofs = CTX["special_offer_ids"]
    prods = CTX["product_ids"]
    rows = []
    for sof in sofs:
        for prod in rng.sample(prods, k=rng.randint(5, 20)):
            rows.append((sof, prod, datetime.now(timezone.utc)))
    return rows


def t_sales_order_header(rng: random.Random):
    custs = CTX["customer_ids"]
    sps = CTX["sales_person_ids"]
    terrs = CTX["territory_ids"]
    addrs = CTX["address_ids"]
    ccs = CTX["credit_card_ids"]
    sms = CTX["ship_method_ids"]
    rows = []
    for i in range(1, 30001):
        rows.append((
            i,
            rng.randint(1, 8),                         # revision_number
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365*3)),
            datetime.now(timezone.utc),
            datetime.now(timezone.utc) + timedelta(days=rng.randint(2, 10)),
            rng.choice([5, 5, 5, 5, 1, 2, 3, 4, 6]),
            "true",
            f"SO{i:08d}",                              # sales_order_number
            None, None,
            rng.choice(custs),
            rng.choice(sps) if rng.random() < 0.5 else None,
            rng.choice(terrs),
            rng.choice(addrs),                         # bill_to_address_id
            rng.choice(addrs),                         # ship_to_address_id
            rng.choice(sms),
            rng.choice(ccs),                           # credit_card_id
            f"approval-{i:06d}",
            None,
            "USD",
            round(rng.uniform(50, 5000), 4),
            round(rng.uniform(10, 500), 4),
            round(rng.uniform(5, 200), 4),
            datetime.now(timezone.utc),
        ))
    CTX["sales_order_ids"] = [r[0] for r in rows]
    return rows


def t_sales_order_detail(rng: random.Random):
    sohs = CTX["sales_order_ids"]
    prods = CTX["product_ids"]
    sofs = CTX["special_offer_ids"]
    rows = []
    next_id = 1
    for soh in sohs:
        n = rng.randint(1, 5)
        for _ in range(n):
            rows.append((
                soh, next_id,
                f"CARRIER-{rng.randint(1, 99)}",       # carrier_tracking_number
                rng.randint(1, 30),
                rng.choice(prods),
                rng.choice(sofs),
                round(rng.uniform(1.0, 5000.0), 4),
                round(rng.uniform(0, 0.5), 4),
                round(rng.uniform(10.0, 50000.0), 4),
                datetime.now(timezone.utc),
            ))
            next_id += 1
    return rows


def t_sales_reason(rng: random.Random):
    rs = ["Price", "Manufacturer", "Quality", "Review", "Promotion",
          "Magazine Advertisement", "Television Advertisement", "Sponsorship",
          "Demo Event", "On Promotion"]
    rows = []
    for i, n in enumerate(rs, start=1):
        rows.append((i, n, rng.choice(["Marketing", "Other"]),
                     datetime.now(timezone.utc)))
    CTX["sales_reason_ids"] = [r[0] for r in rows]
    return rows


def t_sales_order_header_sales_reason(rng: random.Random):
    sohs = CTX["sales_order_ids"]
    srs = CTX["sales_reason_ids"]
    rows = []
    for soh in rng.sample(sohs, k=15000):
        for sr in rng.sample(srs, k=rng.randint(1, 3)):
            rows.append((soh, sr, datetime.now(timezone.utc)))
    return rows


# ----------------------------------------------------------- Table specs
# NOTE: every primary key is declared via PRIMARY KEY constraint so Phase 1
# inventory captures `is_pk = true`. FKs are NOT declared.

THEMATIC: list[Table] = [
    Table("country_region",
          '''CREATE TABLE IF NOT EXISTS "country_region" (
              country_region_code VARCHAR(3) PRIMARY KEY,
              name TEXT NOT NULL,
              modified_date TIMESTAMPTZ)''',
          t_country_region,
          ["country_region_code", "name", "modified_date"]),
    Table("state_province",
          '''CREATE TABLE IF NOT EXISTS "state_province" (
              state_province_id INTEGER PRIMARY KEY,
              state_province_code VARCHAR(3),
              country_region_code VARCHAR(3),
              name TEXT NOT NULL,
              modified_date TIMESTAMPTZ)''',
          t_state_province,
          ["state_province_id", "state_province_code", "country_region_code",
           "name", "modified_date"]),
    Table("address_type",
          '''CREATE TABLE IF NOT EXISTS "address_type" (
              address_type_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              modified_date TIMESTAMPTZ)''',
          t_address_type,
          ["address_type_id", "name", "modified_date"]),
    Table("address",
          '''CREATE TABLE IF NOT EXISTS "address" (
              address_id INTEGER PRIMARY KEY,
              address_line1 TEXT,
              address_line2 TEXT,
              city TEXT,
              state_province_id INTEGER,
              postal_code VARCHAR(15),
              modified_date TIMESTAMPTZ)''',
          t_address,
          ["address_id", "address_line1", "address_line2", "city",
           "state_province_id", "postal_code", "modified_date"]),
    Table("business_entity",
          '''CREATE TABLE IF NOT EXISTS "business_entity" (
              business_entity_id INTEGER PRIMARY KEY,
              modified_date TIMESTAMPTZ)''',
          t_business_entity,
          ["business_entity_id", "modified_date"]),
    Table("business_entity_address",
          '''CREATE TABLE IF NOT EXISTS "business_entity_address" (
              business_entity_id INTEGER,
              address_id INTEGER,
              address_type_id INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (business_entity_id, address_id, address_type_id))''',
          t_business_entity_address,
          ["business_entity_id", "address_id", "address_type_id", "modified_date"]),
    Table("person",
          '''CREATE TABLE IF NOT EXISTS "person" (
              business_entity_id INTEGER PRIMARY KEY,
              person_type VARCHAR(2),
              name_style VARCHAR(8),
              title TEXT,
              first_name TEXT,
              middle_name TEXT,
              last_name TEXT,
              suffix TEXT,
              email_promotion INTEGER,
              modified_date TIMESTAMPTZ)''',
          t_person,
          ["business_entity_id", "person_type", "name_style", "title",
           "first_name", "middle_name", "last_name", "suffix",
           "email_promotion", "modified_date"]),
    Table("email_address",
          '''CREATE TABLE IF NOT EXISTS "email_address" (
              business_entity_id INTEGER,
              email_address_id INTEGER,
              email_address VARCHAR(128),
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (business_entity_id, email_address_id))''',
          t_email_address,
          ["business_entity_id", "email_address_id", "email_address", "modified_date"]),
    Table("person_phone",
          '''CREATE TABLE IF NOT EXISTS "person_phone" (
              business_entity_id INTEGER,
              phone_number VARCHAR(32),
              phone_number_type_id INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (business_entity_id, phone_number, phone_number_type_id))''',
          t_person_phone,
          ["business_entity_id", "phone_number", "phone_number_type_id", "modified_date"]),
    Table("phone_number_type",
          '''CREATE TABLE IF NOT EXISTS "phone_number_type" (
              phone_number_type_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              modified_date TIMESTAMPTZ)''',
          t_phone_number_type,
          ["phone_number_type_id", "name", "modified_date"]),
    Table("password",
          '''CREATE TABLE IF NOT EXISTS "password" (
              business_entity_id INTEGER PRIMARY KEY,
              password_hash TEXT,
              password_salt TEXT,
              modified_date TIMESTAMPTZ)''',
          t_password,
          ["business_entity_id", "password_hash", "password_salt", "modified_date"]),
    Table("department",
          '''CREATE TABLE IF NOT EXISTS "department" (
              department_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              group_name TEXT,
              modified_date TIMESTAMPTZ)''',
          t_department,
          ["department_id", "name", "group_name", "modified_date"]),
    Table("shift",
          '''CREATE TABLE IF NOT EXISTS "shift" (
              shift_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              start_time VARCHAR(8),
              end_time VARCHAR(8),
              modified_date TIMESTAMPTZ)''',
          t_shift,
          ["shift_id", "name", "start_time", "end_time", "modified_date"]),
    Table("employee",
          '''CREATE TABLE IF NOT EXISTS "employee" (
              business_entity_id INTEGER PRIMARY KEY,
              national_id_number VARCHAR(32),
              login_id TEXT,
              organizational_node TEXT,
              organization_level INTEGER,
              job_title TEXT,
              birth_date DATE,
              marital_status VARCHAR(2),
              gender VARCHAR(2),
              hire_date DATE,
              salaried_flag VARCHAR(8),
              vacation_hours INTEGER,
              sick_leave_hours INTEGER,
              current_flag VARCHAR(8),
              modified_date TIMESTAMPTZ)''',
          t_employee,
          ["business_entity_id", "national_id_number", "login_id",
           "organizational_node", "organization_level", "job_title",
           "birth_date", "marital_status", "gender", "hire_date",
           "salaried_flag", "vacation_hours", "sick_leave_hours",
           "current_flag", "modified_date"]),
    Table("employee_department_history",
          '''CREATE TABLE IF NOT EXISTS "employee_department_history" (
              edh_id INTEGER PRIMARY KEY,
              business_entity_id INTEGER,
              department_id INTEGER,
              shift_id INTEGER,
              start_date DATE,
              end_date DATE,
              modified_date TIMESTAMPTZ)''',
          t_employee_department_history,
          ["edh_id", "business_entity_id", "department_id", "shift_id",
           "start_date", "end_date", "modified_date"]),
    Table("employee_pay_history",
          '''CREATE TABLE IF NOT EXISTS "employee_pay_history" (
              eph_id INTEGER PRIMARY KEY,
              business_entity_id INTEGER,
              rate_change_date DATE,
              rate NUMERIC(10,4),
              pay_frequency INTEGER,
              modified_date TIMESTAMPTZ)''',
          t_employee_pay_history,
          ["eph_id", "business_entity_id", "rate_change_date", "rate",
           "pay_frequency", "modified_date"]),
    Table("product_category",
          '''CREATE TABLE IF NOT EXISTS "product_category" (
              product_category_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              modified_date TIMESTAMPTZ)''',
          t_product_category,
          ["product_category_id", "name", "modified_date"]),
    Table("product_subcategory",
          '''CREATE TABLE IF NOT EXISTS "product_subcategory" (
              product_subcategory_id INTEGER PRIMARY KEY,
              product_category_id INTEGER,
              name TEXT NOT NULL,
              modified_date TIMESTAMPTZ)''',
          t_product_subcategory,
          ["product_subcategory_id", "product_category_id", "name",
           "modified_date"]),
    Table("product_model",
          '''CREATE TABLE IF NOT EXISTS "product_model" (
              product_model_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              catalog_description TEXT,
              instructions TEXT,
              modified_date TIMESTAMPTZ)''',
          t_product_model,
          ["product_model_id", "name", "catalog_description",
           "instructions", "modified_date"]),
    Table("product",
          '''CREATE TABLE IF NOT EXISTS "product" (
              product_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              product_number VARCHAR(32),
              make_flag VARCHAR(8),
              finished_goods_flag VARCHAR(8),
              safety_stock_level INTEGER,
              reorder_point INTEGER,
              standard_cost NUMERIC(12,4),
              list_price NUMERIC(12,4),
              size VARCHAR(10),
              size_unit_measure_code VARCHAR(8),
              weight NUMERIC(10,2),
              weight_unit_measure_code VARCHAR(8),
              days_to_manufacture INTEGER,
              product_line VARCHAR(2),
              class VARCHAR(2),
              style VARCHAR(2),
              product_subcategory_id INTEGER,
              product_model_id INTEGER,
              sell_start_date DATE,
              sell_end_date DATE,
              modified_date TIMESTAMPTZ)''',
          t_product,
          ["product_id", "name", "product_number", "make_flag",
           "finished_goods_flag", "safety_stock_level", "reorder_point",
           "standard_cost", "list_price", "size", "size_unit_measure_code",
           "weight", "weight_unit_measure_code", "days_to_manufacture",
           "product_line", "class", "style", "product_subcategory_id",
           "product_model_id", "sell_start_date", "sell_end_date",
           "modified_date"]),
    Table("location",
          '''CREATE TABLE IF NOT EXISTS "location" (
              location_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              cost_rate NUMERIC(10,4),
              availability NUMERIC(10,4),
              modified_date TIMESTAMPTZ)''',
          t_location,
          ["location_id", "name", "cost_rate", "availability", "modified_date"]),
    Table("product_inventory",
          '''CREATE TABLE IF NOT EXISTS "product_inventory" (
              product_id INTEGER,
              location_id INTEGER,
              shelf VARCHAR(10),
              bin INTEGER,
              quantity INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (product_id, location_id))''',
          t_product_inventory,
          ["product_id", "location_id", "shelf", "bin", "quantity",
           "modified_date"]),
    Table("product_review",
          '''CREATE TABLE IF NOT EXISTS "product_review" (
              product_review_id INTEGER PRIMARY KEY,
              product_id INTEGER,
              reviewer_name TEXT,
              review_date TIMESTAMPTZ,
              email_address VARCHAR(128),
              rating INTEGER,
              comments TEXT,
              modified_date TIMESTAMPTZ)''',
          t_product_review,
          ["product_review_id", "product_id", "reviewer_name", "review_date",
           "email_address", "rating", "comments", "modified_date"]),
    Table("product_cost_history",
          '''CREATE TABLE IF NOT EXISTS "product_cost_history" (
              product_id INTEGER,
              start_date DATE,
              end_date DATE,
              standard_cost NUMERIC(12,4),
              modified_date TIMESTAMPTZ)''',
          t_product_cost_history,
          ["product_id", "start_date", "end_date", "standard_cost",
           "modified_date"]),
    Table("vendor",
          '''CREATE TABLE IF NOT EXISTS "vendor" (
              business_entity_id INTEGER PRIMARY KEY,
              account_number VARCHAR(32),
              name TEXT,
              credit_rating INTEGER,
              preferred_vendor_status VARCHAR(8),
              active_flag VARCHAR(8),
              purchasing_web_service_url TEXT,
              modified_date TIMESTAMPTZ)''',
          t_vendor,
          ["business_entity_id", "account_number", "name", "credit_rating",
           "preferred_vendor_status", "active_flag",
           "purchasing_web_service_url", "modified_date"]),
    Table("product_vendor",
          '''CREATE TABLE IF NOT EXISTS "product_vendor" (
              product_id INTEGER,
              business_entity_id INTEGER,
              average_lead_time INTEGER,
              standard_price NUMERIC(12,4),
              last_receipt_cost NUMERIC(12,4),
              last_receipt_date DATE,
              min_order_qty INTEGER,
              max_order_qty INTEGER,
              on_order_qty INTEGER,
              unit_measure_code VARCHAR(8),
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (product_id, business_entity_id))''',
          t_product_vendor,
          ["product_id", "business_entity_id", "average_lead_time",
           "max_order_qty", "min_order_qty", "on_order_qty", "standard_price",
           "last_receipt_cost", "unit_measure_code", "modified_date"]),
    Table("ship_method",
          '''CREATE TABLE IF NOT EXISTS "ship_method" (
              ship_method_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              ship_base NUMERIC(10,4),
              ship_rate NUMERIC(10,4),
              modified_date TIMESTAMPTZ)''',
          t_ship_method,
          ["ship_method_id", "name", "ship_base", "ship_rate", "modified_date"]),
    Table("purchase_order_header",
          '''CREATE TABLE IF NOT EXISTS "purchase_order_header" (
              purchase_order_id INTEGER PRIMARY KEY,
              revision_number INTEGER,
              status INTEGER,
              employee_id INTEGER,
              vendor_id INTEGER,
              ship_method_id INTEGER,
              order_date TIMESTAMPTZ,
              ship_date TIMESTAMPTZ,
              sub_total NUMERIC(14,4),
              tax_amt NUMERIC(14,4),
              freight NUMERIC(14,4),
              modified_date TIMESTAMPTZ)''',
          t_purchase_order_header,
          ["purchase_order_id", "revision_number", "status", "employee_id",
           "vendor_id", "ship_method_id", "order_date", "ship_date",
           "sub_total", "tax_amt", "freight", "modified_date"]),
    Table("purchase_order_detail",
          '''CREATE TABLE IF NOT EXISTS "purchase_order_detail" (
              purchase_order_id INTEGER,
              purchase_order_detail_id INTEGER,
              due_date DATE,
              order_qty INTEGER,
              product_id INTEGER,
              unit_price NUMERIC(12,4),
              line_total NUMERIC(14,4),
              received_qty INTEGER,
              rejected_qty INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (purchase_order_id, purchase_order_detail_id))''',
          t_purchase_order_detail,
          ["purchase_order_id", "purchase_order_detail_id", "due_date",
           "order_qty", "product_id", "unit_price", "line_total",
           "received_qty", "rejected_qty", "modified_date"]),
    Table("sales_territory",
          '''CREATE TABLE IF NOT EXISTS "sales_territory" (
              territory_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              country_region_code VARCHAR(3),
              "group" TEXT,
              sales_ytd NUMERIC(14,4),
              sales_lastyear NUMERIC(14,4),
              cost_ytd INTEGER,
              cost_lastyear INTEGER,
              modified_date TIMESTAMPTZ)''',
          t_sales_territory,
          ["territory_id", "name", "country_region_code", "group",
           "sales_ytd", "sales_lastyear", "cost_ytd", "cost_lastyear",
           "modified_date"]),
    Table("sales_person",
          '''CREATE TABLE IF NOT EXISTS "sales_person" (
              business_entity_id INTEGER PRIMARY KEY,
              territory_id INTEGER,
              sales_quota NUMERIC(14,4),
              bonus NUMERIC(12,4),
              commission_pct NUMERIC(6,4),
              sales_ytd NUMERIC(14,4),
              sales_lastyear NUMERIC(14,4),
              modified_date TIMESTAMPTZ)''',
          t_sales_person,
          ["business_entity_id", "territory_id", "sales_quota", "bonus",
           "commission_pct", "sales_ytd", "sales_lastyear", "modified_date"]),
    Table("credit_card",
          '''CREATE TABLE IF NOT EXISTS "credit_card" (
              credit_card_id INTEGER PRIMARY KEY,
              card_type VARCHAR(32),
              card_number VARCHAR(32),
              exp_month INTEGER,
              exp_year INTEGER,
              modified_date TIMESTAMPTZ)''',
          t_credit_card,
          ["credit_card_id", "card_type", "card_number", "exp_month",
           "exp_year", "modified_date"]),
    Table("person_credit_card",
          '''CREATE TABLE IF NOT EXISTS "person_credit_card" (
              business_entity_id INTEGER,
              credit_card_id INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (business_entity_id, credit_card_id))''',
          t_person_credit_card,
          ["business_entity_id", "credit_card_id", "modified_date"]),
    Table("store",
          '''CREATE TABLE IF NOT EXISTS "store" (
              business_entity_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              sales_person_id INTEGER,
              demographics TEXT,
              modified_date TIMESTAMPTZ)''',
          t_store,
          ["business_entity_id", "name", "sales_person_id", "demographics",
           "modified_date"]),
    Table("customer",
          '''CREATE TABLE IF NOT EXISTS "customer" (
              customer_id INTEGER PRIMARY KEY,
              person_id INTEGER,
              store_id INTEGER,
              territory_id INTEGER,
              account_number VARCHAR(20),
              modified_date TIMESTAMPTZ)''',
          t_customer,
          ["customer_id", "person_id", "store_id", "territory_id",
           "account_number", "modified_date"]),
    Table("special_offer",
          '''CREATE TABLE IF NOT EXISTS "special_offer" (
              special_offer_id INTEGER PRIMARY KEY,
              description TEXT NOT NULL,
              discount_pct NUMERIC(6,4),
              type TEXT,
              category TEXT,
              min_qty INTEGER,
              max_qty INTEGER,
              start_date TIMESTAMPTZ,
              end_date TIMESTAMPTZ,
              modified_date TIMESTAMPTZ)''',
          t_special_offer,
          ["special_offer_id", "description", "discount_pct", "type",
           "category", "min_qty", "max_qty", "start_date", "end_date",
           "modified_date"]),
    Table("special_offer_product",
          '''CREATE TABLE IF NOT EXISTS "special_offer_product" (
              special_offer_id INTEGER,
              product_id INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (special_offer_id, product_id))''',
          t_special_offer_product,
          ["special_offer_id", "product_id", "modified_date"]),
    Table("sales_order_header",
          '''CREATE TABLE IF NOT EXISTS "sales_order_header" (
              sales_order_id INTEGER PRIMARY KEY,
              revision_number INTEGER,
              order_date TIMESTAMPTZ,
              due_date TIMESTAMPTZ,
              ship_date TIMESTAMPTZ,
              status INTEGER,
              online_order_flag VARCHAR(8),
              sales_order_number VARCHAR(32),
              purchase_order_number VARCHAR(32),
              account_number VARCHAR(32),
              customer_id INTEGER,
              sales_person_id INTEGER,
              territory_id INTEGER,
              bill_to_address_id INTEGER,
              ship_to_address_id INTEGER,
              ship_method_id INTEGER,
              credit_card_id INTEGER,
              credit_card_approval_code VARCHAR(32),
              currency_rate_id INTEGER,
              currency VARCHAR(8),
              sub_total NUMERIC(14,4),
              tax_amt NUMERIC(14,4),
              freight NUMERIC(14,4),
              modified_date TIMESTAMPTZ)''',
          t_sales_order_header,
          ["sales_order_id", "revision_number", "order_date", "due_date",
           "ship_date", "status", "online_order_flag", "sales_order_number",
           "purchase_order_number", "account_number", "customer_id",
           "sales_person_id", "territory_id", "bill_to_address_id",
           "ship_to_address_id", "ship_method_id", "credit_card_id",
           "credit_card_approval_code", "currency_rate_id", "currency",
           "sub_total", "tax_amt", "freight", "modified_date"]),
    Table("sales_order_detail",
          '''CREATE TABLE IF NOT EXISTS "sales_order_detail" (
              sales_order_id INTEGER,
              sales_order_detail_id INTEGER,
              carrier_tracking_number VARCHAR(32),
              order_qty INTEGER,
              product_id INTEGER,
              special_offer_id INTEGER,
              unit_price NUMERIC(12,4),
              unit_price_discount NUMERIC(6,4),
              line_total NUMERIC(14,4),
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (sales_order_id, sales_order_detail_id))''',
          t_sales_order_detail,
          ["sales_order_id", "sales_order_detail_id", "carrier_tracking_number",
           "order_qty", "product_id", "special_offer_id", "unit_price",
           "unit_price_discount", "line_total", "modified_date"]),
    Table("sales_reason",
          '''CREATE TABLE IF NOT EXISTS "sales_reason" (
              sales_reason_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              reason_type TEXT,
              modified_date TIMESTAMPTZ)''',
          t_sales_reason,
          ["sales_reason_id", "name", "reason_type", "modified_date"]),
    Table("sales_order_header_sales_reason",
          '''CREATE TABLE IF NOT EXISTS "sales_order_header_sales_reason" (
              sales_order_id INTEGER,
              sales_reason_id INTEGER,
              modified_date TIMESTAMPTZ,
              PRIMARY KEY (sales_order_id, sales_reason_id))''',
          t_sales_order_header_sales_reason,
          ["sales_order_id", "sales_reason_id", "modified_date"]),
]


# ----------------------------------------------------------- driver

def already_populated(cur, name: str) -> bool:
    cur.execute(f'SELECT EXISTS(SELECT 1 FROM "{name}" LIMIT 1)')
    return cur.fetchone()[0]


def run(reset: bool = False, schema: str = "adv") -> None:
    t0 = time.time()
    tables = list(THEMATIC)
    print(f"Planned tables: {len(tables)}  (schema: {schema})")

    if reset:
        print(f"--reset: dropping all planned tables in {schema!r}")
        with conn_cur(schema) as (conn, cur):
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{t.name}" CASCADE')
            conn.commit()

    print(f"Phase 1/2: creating tables in schema {schema!r}...")
    with conn_cur(schema) as (conn, cur):
        for i, t in enumerate(tables, 1):
            cur.execute(t.ddl)
            if i % 10 == 0:
                conn.commit()
                print(f"  ddl progress: {i}/{len(tables)}", flush=True)
        conn.commit()
    print(f"  DDL done in {time.time()-t0:.1f}s")

    print(f"Phase 2/2: populating tables in schema {schema!r}...")
    total_rows = 0
    for idx, t in enumerate(tables, 1):
        if t.populate is None:
            continue
        with conn_cur(schema) as (conn, cur):
            if already_populated(cur, t.name):
                continue
            rng = random.Random(SEED ^ hash(t.name))
            try:
                rows = t.populate(rng)
            except Exception as e:
                print(f"  generator error in {t.name}: {e!r}")
                continue
            if not rows:
                continue
            n = copy_rows(cur, t.name, t.column_names, rows)
            conn.commit()
            total_rows += n
            print(f"  [{idx:>2}/{len(tables)}] {t.name:<38s} +{n:>8} rows  total={total_rows:,}",
                  flush=True)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    with conn_cur(schema) as (conn, cur):
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s",
                    (schema,))
        actual = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema=%s AND constraint_type='PRIMARY KEY'",
                    (schema,))
        n_pks = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema=%s AND constraint_type='FOREIGN KEY'",
                    (schema,))
        n_fks = cur.fetchone()[0]
    print(f"Tables in {schema}: {actual}")
    print(f"Total rows inserted: {total_rows:,}")
    print(f"PRIMARY KEY constraints: {n_pks}")
    print(f"FOREIGN KEY constraints: {n_fks}  (must be 0)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--schema", default="adv")
    args = ap.parse_args()
    run(reset=args.reset, schema=args.schema)
