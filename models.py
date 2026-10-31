"""
Domain models for the Supply Chain Order Promising & ATP Engine.
Covers Warehouses, Inventory, Production Plans, Shipping Matrix, Orders, and Promises.
"""

from datetime import date, datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, computed_field


class WarehouseType(str, Enum):
    PLANT = "PLANT"
    REGIONAL = "REGIONAL"


class Warehouse(BaseModel):
    id: str
    name: str
    type: WarehouseType
    location: str
    dispatch_lead_days: int = 1  # Time taken to pick, pack & dispatch


class InventoryRecord(BaseModel):
    sku: str
    warehouse_id: str
    released_stock: int = Field(ge=0, description="Physically available released stock on shelves")
    reserved_stock: int = Field(default=0, ge=0, description="Stock reserved for confirmed orders")

    @computed_field
    def available_stock(self) -> int:
        """Net available stock that can be safely promised right now."""
        return max(0, self.released_stock - self.reserved_stock)


class ProductionBatch(BaseModel):
    batch_id: str
    sku: str
    warehouse_id: str  # Destination warehouse where batch arrives (usually Plant Warehouse)
    quantity: int = Field(ge=0, description="Total planned production units")
    reserved_quantity: int = Field(default=0, ge=0, description="Quantity already committed to future orders")
    completion_date: date

    @computed_field
    def available_quantity(self) -> int:
        """Unallocated production volume available to promise for future delivery."""
        return max(0, self.quantity - self.reserved_quantity)


class ShippingRoute(BaseModel):
    origin_warehouse_id: str
    destination_region: str
    transit_days: int = Field(ge=0, description="Lead time in transit days from warehouse to customer")
    cost_per_unit: float = Field(ge=0.0, description="Variable freight cost per unit")
    fixed_shipment_fee: float = Field(default=25.0, ge=0.0, description="Fixed handling & docket fee per shipment leg")


class CustomerOrder(BaseModel):
    order_id: str
    customer_name: str
    destination_region: str
    sku: str
    quantity: int = Field(gt=0)
    order_date: date
    max_acceptable_date: Optional[date] = None
    allow_split: bool = True


class FulfillmentLeg(BaseModel):
    warehouse_id: str
    warehouse_name: str
    source_type: str  # "ON_HAND" or "FUTURE_PRODUCTION"
    batch_id: Optional[str] = None
    quantity: int
    dispatch_date: date
    arrival_date: date
    transit_days: int
    cost_per_unit: float
    fixed_cost: float
    total_cost: float


class OrderPromise(BaseModel):
    order_id: str
    customer_name: str
    sku: str
    requested_quantity: int
    status: str  # "PROMISED", "REJECTED", "BACKORDERED"
    promised_delivery_date: Optional[date] = None
    total_shipping_cost: float = 0.0
    legs: List[FulfillmentLeg] = []
    is_split: bool = False
    split_penalty: float = 0.0
    explanation: str


class Reservation(BaseModel):
    reservation_id: str
    order_id: str
    sku: str
    warehouse_id: str
    source_type: str  # "ON_HAND" or "FUTURE_PRODUCTION"
    batch_id: Optional[str] = None
    quantity: int
    reserved_at: datetime = Field(default_factory=datetime.now)
