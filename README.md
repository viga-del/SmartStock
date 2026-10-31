# 📦 Supply Chain Order Promising & ATP (Available-To-Promise) Engine

An enterprise-grade **Order Promising & Real-Time Inventory Sourcing Engine** designed to solve two classic supply chain breakdowns:
1. **The Overpromising / Race Condition Bug**: When two hospitals or distributors order the last available unit simultaneously, traditional systems double-book it. Our engine enforces **strict thread-safe atomic reservations**.
2. **Chaotic Split Shipments**: Fragmented orders across warehouses cause packaging overhead, multiple freight dockets, and receiving headaches for hospitals. Our engine uses an **optimization objective with a split-coordination penalty** to prefer consolidated single-warehouse shipments or close production batches.

---

## 🏛️ System Topology & Model

```
                     +---------------------------------------+
                     |    Central Plant Manufacturing Hub    |
                     |             (WH-PLANT)                |
                     |  - Central Stock                      |
                     |  - Receives Incoming Production Runs  |
                     +-------------------+-------------------+
                                         |
         +-------------------------------+-------------------------------+
         |                               |                               |
         v                               v                               v
+------------------+           +------------------+            +------------------+
| North Regional   |           | South Regional   |            | West Regional    |
| Warehouse        |           | Warehouse        |            | Warehouse        |
| (WH-REG-NORTH)   |           | (WH-REG-SOUTH)   |            | (WH-REG-WEST)    |
+--------+---------+           +--------+---------+            +--------+---------+
         |                              |                               |
         +------------------------------+-------------------------------+
                                        |
                                        v
                       +---------------------------------+
                       | Hospital / Distributor Delivery |
                       |    "We can deliver by Date X"   |
                       +---------------------------------+
```

### 1. Warehouses
- **Plant Warehouse (`WH-PLANT`)**: Central manufacturing hub where incoming factory production batches arrive.
- **Regional Warehouses (`WH-REG-NORTH`, `WH-REG-SOUTH`, `WH-REG-WEST`)**: Strategic regional fulfillment centers closer to customers with lower transit times.

### 2. Available-To-Promise (ATP) Equation
$$\text{Available Stock} = \max(0, \text{Released Stock} - \text{Reserved Stock})$$
When on-hand stock is insufficient, the engine computes **Capable-To-Promise (CTP)** against scheduled factory production batches:
$$\text{Promised Delivery Date} = \text{Batch Completion Date} + \text{Dispatch Lead Days} + \text{Transit Days}$$

### 3. Shipping Cost & Time Matrix
Each warehouse-to-region leg has:
- `transit_days` (lead time in calendar days)
- `cost_per_unit` (freight variable cost)
- `fixed_shipment_fee` (per-docket handling and packaging cost)

---

## 🚀 Key Demonstration Scenarios

### Case 1: Two Orders Competing for the Last Unit (Atomic Race Condition)
- **Problem**: 10 units released, 9 reserved $\rightarrow$ **Exactly 1 unit available**. Two hospital orders arrive at the exact same millisecond.
- **Engine Behavior**:
  - The thread-safe mutex lock (`threading.RLock`) ensures atomic serialization.
  - In a SQL/database production environment, this translates to `SELECT ... FOR UPDATE` or atomic CAS (`UPDATE inventory SET reserved = reserved + :qty WHERE (released - reserved) >= :qty`).
  - **Result**: Order A is promised for tomorrow. Order B is cleanly rejected or backordered. Inventory ends at 0 (never -1). Zero overselling occurs.

### Case 2: Stock Exists ONLY in Future Production Schedule
- **Problem**: 0 released stock anywhere in the network. Customer orders 25 units.
- **Engine Behavior**:
  - Checks the incoming factory production plan.
  - Identifies `BATCH-PROD-001` completing in $D+4$ days with 50 units.
  - Allocates 25 units from future batch (reducing future unallocated ATP to 25).
  - Calculates: Factory completion ($D+4$) + Plant dispatch lead ($1\text{ day}$) + Transit ($2\text{ days}$) = **Promised Delivery Date: $D+7$**.
  - Returns clear explanation: *"We can deliver by Date X from Plant Warehouse (Incoming Production Batch BATCH-PROD-001)"*.

### Case 3: Split vs Single Warehouse Optimization (Taming Chaos)
- **Problem**: Order for 20 units. Regional North has 12, Regional South has 10, Plant has 50.
- **Engine Behavior**:
  - Fulfilling via split (12 North + 8 South) incurs 2 separate delivery dates and an operational coordination penalty (\$40).
  - The engine determines that fulfilling 100% from Plant Warehouse in a single shipment provides a unified delivery window and lower total cost.

---

## 💻 Tech Stack & Architecture

- **Backend & Core Logic**: Python 3.12 (FastAPI, Pydantic v2, Pytest, Rich)
- **Concurrency & Atomicity**: Python `threading.RLock` with transactional commit semantics (simulating SQL `SELECT FOR UPDATE` / Redis distributed locks).
- **Interactive UI**: Single-page modern Glassmorphism Dashboard with live warehouse cards, stock gauges, order simulator, and one-click test case runners.
- **API Spec**: OpenAPI 3.0 / Swagger UI.

---

## ⚡ Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Demonstration Scenarios in Terminal
```bash
python run_demo.py --cli
```

### 3. Run Automated Unit & Concurrency Test Suite
```bash
python run_demo.py --test
```

### 4. Launch Interactive Web Dashboard & REST API
```bash
python run_demo.py --web
```
Open your browser to:
- **Interactive Dashboard**: `http://localhost:8000`
- **Swagger API Docs**: `http://localhost:8000/docs`

---

## 🧪 Automated Tests (`tests/test_engine.py`)

Run tests with `pytest`:
```bash
pytest tests/test_engine.py -v
```

Test coverage:
1. `test_network_topology_initialization`: Validates Plant + 3 Regional warehouses and route matrix.
2. `test_single_warehouse_fastest_cheapest`: Verifies closest regional warehouse selection.
3. `test_concurrency_race_condition_competing_for_last_unit`: ThreadPool parallel execution verifying zero double-booking on last unit.
4. `test_future_production_promise`: Verifies future batch date calculation and reservation.
5. `test_split_shipment_control`: Verifies multi-warehouse split logic and penalty application.
6. `test_insufficient_stock_rejection`: Verifies graceful failure when demand exceeds all supply horizons.

---

## 🛠️ Production Recommendations & Talking Points for the Interview

When presenting this in a vibe coding interview, highlight these architectural talking points:

1. **Distributed Concurrency**:
   - In single-node environments: In-memory mutex or SQLite `IMMEDIATE` transaction.
   - In distributed microservices: Redis distributed lock (`Redlock`) on key `lock:sku:{sku_id}` or Postgres `SELECT ... FOR UPDATE` with `REPEATABLE READ` isolation level.
2. **Event-Driven Architecture**:
   - Production completions can be published as Kafka / RabbitMQ events (`ProductionBatchCompleted`), which automatically transition stock from `ProductionBatch` to `ReleasedStock` and notify waiting backorders.
3. **Split Shipment Thresholding**:
   - Hospital receiving docks hate fragmented shipments. By incorporating a configurable split penalty, supply chain operations can set a business threshold: *"Only split if it saves more than 3 days of critical medical lead time."*
4. **Cloud Deployment**:
   - Containerized with Docker on **AWS ECS / GCP Cloud Run** or **Render / Railway / Fly.io**.
   - Persistence layer: PostgreSQL with timescale / PostGIS for geographical distance calculations.
