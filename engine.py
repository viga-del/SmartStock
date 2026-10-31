"""
Available-to-Promise (ATP) & Order Fulfillment Optimization Engine.

Core Features:
1. Thread-safe atomic inventory reservation (prevents the "two orders reserve the last unit" race condition).
2. Multi-warehouse routing optimization (Plant Warehouse + 3 Regional Warehouses).
3. Delivery date calculation ("We can deliver by date X").
4. Chaos-free split shipment handling with multi-shipment coordination penalties.
5. Future production schedule forecasting (Capable-To-Promise / ATP from incoming factory runs).
"""

from datetime import date, datetime, timedelta
import threading
from typing import Dict, List, Optional, Tuple
import uuid

from models import (
    CustomerOrder,
    FulfillmentLeg,
    InventoryRecord,
    OrderPromise,
    ProductionBatch,
    Reservation,
    ShippingRoute,
    Warehouse,
    WarehouseType,
)


class InsufficientInventoryError(Exception):
    """Raised when inventory cannot satisfy the order requirements."""
    pass


class OrderPromisingEngine:
    def __init__(self, split_shipment_penalty: float = 35.0):
        # Master synchronization lock for atomic allocations across threads
        self._lock = threading.RLock()
        
        # Operational penalty ($) applied per additional warehouse used in an order
        # to deter chaotic split shipments unless strictly necessary.
        self.split_shipment_penalty = split_shipment_penalty

        # In-memory repositories
        self.warehouses: Dict[str, Warehouse] = {}
        self.inventory: Dict[Tuple[str, str], InventoryRecord] = {}  # (sku, warehouse_id) -> Record
        self.production_plan: List[ProductionBatch] = []
        self.shipping_matrix: Dict[Tuple[str, str], ShippingRoute] = {}  # (origin_id, dest_region) -> Route
        self.reservations: List[Reservation] = []
        self.orders_history: List[OrderPromise] = []

        # Load default baseline supply chain topology
        self._seed_default_data()

    def _seed_default_data(self):
        """Initializes the Plant Warehouse and 3 Regional Warehouses with realistic baseline data."""
        with self._lock:
            # 1. Warehouses
            pw = Warehouse(
                id="WH-PLANT",
                name="Central Plant Manufacturing Hub",
                type=WarehouseType.PLANT,
                location="Midwest Central",
                dispatch_lead_days=1,
            )
            rw_north = Warehouse(
                id="WH-REG-NORTH",
                name="North Regional Warehouse",
                type=WarehouseType.REGIONAL,
                location="Chicago/North Metro",
                dispatch_lead_days=1,
            )
            rw_south = Warehouse(
                id="WH-REG-SOUTH",
                name="South Regional Warehouse",
                type=WarehouseType.REGIONAL,
                location="Atlanta/South Hub",
                dispatch_lead_days=1,
            )
            rw_west = Warehouse(
                id="WH-REG-WEST",
                name="West Regional Warehouse",
                type=WarehouseType.REGIONAL,
                location="Denver/West Hub",
                dispatch_lead_days=1,
            )

            for w in [pw, rw_north, rw_south, rw_west]:
                self.warehouses[w.id] = w

            # 2. Shipping Routes & Lead Time Matrix to Customer Regions (North, South, West, East)
            # Route: (Origin, Destination Region) -> transit days, cost/unit, fixed shipment fee
            routes = [
                # From Plant (WH-PLANT)
                ShippingRoute(origin_warehouse_id="WH-PLANT", destination_region="North", transit_days=2, cost_per_unit=4.0, fixed_shipment_fee=30.0),
                ShippingRoute(origin_warehouse_id="WH-PLANT", destination_region="South", transit_days=3, cost_per_unit=5.0, fixed_shipment_fee=30.0),
                ShippingRoute(origin_warehouse_id="WH-PLANT", destination_region="West", transit_days=3, cost_per_unit=5.5, fixed_shipment_fee=30.0),
                ShippingRoute(origin_warehouse_id="WH-PLANT", destination_region="East", transit_days=2, cost_per_unit=4.5, fixed_shipment_fee=30.0),

                # From North Regional (WH-REG-NORTH)
                ShippingRoute(origin_warehouse_id="WH-REG-NORTH", destination_region="North", transit_days=1, cost_per_unit=1.5, fixed_shipment_fee=15.0),
                ShippingRoute(origin_warehouse_id="WH-REG-NORTH", destination_region="East", transit_days=2, cost_per_unit=3.0, fixed_shipment_fee=20.0),
                ShippingRoute(origin_warehouse_id="WH-REG-NORTH", destination_region="South", transit_days=4, cost_per_unit=6.5, fixed_shipment_fee=35.0),
                ShippingRoute(origin_warehouse_id="WH-REG-NORTH", destination_region="West", transit_days=4, cost_per_unit=7.0, fixed_shipment_fee=35.0),

                # From South Regional (WH-REG-SOUTH)
                ShippingRoute(origin_warehouse_id="WH-REG-SOUTH", destination_region="South", transit_days=1, cost_per_unit=1.5, fixed_shipment_fee=15.0),
                ShippingRoute(origin_warehouse_id="WH-REG-SOUTH", destination_region="East", transit_days=2, cost_per_unit=3.2, fixed_shipment_fee=20.0),
                ShippingRoute(origin_warehouse_id="WH-REG-SOUTH", destination_region="North", transit_days=4, cost_per_unit=6.5, fixed_shipment_fee=35.0),
                ShippingRoute(origin_warehouse_id="WH-REG-SOUTH", destination_region="West", transit_days=4, cost_per_unit=6.8, fixed_shipment_fee=35.0),

                # From West Regional (WH-REG-WEST)
                ShippingRoute(origin_warehouse_id="WH-REG-WEST", destination_region="West", transit_days=1, cost_per_unit=1.5, fixed_shipment_fee=15.0),
                ShippingRoute(origin_warehouse_id="WH-REG-WEST", destination_region="North", transit_days=3, cost_per_unit=5.0, fixed_shipment_fee=30.0),
                ShippingRoute(origin_warehouse_id="WH-REG-WEST", destination_region="South", transit_days=3, cost_per_unit=5.5, fixed_shipment_fee=30.0),
                ShippingRoute(origin_warehouse_id="WH-REG-WEST", destination_region="East", transit_days=4, cost_per_unit=7.5, fixed_shipment_fee=40.0),
            ]

            for r in routes:
                self.shipping_matrix[(r.origin_warehouse_id, r.destination_region)] = r

            # 3. Default Inventory: SKU "MED-VENTILATOR-100"
            today = date.today()
            self.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=50, reserved=10)
            self.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=20, reserved=5)
            self.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=15, reserved=15)  # 0 available
            self.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=10, reserved=2)

            # 4. Short-term Incoming Production Plan at Plant Hub
            self.production_plan = [
                ProductionBatch(
                    batch_id="BATCH-PROD-001",
                    sku="MED-VENTILATOR-100",
                    warehouse_id="WH-PLANT",
                    quantity=30,
                    reserved_quantity=0,
                    completion_date=today + timedelta(days=3),
                ),
                ProductionBatch(
                    batch_id="BATCH-PROD-002",
                    sku="MED-VENTILATOR-100",
                    warehouse_id="WH-PLANT",
                    quantity=75,
                    reserved_quantity=0,
                    completion_date=today + timedelta(days=7),
                ),
            ]

    def set_inventory(self, sku: str, warehouse_id: str, released: int, reserved: int = 0):
        """Sets the released and reserved inventory for a specific SKU and warehouse."""
        with self._lock:
            self.inventory[(sku, warehouse_id)] = InventoryRecord(
                sku=sku,
                warehouse_id=warehouse_id,
                released_stock=released,
                reserved_stock=reserved,
            )

    def get_inventory_summary(self, sku: Optional[str] = None) -> List[Dict]:
        """Returns current inventory snapshot across all warehouses."""
        with self._lock:
            records = []
            for (item_sku, wh_id), inv in self.inventory.items():
                if sku and item_sku != sku:
                    continue
                wh = self.warehouses.get(wh_id)
                records.append({
                    "sku": item_sku,
                    "warehouse_id": wh_id,
                    "warehouse_name": wh.name if wh else wh_id,
                    "warehouse_type": wh.type.value if wh else "UNKNOWN",
                    "released_stock": inv.released_stock,
                    "reserved_stock": inv.reserved_stock,
                    "available_stock": inv.available_stock,
                })
            return records

    def get_production_plan_summary(self, sku: Optional[str] = None) -> List[Dict]:
        """Returns the upcoming production batches."""
        with self._lock:
            batches = []
            for b in self.production_plan:
                if sku and b.sku != sku:
                    continue
                batches.append({
                    "batch_id": b.batch_id,
                    "sku": b.sku,
                    "warehouse_id": b.warehouse_id,
                    "total_quantity": b.quantity,
                    "reserved_quantity": b.reserved_quantity,
                    "available_quantity": b.available_quantity,
                    "completion_date": b.completion_date.isoformat(),
                })
            return batches

    def get_route(self, origin_warehouse_id: str, destination_region: str) -> ShippingRoute:
        """Finds the shipping route or generates a conservative default."""
        route = self.shipping_matrix.get((origin_warehouse_id, destination_region))
        if not route:
            # Default fallback route if unspecified
            route = ShippingRoute(
                origin_warehouse_id=origin_warehouse_id,
                destination_region=destination_region,
                transit_days=4,
                cost_per_unit=8.0,
                fixed_shipment_fee=40.0,
            )
        return route

    # -------------------------------------------------------------------------
    # Core ATP (Available-To-Promise) Evaluation & Sourcing Logic
    # -------------------------------------------------------------------------

    def evaluate_order(self, order: CustomerOrder) -> OrderPromise:
        """
        Evaluates the best fulfillment plan for an incoming customer order without reserving.
        Decides:
        1. Single-warehouse on-hand fulfillment (fastest / cheapest, 0 split chaos).
        2. Multi-warehouse split shipment on-hand (with penalty consideration).
        3. Future incoming production allocation at Plant Warehouse.
        """
        with self._lock:
            # Candidate 1: Single warehouse on-hand fulfillment
            single_options = self._evaluate_single_warehouse_options(order)
            if single_options:
                # Best single option: minimize arrival date, then total shipping cost
                best_single = sorted(
                    single_options,
                    key=lambda opt: (opt.promised_delivery_date, opt.total_shipping_cost)
                )[0]
                return best_single

            # Candidate 2: Multi-warehouse split shipment from on-hand stock
            split_option = None
            if order.allow_split:
                split_option = self._evaluate_split_shipment_options(order)

            # Candidate 3: Incoming production schedule ATP
            production_option = self._evaluate_future_production_options(order)

            # Decision criteria between split on-hand vs waiting for future production:
            # Split shipment creates warehouse coordination overhead.
            # If a future production batch is available within 1-2 days of the split arrival date
            # and avoids split shipments, it may be preferred.
            if split_option and production_option:
                # If split delivers significantly earlier, deliver via split
                if split_option.promised_delivery_date <= production_option.promised_delivery_date:
                    return split_option
                else:
                    return production_option
            elif split_option:
                return split_option
            elif production_option:
                return production_option

            # Fallback: Cannot satisfy within planned horizon
            return OrderPromise(
                order_id=order.order_id,
                customer_name=order.customer_name,
                sku=order.sku,
                requested_quantity=order.quantity,
                status="REJECTED",
                promised_delivery_date=None,
                total_shipping_cost=0.0,
                legs=[],
                is_split=False,
                split_penalty=0.0,
                explanation=f"Insufficient on-hand stock and no incoming production batch can satisfy order quantity {order.quantity} of {order.sku}.",
            )

    def _evaluate_single_warehouse_options(self, order: CustomerOrder) -> List[OrderPromise]:
        """Finds all single warehouses that can independently fulfill 100% of order quantity."""
        options: List[OrderPromise] = []
        for wh_id, wh in self.warehouses.items():
            inv = self.inventory.get((order.sku, wh_id))
            if inv and inv.available_stock >= order.quantity:
                route = self.get_route(wh_id, order.destination_region)
                dispatch_date = order.order_date + timedelta(days=wh.dispatch_lead_days)
                arrival_date = dispatch_date + timedelta(days=route.transit_days)

                total_cost = (order.quantity * route.cost_per_unit) + route.fixed_shipment_fee
                leg = FulfillmentLeg(
                    warehouse_id=wh_id,
                    warehouse_name=wh.name,
                    source_type="ON_HAND",
                    batch_id=None,
                    quantity=order.quantity,
                    dispatch_date=dispatch_date,
                    arrival_date=arrival_date,
                    transit_days=route.transit_days,
                    cost_per_unit=route.cost_per_unit,
                    fixed_cost=route.fixed_shipment_fee,
                    total_cost=total_cost,
                )

                promise = OrderPromise(
                    order_id=order.order_id,
                    customer_name=order.customer_name,
                    sku=order.sku,
                    requested_quantity=order.quantity,
                    status="PROMISED",
                    promised_delivery_date=arrival_date,
                    total_shipping_cost=round(total_cost, 2),
                    legs=[leg],
                    is_split=False,
                    split_penalty=0.0,
                    explanation=f"Fulfilled 100% from released stock at {wh.name} in a single shipment. Delivery promised by {arrival_date.isoformat()}.",
                )
                options.append(promise)
        return options

    def _evaluate_split_shipment_options(self, order: CustomerOrder) -> Optional[OrderPromise]:
        """
        Evaluates fulfilling the order across multiple warehouses using available on-hand stock.
        Applies a penalty for each additional shipment leg to avoid chaotic fragmented shipments.
        """
        # Collect all warehouses that have some available stock > 0
        candidate_warehouses = []
        for wh_id, wh in self.warehouses.items():
            inv = self.inventory.get((order.sku, wh_id))
            if inv and inv.available_stock > 0:
                route = self.get_route(wh_id, order.destination_region)
                dispatch_date = order.order_date + timedelta(days=wh.dispatch_lead_days)
                arrival_date = dispatch_date + timedelta(days=route.transit_days)
                candidate_warehouses.append({
                    "warehouse": wh,
                    "available": inv.available_stock,
                    "route": route,
                    "dispatch_date": dispatch_date,
                    "arrival_date": arrival_date,
                })

        # Check total available across the network
        total_available = sum(c["available"] for c in candidate_warehouses)
        if total_available < order.quantity:
            return None  # Cannot fulfill completely from on-hand stock alone

        # Sort candidates to prioritize closest/fastest regional warehouses first
        candidate_warehouses.sort(key=lambda c: (c["arrival_date"], c["route"].cost_per_unit))

        remaining = order.quantity
        legs: List[FulfillmentLeg] = []
        total_freight_cost = 0.0

        for c in candidate_warehouses:
            if remaining <= 0:
                break
            wh = c["warehouse"]
            route = c["route"]
            alloc_qty = min(remaining, c["available"])
            remaining -= alloc_qty

            leg_cost = (alloc_qty * route.cost_per_unit) + route.fixed_shipment_fee
            total_freight_cost += leg_cost

            legs.append(FulfillmentLeg(
                warehouse_id=wh.id,
                warehouse_name=wh.name,
                source_type="ON_HAND",
                batch_id=None,
                quantity=alloc_qty,
                dispatch_date=c["dispatch_date"],
                arrival_date=c["arrival_date"],
                transit_days=route.transit_days,
                cost_per_unit=route.cost_per_unit,
                fixed_cost=route.fixed_shipment_fee,
                total_cost=round(leg_cost, 2),
            ))

        if remaining > 0:
            return None

        # Split shipment penalty calculation: $penalty * (num_legs - 1)
        num_shipments = len(legs)
        split_penalty = self.split_shipment_penalty * (num_shipments - 1) if num_shipments > 1 else 0.0
        final_promised_date = max(leg.arrival_date for leg in legs)
        total_shipping_cost = round(total_freight_cost + split_penalty, 2)

        return OrderPromise(
            order_id=order.order_id,
            customer_name=order.customer_name,
            sku=order.sku,
            requested_quantity=order.quantity,
            status="PROMISED",
            promised_delivery_date=final_promised_date,
            total_shipping_cost=total_shipping_cost,
            legs=legs,
            is_split=(num_shipments > 1),
            split_penalty=split_penalty,
            explanation=f"Split fulfillment across {num_shipments} warehouses. Final package arriving by {final_promised_date.isoformat()} (Split handling penalty applied: ${split_penalty}).",
        )

    def _evaluate_future_production_options(self, order: CustomerOrder) -> Optional[OrderPromise]:
        """
        Evaluates satisfying order from scheduled future production batches at the Plant Warehouse.
        ATP timeline walks through incoming production dates to calculate delivery promise date.
        """
        matching_batches = [
            b for b in self.production_plan
            if b.sku == order.sku and b.available_quantity > 0 and b.completion_date >= order.order_date
        ]
        # Sort chronologically by completion date
        matching_batches.sort(key=lambda b: b.completion_date)

        total_future_avail = sum(b.available_quantity for b in matching_batches)
        if total_future_avail < order.quantity:
            return None  # Even future production schedule is insufficient

        plant_wh = self.warehouses["WH-PLANT"]
        route = self.get_route(plant_wh.id, order.destination_region)

        remaining = order.quantity
        legs: List[FulfillmentLeg] = []
        total_cost = 0.0

        for batch in matching_batches:
            if remaining <= 0:
                break
            alloc_qty = min(remaining, batch.available_quantity)
            remaining -= alloc_qty

            dispatch_date = batch.completion_date + timedelta(days=plant_wh.dispatch_lead_days)
            arrival_date = dispatch_date + timedelta(days=route.transit_days)
            leg_cost = (alloc_qty * route.cost_per_unit) + route.fixed_shipment_fee
            total_cost += leg_cost

            legs.append(FulfillmentLeg(
                warehouse_id=plant_wh.id,
                warehouse_name=plant_wh.name,
                source_type="FUTURE_PRODUCTION",
                batch_id=batch.batch_id,
                quantity=alloc_qty,
                dispatch_date=dispatch_date,
                arrival_date=arrival_date,
                transit_days=route.transit_days,
                cost_per_unit=route.cost_per_unit,
                fixed_cost=route.fixed_shipment_fee,
                total_cost=round(leg_cost, 2),
            ))

        promised_delivery_date = max(leg.arrival_date for leg in legs)
        batches_str = ", ".join(leg.batch_id for leg in legs if leg.batch_id)

        return OrderPromise(
            order_id=order.order_id,
            customer_name=order.customer_name,
            sku=order.sku,
            requested_quantity=order.quantity,
            status="PROMISED",
            promised_delivery_date=promised_delivery_date,
            total_shipping_cost=round(total_cost, 2),
            legs=legs,
            is_split=False,
            split_penalty=0.0,
            explanation=f"Promised from incoming factory production [{batches_str}]. Delivery date {promised_delivery_date.isoformat()} based on production completion + transit.",
        )

    # -------------------------------------------------------------------------
    # Atomic Execution & Stock Locking (Fixes the Concurrency Race Condition)
    # -------------------------------------------------------------------------

    def promise_and_reserve(self, order: CustomerOrder) -> OrderPromise:
        """
        ATOMIC OPERATION:
        Evaluates fulfillment and commits the stock reservation inside a synchronized lock.
        Guarantees that two concurrent orders competing for the last unit cannot both reserve it.
        """
        with self._lock:
            # 1. Evaluate candidate fulfillment based on live real-time available balances
            promise = self.evaluate_order(order)
            if promise.status != "PROMISED":
                self.orders_history.append(promise)
                return promise

            # 2. Commit reservations atomically for every fulfillment leg
            for leg in promise.legs:
                if leg.source_type == "ON_HAND":
                    inv = self.inventory.get((order.sku, leg.warehouse_id))
                    if not inv or inv.available_stock < leg.quantity:
                        raise InsufficientInventoryError(
                            f"Race condition detected: Warehouse {leg.warehouse_id} no longer has {leg.quantity} available!"
                        )
                    # Deduct from available by increasing reserved stock
                    inv.reserved_stock += leg.quantity

                    # Record reservation audit entry
                    res = Reservation(
                        reservation_id=f"RES-{uuid.uuid4().hex[:8].upper()}",
                        order_id=order.order_id,
                        sku=order.sku,
                        warehouse_id=leg.warehouse_id,
                        source_type="ON_HAND",
                        batch_id=None,
                        quantity=leg.quantity,
                        reserved_at=datetime.now(),
                    )
                    self.reservations.append(res)

                elif leg.source_type == "FUTURE_PRODUCTION":
                    # Reserve against the production batch
                    batch = next((b for b in self.production_plan if b.batch_id == leg.batch_id), None)
                    if not batch or batch.available_quantity < leg.quantity:
                        raise InsufficientInventoryError(
                            f"Race condition detected: Production batch {leg.batch_id} no longer has {leg.quantity} unallocated units!"
                        )
                    batch.reserved_quantity += leg.quantity

                    res = Reservation(
                        reservation_id=f"RES-FUT-{uuid.uuid4().hex[:8].upper()}",
                        order_id=order.order_id,
                        sku=order.sku,
                        warehouse_id=leg.warehouse_id,
                        source_type="FUTURE_PRODUCTION",
                        batch_id=leg.batch_id,
                        quantity=leg.quantity,
                        reserved_at=datetime.now(),
                    )
                    self.reservations.append(res)

            self.orders_history.append(promise)
            return promise

    def reset_state(self):
        """Resets the engine back to fresh initial state."""
        with self._lock:
            self.warehouses.clear()
            self.inventory.clear()
            self.production_plan.clear()
            self.shipping_matrix.clear()
            self.reservations.clear()
            self.orders_history.clear()
            self._seed_default_data()
