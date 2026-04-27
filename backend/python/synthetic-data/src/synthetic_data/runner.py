"""Orchestrator: generates all tables in DAG order, writes Parquet files."""

from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import numpy as np
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from synthetic_data.config import ALL_TABLES
from synthetic_data.generators.catalog import (
    CategoriesGenerator,
    ProductsGenerator,
    InventoryGenerator,
    WarehousesGenerator,
    WarehouseStockGenerator,
)
from synthetic_data.generators.customers import CustomersGenerator, AddressesGenerator
from synthetic_data.generators.edge import EmptyTableGenerator, WideDenormalizedGenerator
from synthetic_data.generators.hr import DepartmentsGenerator, EmployeeRecordsGenerator
from synthetic_data.generators.identity import (
    ApiTokensGenerator,
    RolesGenerator,
    UserRolesGenerator,
    UserSessionsGenerator,
    UsersGenerator,
)
from synthetic_data.generators.noise import (
    AccessLogGenerator,
    AuditLogGenerator,
    CustomersArchiveGenerator,
    EtlImportQueueGenerator,
    MigrationsGenerator,
    OrdersBakGenerator,
    TempImportBatchGenerator,
    TmpStagingOrdersGenerator,
    UserEventsGenerator,
)
from synthetic_data.generators.orders import (
    OrderItemsGenerator,
    OrdersGenerator,
    PaymentsGenerator,
)
from synthetic_data.generators.support import (
    ReviewsGenerator,
    TicketMessagesGenerator,
    TicketsGenerator,
)
from synthetic_data.manifest import write_ground_truth
from synthetic_data.metadata import write_metadata

console = Console()


def _write_table(args: tuple) -> tuple[str, int]:
    """Worker function for multiprocessing pool."""
    gen, schemas_dir, compression, compression_level = args
    path = schemas_dir / f"{gen.name}.parquet"
    rows = gen.write_parquet(path, compression=compression, compression_level=compression_level)
    return gen.name, rows


def run_generation(
    output_dir: Path,
    seed: int = 42,
    compression: str = "zstd",
    compression_level: int = 3,
    small: bool = False,
) -> dict[str, int]:
    """
    Run full generation pipeline.
    Returns {table_name: row_count} mapping.
    """
    scale = 0.1 if small else 1.0
    output_dir = Path(output_dir)
    schemas_dir = output_dir / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)

    actual_row_counts: dict[str, int] = {}

    def write_gen(gen, label: str) -> int:
        path = schemas_dir / f"{gen.name}.parquet"
        rows = gen.write_parquet(path, compression=compression, compression_level=compression_level)
        actual_row_counts[gen.name] = rows
        console.print(f"  [green]✓[/green] {gen.name:30s} {rows:>10,} rows")
        return rows

    t0 = time.time()
    console.rule("[bold blue]Synthetic Data Generator")

    # -------------------------------------------------------------------------
    # L0: No FK dependencies
    # -------------------------------------------------------------------------
    console.print("\n[bold]Layer 0: Root tables[/bold]")

    customers_gen = CustomersGenerator(seed=seed, scale=scale)
    write_gen(customers_gen, "customers")
    customer_ids = customers_gen.ids

    categories_gen = CategoriesGenerator(seed=seed, scale=scale)
    write_gen(categories_gen, "categories")
    category_ids = categories_gen.ids

    roles_gen = RolesGenerator(seed=seed, scale=scale)
    write_gen(roles_gen, "roles")
    role_ids = roles_gen.ids

    warehouses_gen = WarehousesGenerator(seed=seed, scale=scale)
    write_gen(warehouses_gen, "warehouses")
    warehouse_ids = warehouses_gen.ids

    employee_gen = EmployeeRecordsGenerator(seed=seed, scale=scale)
    write_gen(employee_gen, "employee_records")
    employee_ids = employee_gen.ids

    # -------------------------------------------------------------------------
    # L1: Depend on L0
    # -------------------------------------------------------------------------
    console.print("\n[bold]Layer 1: First-level children[/bold]")

    addresses_gen = AddressesGenerator(
        customer_ids=customer_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(addresses_gen, "addresses")
    address_ids = addresses_gen.ids

    products_gen = ProductsGenerator(
        category_ids=category_ids,
        seed=seed, scale=scale
    )
    write_gen(products_gen, "products")
    product_ids = products_gen.ids

    # Use the *actual* prices stamped into products.parquet (set as a
    # side-effect of ProductsGenerator.batches()). Re-deriving them with a
    # fresh RNG would diverge from what was written, because cat_fk is
    # drawn from the same RNG before prices.
    if products_gen.prices is None:
        raise RuntimeError(
            "ProductsGenerator did not expose its `prices` array; cannot "
            "compute consistent order_items.unit_price / payments.amount."
        )
    product_prices = products_gen.prices

    users_gen = UsersGenerator(customer_ids=customer_ids.astype(np.int64), seed=seed, scale=scale)
    write_gen(users_gen, "users")
    user_ids = users_gen.ids

    inventory_gen = InventoryGenerator(product_ids=product_ids.astype(np.int64), seed=seed, scale=scale)
    write_gen(inventory_gen, "inventory")

    warehouse_stock_gen = WarehouseStockGenerator(
        product_ids=product_ids.astype(np.int64),
        warehouse_ids=warehouse_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(warehouse_stock_gen, "warehouse_stock")

    departments_gen = DepartmentsGenerator(
        employee_ids=employee_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(departments_gen, "departments")

    # -------------------------------------------------------------------------
    # L2: Depend on L0/L1
    # -------------------------------------------------------------------------
    console.print("\n[bold]Layer 2: Second-level children[/bold]")

    orders_gen = OrdersGenerator(
        customer_ids=customer_ids.astype(np.int64),
        address_ids=address_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(orders_gen, "orders")
    order_ids = orders_gen.ids

    user_sessions_gen = UserSessionsGenerator(user_ids=user_ids.astype(np.int64), seed=seed, scale=scale)
    write_gen(user_sessions_gen, "user_sessions")

    api_tokens_gen = ApiTokensGenerator(user_ids=user_ids.astype(np.int64), seed=seed, scale=scale)
    write_gen(api_tokens_gen, "api_tokens")

    user_roles_gen = UserRolesGenerator(
        user_ids=user_ids.astype(np.int64),
        role_ids=role_ids.astype(np.int32),
        seed=seed, scale=scale
    )
    write_gen(user_roles_gen, "user_roles")

    tickets_gen = TicketsGenerator(
        customer_ids=customer_ids.astype(np.int64),
        employee_ids=employee_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(tickets_gen, "tickets")
    ticket_ids = tickets_gen.ids

    # -------------------------------------------------------------------------
    # L3: Depend on L2
    # -------------------------------------------------------------------------
    console.print("\n[bold]Layer 3: Third-level children[/bold]")

    order_items_gen = OrderItemsGenerator(
        order_ids=order_ids.astype(np.int64),
        product_ids=product_ids.astype(np.int64),
        product_prices=product_prices,
        seed=seed, scale=scale
    )
    write_gen(order_items_gen, "order_items")
    # Subtotals are accumulated as a side-effect of the streaming write;
    # read them directly rather than regenerating the 2M-row table.
    subtotals = order_items_gen.order_subtotals

    ticket_messages_gen = TicketMessagesGenerator(
        ticket_ids=ticket_ids.astype(np.int64),
        user_ids=user_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(ticket_messages_gen, "ticket_messages")

    reviews_gen = ReviewsGenerator(
        product_ids=product_ids.astype(np.int64),
        customer_ids=customer_ids.astype(np.int64),
        seed=seed, scale=scale
    )
    write_gen(reviews_gen, "reviews")

    # -------------------------------------------------------------------------
    # L4: Payments (depends on order_items subtotals)
    # -------------------------------------------------------------------------
    console.print("\n[bold]Layer 4: Payments[/bold]")

    payments_gen = PaymentsGenerator(
        order_ids=order_ids.astype(np.int64),
        customer_ids=customer_ids.astype(np.int64),
        order_subtotals=subtotals,
        seed=seed, scale=scale
    )
    write_gen(payments_gen, "payments")

    # -------------------------------------------------------------------------
    # Noise & Edge (independent, parallel)
    # -------------------------------------------------------------------------
    console.print("\n[bold]Noise & Edge tables[/bold]")

    noise_gens = [
        AuditLogGenerator(seed=seed, scale=scale),
        AccessLogGenerator(seed=seed, scale=scale),
        TempImportBatchGenerator(seed=seed, scale=scale),
        TmpStagingOrdersGenerator(seed=seed, scale=scale),
        OrdersBakGenerator(seed=seed, scale=scale),
        CustomersArchiveGenerator(seed=seed, scale=scale),
        UserEventsGenerator(seed=seed, scale=scale),
        EtlImportQueueGenerator(seed=seed, scale=scale),
        MigrationsGenerator(seed=seed, scale=scale),
        WideDenormalizedGenerator(seed=seed, scale=scale),
        EmptyTableGenerator(seed=seed, scale=scale),
    ]

    args_list = [
        (gen, schemas_dir, compression, compression_level)
        for gen in noise_gens
    ]

    # Use multiprocessing for independent tables
    with mp.Pool(processes=min(4, mp.cpu_count())) as pool:
        results = pool.map(_write_table, args_list)

    for name, rows in results:
        actual_row_counts[name] = rows
        console.print(f"  [green]✓[/green] {name:30s} {rows:>10,} rows")

    # -------------------------------------------------------------------------
    # Write manifests
    # -------------------------------------------------------------------------
    console.print("\n[bold]Writing manifests...[/bold]")

    gt_path = write_ground_truth(output_dir, seed=seed, actual_row_counts=actual_row_counts)
    console.print(f"  [green]✓[/green] {gt_path}")

    meta_path = write_metadata(output_dir, seed=seed,
                               actual_row_counts=actual_row_counts,
                               schemas_dir=schemas_dir)
    console.print(f"  [green]✓[/green] {meta_path}")

    elapsed = time.time() - t0
    total_rows = sum(actual_row_counts.values())
    console.rule(
        f"[bold green]Done in {elapsed:.1f}s — "
        f"{total_rows:,} rows across {len(actual_row_counts)} tables"
    )

    return actual_row_counts
