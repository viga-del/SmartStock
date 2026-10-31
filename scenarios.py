"""
Interactive Demonstration Scenarios for the Supply Chain Order Promising Engine.

Demonstrates:
- Case 1: Two concurrent orders competing for the exact last available unit (Race Condition & Atomic Locking).
- Case 2: Stock exists ONLY in future scheduled production batches (Timeline ATP & Delivery Promising).
- Case 3: Split vs Single Warehouse optimization (Preventing chaotic split shipments with split penalty).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from engine import OrderPromisingEngine
from models import CustomerOrder, ProductionBatch

console = Console(highlight=False)


def print_inventory_table(engine: OrderPromisingEngine, title: str):
    """Utility to print a visually rich table of warehouse stocks."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Warehouse", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Released (On-Hand)", justify="right", style="green")
    table.add_column("Reserved", justify="right", style="yellow")
    table.add_column("Net Available", justify="right", style="bold white")

    for item in engine.get_inventory_summary():
        table.add_row(
            item["warehouse_name"],
            item["warehouse_type"],
            str(item["released_stock"]),
            str(item["reserved_stock"]),
            str(item["available_stock"]),
        )
    console.print(table)


def run_case_1_last_unit_competition():
    """
    CASE 1: Two orders compete simultaneously for the last unit.
    
    Setup:
    - Only 1 unit of 'MED-VENTILATOR-100' is available across the entire network (at WH-REG-NORTH).
    - Hospital A (St. Jude) and Hospital B (General Memorial) place orders for 1 unit at the exact same millisecond.
    - Demonstrates that the engine atomically grants the unit to the first order to lock the resource,
      and either routes the second order to incoming production or rejects it without double-booking.
    """
    console.print(Panel.fit(
        "[bold red]CASE 1: Two Orders Competing for the Last Unit (Atomic Concurrency Test)[/bold red]\n"
        "Tests prevention of the 'both orders reserve the same last unit' bug using thread-safe locking.",
        border_style="red"
    ))

    engine = OrderPromisingEngine()

    # Configure strictly 1 unit available in the whole network
    engine.inventory.clear()
    engine.production_plan.clear()
    
    # 10 released, 9 already reserved => Exactly 1 unit available!
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=10, reserved=9)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=0, reserved=0)

    print_inventory_table(engine, "Initial Inventory State (Exactly 1 Unit Available at North Regional)")

    # Define two simultaneous orders
    order_a = CustomerOrder(
        order_id="ORD-HOSPITAL-ALPHA",
        customer_name="St. Jude Children's Hospital",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=1,
        order_date=date.today(),
    )

    order_b = CustomerOrder(
        order_id="ORD-HOSPITAL-BETA",
        customer_name="General Memorial Hospital",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=1,
        order_date=date.today(),
    )

    console.print("\n[bold yellow]>>> Firing 2 concurrent requests simultaneously using ThreadPoolExecutor...[/bold yellow]\n")

    results = []

    def place_order_task(order: CustomerOrder):
        # Micro-jitter to simulate real-world arrival within nanoseconds
        promise = engine.promise_and_reserve(order)
        return (order.customer_name, promise)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(place_order_task, order_a),
            executor.submit(place_order_task, order_b),
        ]
        for f in futures:
            results.append(f.result())

    # Display results
    results_table = Table(title="Concurrent Execution Outcome", show_header=True, header_style="bold blue")
    results_table.add_column("Hospital / Order", style="bold")
    results_table.add_column("Status", style="bold")
    results_table.add_column("Promised Date", style="cyan")
    results_table.add_column("Fulfilled By", style="green")
    results_table.add_column("Explanation", style="italic")

    for customer_name, promise in results:
        status_color = "green" if promise.status == "PROMISED" else "red"
        wh_name = promise.legs[0].warehouse_name if promise.legs else "None (Stock Depleted)"
        p_date = promise.promised_delivery_date.isoformat() if promise.promised_delivery_date else "N/A"
        results_table.add_row(
            customer_name,
            f"[{status_color}]{promise.status}[/{status_color}]",
            p_date,
            wh_name,
            promise.explanation,
        )

    console.print(results_table)

    print_inventory_table(engine, "Final Inventory State After Contention (Net Available MUST be 0, NEVER -1)")

    # Assert correctness
    inv_north = engine.inventory[("MED-VENTILATOR-100", "WH-REG-NORTH")]
    assert inv_north.available_stock == 0, "Available stock should be 0!"
    assert inv_north.reserved_stock == 10, "Reserved stock should be exactly 10!"
    console.print("[bold green][PASS] Zero double-booking occurred. One hospital secured the unit, the other was safely handled without overpromising![/bold green]\n")


def run_case_2_future_production():
    """
    CASE 2: Stock exists only in future production schedule.
    
    Setup:
    - 0 on-hand units available anywhere in the network.
    - Customer places an urgent order for 25 units of 'MED-VENTILATOR-100'.
    - Production batch of 50 units finishes on Day D+4 at Plant Warehouse.
    - Plant to Destination transit time is 2 days (+ 1 day dispatch lead time).
    - Demonstrates calculating: Promised Delivery Date = Completion Date + Dispatch Lead + Transit Days.
    """
    console.print(Panel.fit(
        "[bold cyan]CASE 2: Stock Exists ONLY in Future Production (ATP Schedule Forecasting)[/bold cyan]\n"
        "Tests promising delivery dates based on incoming factory production batches when on-hand inventory is 0.",
        border_style="cyan"
    ))

    engine = OrderPromisingEngine()

    # Zero out all on-hand stock across all 4 warehouses
    engine.inventory.clear()
    engine.production_plan.clear()

    engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)

    # Short production plan with 2 upcoming batches
    today = date.today()
    batch_1 = today + timedelta(days=4)
    batch_2 = today + timedelta(days=10)

    engine.production_plan = [
        ProductionBatch(
            batch_id="PROD-RUN-MAY-101",
            sku="MED-VENTILATOR-100",
            warehouse_id="WH-PLANT",
            quantity=50,
            reserved_quantity=0,
            completion_date=batch_1,
        ),
        ProductionBatch(
            batch_id="PROD-RUN-MAY-102",
            sku="MED-VENTILATOR-100",
            warehouse_id="WH-PLANT",
            quantity=100,
            reserved_quantity=0,
            completion_date=batch_2,
        ),
    ]

    print_inventory_table(engine, "On-Hand Inventory (Completely Zero Across All Warehouses)")

    # Print production plan table
    plan_table = Table(title="Incoming Factory Production Schedule at Plant Hub", show_header=True, header_style="bold green")
    plan_table.add_column("Batch ID", style="cyan")
    plan_table.add_column("Completion Date", style="bold yellow")
    plan_table.add_column("Total Quantity", justify="right")
    plan_table.add_column("Available to Promise", justify="right", style="bold green")
    for b in engine.production_plan:
        plan_table.add_row(b.batch_id, b.completion_date.isoformat(), str(b.quantity), str(b.available_quantity))
    console.print(plan_table)

    # Customer orders 25 units
    order = CustomerOrder(
        order_id="ORD-DIST-WEST",
        customer_name="Pacific Health Regional Distributor",
        destination_region="North",  # Transit from Plant to North is 2 days
        sku="MED-VENTILATOR-100",
        quantity=25,
        order_date=today,
    )

    console.print(f"\n[bold yellow][ORDER] Customer Placing Order for {order.quantity} units to {order.destination_region}...[/bold yellow]\n")

    promise = engine.promise_and_reserve(order)

    # Show promise details
    res_table = Table(title="Order Promising Engine Response", show_header=True, header_style="bold magenta")
    res_table.add_column("Metric", style="bold")
    res_table.add_column("Value", style="cyan")

    res_table.add_row("Order Status", f"[bold green]{promise.status}[/bold green]")
    res_table.add_row("Promised Delivery Date", f"[bold yellow]{promise.promised_delivery_date.isoformat()}[/bold yellow]")
    res_table.add_row("Sourced From", f"{promise.legs[0].warehouse_name} ({promise.legs[0].source_type})")
    res_table.add_row("Production Batch Allocated", f"{promise.legs[0].batch_id}")
    res_table.add_row("Dispatch Date", f"{promise.legs[0].dispatch_date.isoformat()} (Factory Finishes: {batch_1.isoformat()} + 1 Day Prep)")
    res_table.add_row("Transit Lead Time", f"{promise.legs[0].transit_days} days")
    res_table.add_row("Total Shipping & Freight Cost", f"${promise.total_shipping_cost:.2f}")
    res_table.add_row("System Explanation", f"{promise.explanation}")

    console.print(res_table)

    # Check updated production plan
    console.print("\n[bold]Updated Production Plan Allocations:[/bold]")
    plan_table_after = Table(show_header=True, header_style="bold green")
    plan_table_after.add_column("Batch ID")
    plan_table_after.add_column("Total Qty")
    plan_table_after.add_column("Reserved Qty", style="yellow")
    plan_table_after.add_column("Remaining Unallocated ATP", style="bold green")
    for b in engine.production_plan:
        plan_table_after.add_row(b.batch_id, str(b.quantity), str(b.reserved_quantity), str(b.available_quantity))
    console.print(plan_table_after)

    assert promise.status == "PROMISED"
    # Plant dispatch: Batch completion (D+4) + 1 day dispatch lead + 2 days transit = D+7
    expected_date = batch_1 + timedelta(days=1) + timedelta(days=2)
    assert promise.promised_delivery_date == expected_date
    console.print(f"[bold green][PASS] Successfully forecasted and locked future production! Delivery promised on {promise.promised_delivery_date}.[/bold green]\n")


def run_case_3_split_shipment_control():
    """
    CASE 3: Mitigating Split Shipment Chaos.
    Demonstrates evaluating whether to split an order between regional warehouses
    or fulfill from the Central Plant Warehouse in a single parcel.
    """
    console.print(Panel.fit(
        "[bold green]CASE 3: Split vs Single Shipment Optimization (Taming Split Chaos)[/bold green]\n"
        "Compares cost, delivery dates, and handling penalties to choose the optimal shipping strategy.",
        border_style="green"
    ))

    engine = OrderPromisingEngine(split_shipment_penalty=40.0)

    # Setup:
    # Customer orders 20 units to 'North'.
    # WH-REG-NORTH has 12 units available.
    # WH-REG-SOUTH has 10 units available.
    # WH-PLANT has 50 units available.
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=12, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=10, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=50, reserved=0)

    print_inventory_table(engine, "Inventory Topology for Split Analysis")

    order = CustomerOrder(
        order_id="ORD-CONSOLIDATION-TEST",
        customer_name="Cleveland Clinic Health System",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=20,
        order_date=date.today(),
        allow_split=True,
    )

    promise = engine.evaluate_order(order)

    console.print(f"\n[bold]Optimal Sourcing Decision for Order of {order.quantity} units to {order.destination_region}:[/bold]")
    console.print(f"Status: [bold green]{promise.status}[/bold green]")
    console.print(f"Is Split Shipment: [bold cyan]{promise.is_split}[/bold cyan]")
    console.print(f"Total Freight & Handling Cost: [bold yellow]${promise.total_shipping_cost}[/bold yellow]")
    console.print(f"Promised Delivery Date: [bold magenta]{promise.promised_delivery_date.isoformat()}[/bold magenta]")
    console.print(f"Explanation: {promise.explanation}\n")

    leg_table = Table(title="Planned Fulfillment Legs", show_header=True, header_style="bold cyan")
    leg_table.add_column("Origin Warehouse")
    leg_table.add_column("Quantity", justify="right")
    leg_table.add_column("Transit Days", justify="right")
    leg_table.add_column("Arrival Date")
    leg_table.add_column("Cost", justify="right")

    for leg in promise.legs:
        leg_table.add_row(
            leg.warehouse_name,
            str(leg.quantity),
            f"{leg.transit_days} days",
            leg.arrival_date.isoformat(),
            f"${leg.total_cost:.2f}",
        )
    console.print(leg_table)


if __name__ == "__main__":
    console.print("\n[bold underline yellow]=== RUNNING SUPPLY CHAIN ORDER PROMISING DEMO SCENARIOS ===[/bold underline yellow]\n")
    run_case_1_last_unit_competition()
    run_case_2_future_production()
    run_case_3_split_shipment_control()
    console.print("[bold green]=== ALL DEMONSTRATION CASES COMPLETED SUCCESSFULLY! ===[/bold green]\n")
