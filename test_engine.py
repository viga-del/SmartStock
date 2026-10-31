"""
Comprehensive Unit & Concurrency Test Suite for Order Promising & ATP Engine.
Run with: pytest tests/test_engine.py
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import pytest

from engine import OrderPromisingEngine
from models import CustomerOrder, ProductionBatch


@pytest.fixture
def fresh_engine():
    """Provides a fresh isolated instance of the engine for every test."""
    return OrderPromisingEngine()


def test_network_topology_initialization(fresh_engine):
    """Verifies that Plant Warehouse and 3 Regional Warehouses are properly initialized."""
    whs = fresh_engine.warehouses
    assert len(whs) == 4
    assert "WH-PLANT" in whs
    assert "WH-REG-NORTH" in whs
    assert "WH-REG-SOUTH" in whs
    assert "WH-REG-WEST" in whs
    assert whs["WH-PLANT"].type.value == "PLANT"
    assert whs["WH-REG-NORTH"].type.value == "REGIONAL"


def test_single_warehouse_fastest_cheapest(fresh_engine):
    """
    When a regional warehouse has plenty of released stock,
    order should be fulfilled from the regional warehouse (1-day transit) rather than Plant (2-day transit).
    """
    order = CustomerOrder(
        order_id="TEST-ORD-01",
        customer_name="North General Clinic",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=5,
        order_date=date.today(),
    )
    promise = fresh_engine.promise_and_reserve(order)
    assert promise.status == "PROMISED"
    assert promise.is_split is False
    assert len(promise.legs) == 1
    # Regional North warehouse has 15 available, so it fulfills it in 1 transit day
    assert promise.legs[0].warehouse_id == "WH-REG-NORTH"
    assert promise.legs[0].transit_days == 1


def test_concurrency_race_condition_competing_for_last_unit(fresh_engine):
    """
    CRITICAL REQUIREMENT (Case 1):
    Two orders compete for the exact same last available unit simultaneously.
    Thread-safe locking must prevent double-booking.
    Exactly 1 order must succeed, 1 must fail/backorder, and net available stock must remain 0.
    """
    # Force state: Only 1 unit in the entire network
    fresh_engine.inventory.clear()
    fresh_engine.production_plan.clear()
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=10, reserved=9)  # 1 available
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=0, reserved=0)
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=0, reserved=0)

    order_1 = CustomerOrder(
        order_id="COMPETE-ORD-1",
        customer_name="Hospital Alpha",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=1,
        order_date=date.today(),
    )
    order_2 = CustomerOrder(
        order_id="COMPETE-ORD-2",
        customer_name="Hospital Beta",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=1,
        order_date=date.today(),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(fresh_engine.promise_and_reserve, order_1)
        f2 = executor.submit(fresh_engine.promise_and_reserve, order_2)
        res1 = f1.result()
        res2 = f2.result()

    statuses = [res1.status, res2.status]
    assert statuses.count("PROMISED") == 1, "Exactly one order must be promised!"
    assert statuses.count("REJECTED") == 1, "The competing order must be rejected when stock runs out!"

    # Verify inventory was not oversold
    inv = fresh_engine.inventory[("MED-VENTILATOR-100", "WH-REG-NORTH")]
    assert inv.available_stock == 0
    assert inv.reserved_stock == 10  # 9 prior + 1 new = 10 reserved


def test_future_production_promise(fresh_engine):
    """
    CRITICAL REQUIREMENT (Case 2):
    Stock exists ONLY in future production schedule.
    Engine must calculate delivery promise date based on batch completion + dispatch lead + transit days.
    """
    fresh_engine.inventory.clear()
    fresh_engine.production_plan.clear()

    # Zero stock everywhere
    for wh_id in fresh_engine.warehouses:
        fresh_engine.set_inventory("MED-VENTILATOR-100", wh_id, released=0, reserved=0)

    today = date.today()
    batch_completion = today + timedelta(days=5)

    fresh_engine.production_plan = [
        ProductionBatch(
            batch_id="FUTURE-BATCH-5D",
            sku="MED-VENTILATOR-100",
            warehouse_id="WH-PLANT",
            quantity=40,
            reserved_quantity=0,
            completion_date=batch_completion,
        )
    ]

    order = CustomerOrder(
        order_id="FUTURE-ORD-01",
        customer_name="West Coast Trauma Center",
        destination_region="North",  # Transit from Plant to North is 2 days
        sku="MED-VENTILATOR-100",
        quantity=15,
        order_date=today,
    )

    promise = fresh_engine.promise_and_reserve(order)

    assert promise.status == "PROMISED"
    assert promise.legs[0].source_type == "FUTURE_PRODUCTION"
    assert promise.legs[0].batch_id == "FUTURE-BATCH-5D"
    assert promise.legs[0].quantity == 15

    # Expected date: Completion (D+5) + Dispatch lead (1 day) + Transit (2 days) = D+8
    expected_delivery = batch_completion + timedelta(days=1) + timedelta(days=2)
    assert promise.promised_delivery_date == expected_delivery

    # Verify batch reservation
    batch = fresh_engine.production_plan[0]
    assert batch.reserved_quantity == 15
    assert batch.available_quantity == 25


def test_split_shipment_control(fresh_engine):
    """
    Tests that split shipment correctly divides order when no single warehouse has enough on-hand stock,
    and applies the coordination penalty.
    """
    fresh_engine.inventory.clear()
    fresh_engine.production_plan.clear()

    # North has 8 units, South has 7 units (total 15 available)
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=8, reserved=0)
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=7, reserved=0)
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)
    fresh_engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=0, reserved=0)

    order = CustomerOrder(
        order_id="SPLIT-ORD-01",
        customer_name="Multi-Site Hospital",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=12,
        order_date=date.today(),
        allow_split=True,
    )

    promise = fresh_engine.promise_and_reserve(order)

    assert promise.status == "PROMISED"
    assert promise.is_split is True
    assert len(promise.legs) == 2
    assert promise.split_penalty > 0
    total_qty = sum(l.quantity for l in promise.legs)
    assert total_qty == 12


def test_insufficient_stock_rejection(fresh_engine):
    """When order exceeds all on-hand and future production, engine rejects cleanly."""
    fresh_engine.inventory.clear()
    fresh_engine.production_plan.clear()

    order = CustomerOrder(
        order_id="UNFULFILLABLE-ORD",
        customer_name="Emergency Response",
        destination_region="South",
        sku="MED-VENTILATOR-100",
        quantity=500,
        order_date=date.today(),
    )

    promise = fresh_engine.promise_and_reserve(order)
    assert promise.status == "REJECTED"
    assert promise.promised_delivery_date is None
    assert "Insufficient on-hand stock" in promise.explanation
