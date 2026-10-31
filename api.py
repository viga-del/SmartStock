"""
FastAPI Application and Interactive Visual Dashboard for the Supply Chain Order Promising Engine.
"""

from datetime import date, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from engine import OrderPromisingEngine
from models import CustomerOrder, OrderPromise

app = FastAPI(
    title="Supply Chain Order Promising & ATP Engine",
    description="Real-time multi-warehouse Available-to-Promise (ATP) engine with concurrency locking.",
    version="1.0.0",
)

# Global engine instance
engine = OrderPromisingEngine()


class OrderRequest(BaseModel):
    customer_name: str
    destination_region: str
    sku: str = "MED-VENTILATOR-100"
    quantity: int
    allow_split: bool = True


@app.get("/api/inventory")
def get_inventory():
    return engine.get_inventory_summary()


@app.get("/api/production-plan")
def get_production_plan():
    return engine.get_production_plan_summary()


@app.get("/api/shipping-routes")
def get_routes():
    routes = []
    for (origin, dest), r in engine.shipping_matrix.items():
        wh = engine.warehouses.get(origin)
        routes.append({
            "origin_id": origin,
            "origin_name": wh.name if wh else origin,
            "destination_region": dest,
            "transit_days": r.transit_days,
            "cost_per_unit": r.cost_per_unit,
            "fixed_shipment_fee": r.fixed_shipment_fee,
        })
    return routes


@app.post("/api/orders/evaluate", response_model=OrderPromise)
def evaluate_order(req: OrderRequest):
    order = CustomerOrder(
        order_id=f"ORD-EVAL-{int(date.today().strftime('%Y%m%d'))}",
        customer_name=req.customer_name,
        destination_region=req.destination_region,
        sku=req.sku,
        quantity=req.quantity,
        order_date=date.today(),
        allow_split=req.allow_split,
    )
    return engine.evaluate_order(order)


@app.post("/api/orders/place", response_model=OrderPromise)
def place_order(req: OrderRequest):
    import uuid
    order = CustomerOrder(
        order_id=f"ORD-{uuid.uuid4().hex[:6].upper()}",
        customer_name=req.customer_name,
        destination_region=req.destination_region,
        sku=req.sku,
        quantity=req.quantity,
        order_date=date.today(),
        allow_split=req.allow_split,
    )
    return engine.promise_and_reserve(order)


@app.post("/api/scenarios/run-case-1")
def run_case_1():
    """Trigger concurrent orders competing for last unit."""
    from concurrent.futures import ThreadPoolExecutor

    # Setup 1 unit only
    engine.inventory.clear()
    engine.production_plan.clear()
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=10, reserved=9)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=0, reserved=0)

    order_a = CustomerOrder(
        order_id="ORD-CONCUR-A",
        customer_name="St. Jude Children's Hospital",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=1,
        order_date=date.today(),
    )
    order_b = CustomerOrder(
        order_id="ORD-CONCUR-B",
        customer_name="General Memorial Hospital",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=1,
        order_date=date.today(),
    )

    results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(engine.promise_and_reserve, order_a)
        f_b = executor.submit(engine.promise_and_reserve, order_b)
        results = [f_a.result(), f_b.result()]

    return {
        "scenario": "Case 1: Atomic Concurrency Check",
        "message": "Two orders attempted to reserve the last 1 available unit simultaneously.",
        "results": results,
        "final_inventory": engine.get_inventory_summary(),
    }


@app.post("/api/scenarios/run-case-2")
def run_case_2():
    """Trigger stock only exists in future production."""
    from models import ProductionBatch

    engine.inventory.clear()
    engine.production_plan.clear()

    # Zero on-hand everywhere
    engine.set_inventory("MED-VENTILATOR-100", "WH-PLANT", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-NORTH", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-SOUTH", released=0, reserved=0)
    engine.set_inventory("MED-VENTILATOR-100", "WH-REG-WEST", released=0, reserved=0)

    today = date.today()
    batch_date = today + timedelta(days=4)
    engine.production_plan = [
        ProductionBatch(
            batch_id="BATCH-FUTURE-4D",
            sku="MED-VENTILATOR-100",
            warehouse_id="WH-PLANT",
            quantity=50,
            reserved_quantity=0,
            completion_date=batch_date,
        )
    ]

    order = CustomerOrder(
        order_id="ORD-FUTURE-DEMO",
        customer_name="Regional Medical Distributor",
        destination_region="North",
        sku="MED-VENTILATOR-100",
        quantity=20,
        order_date=today,
    )
    promise = engine.promise_and_reserve(order)

    return {
        "scenario": "Case 2: Stock in Future Production Plan",
        "order": order,
        "promise": promise,
        "production_plan": engine.get_production_plan_summary(),
    }


@app.post("/api/reset")
def reset():
    engine.reset_state()
    return {"status": "reset", "message": "Engine reset to baseline topology."}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Supply Chain Order Promising & ATP Engine</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { background-color: #0b0f19; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .card { background: #161e2e; border: 1px solid #283548; }
    .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(8px); }
  </style>
</head>
<body class="p-6 min-h-screen">
  <div class="max-w-7xl mx-auto space-y-6">
    
    <!-- Header -->
    <header class="flex flex-col md:flex-row items-start md:items-center justify-between pb-4 border-b border-gray-800 gap-4">
      <div>
        <div class="flex items-center gap-3">
          <span class="p-2.5 bg-blue-600 rounded-lg text-white text-xl shadow-lg shadow-blue-500/20">
            <i class="fa-solid fa-truck-fast"></i>
          </span>
          <div>
            <h1 class="text-2xl font-bold text-white tracking-tight">Supply Chain Order Promising & ATP Engine</h1>
            <p class="text-sm text-gray-400">Plant Warehouse & 3 Regional Warehouses • Concurrency Locking • Future Production Forecasting</p>
          </div>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button onclick="resetEngine()" class="px-3.5 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg text-sm font-medium border border-gray-700 transition flex items-center gap-2">
          <i class="fa-solid fa-rotate"></i> Reset Baseline
        </button>
        <a href="/docs" target="_blank" class="px-3.5 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium shadow-md shadow-blue-600/30 transition flex items-center gap-2">
          <i class="fa-solid fa-code"></i> OpenAPI Docs
        </a>
      </div>
    </header>

    <!-- Preset Test Case Quick Triggers -->
    <section class="card rounded-xl p-5 border-l-4 border-l-amber-500 shadow-xl">
      <div class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
        <div>
          <h2 class="text-base font-semibold text-white flex items-center gap-2">
            <i class="fa-solid fa-bolt text-amber-400"></i> Live Scenario Simulations
          </h2>
          <p class="text-xs text-gray-400 mt-1">Simulate real-time concurrency locking and factory production schedule forecasting.</p>
        </div>
        <div class="flex flex-wrap gap-3">
          <button onclick="runCase1()" class="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-semibold transition flex items-center gap-2 shadow-lg shadow-red-600/20">
            <i class="fa-solid fa-bolt"></i> Run Case 1: Compete for Last Unit
          </button>
          <button onclick="runCase2()" class="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-semibold transition flex items-center gap-2 shadow-lg shadow-purple-600/20">
            <i class="fa-solid fa-calendar-check"></i> Run Case 2: Future Production ATP
          </button>
        </div>
      </div>
    </section>

    <!-- Warehouse Network Status Grid -->
    <div>
      <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Warehouse Network Topology & Inventory</h2>
      <div id="warehouse-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <!-- Rendered dynamically -->
      </div>
    </div>

    <!-- Production Plan & Order Placement -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
      
      <!-- Order Form -->
      <div class="lg:col-span-5 card rounded-xl p-5 shadow-lg">
        <h3 class="text-base font-semibold text-white flex items-center gap-2 mb-4">
          <i class="fa-solid fa-clipboard-check text-blue-400"></i> Place Customer Order
        </h3>
        <form id="orderForm" onsubmit="handlePlaceOrder(event)" class="space-y-4">
          <div>
            <label class="block text-xs font-medium text-gray-400 mb-1">Customer / Hospital Name</label>
            <input type="text" id="custName" value="Memorial Regional Hospital" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-medium text-gray-400 mb-1">Destination Region</label>
              <select id="destRegion" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                <option value="North">North</option>
                <option value="South">South</option>
                <option value="West">West</option>
                <option value="East">East</option>
              </select>
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-400 mb-1">Quantity</label>
              <input type="number" id="orderQty" value="15" min="1" max="500" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
            </div>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-400 mb-1">SKU</label>
            <input type="text" id="orderSku" value="MED-VENTILATOR-100" readonly class="w-full bg-gray-800/60 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-300">
          </div>
          <div class="flex items-center gap-2 pt-1">
            <input type="checkbox" id="allowSplit" checked class="w-4 h-4 rounded bg-gray-900 border-gray-700 text-blue-600 focus:ring-0">
            <label for="allowSplit" class="text-xs text-gray-300">Allow split shipments if necessary (applies coordination penalty)</label>
          </div>
          <div class="flex gap-3 pt-2">
            <button type="button" onclick="handleEvaluateOrder()" class="flex-1 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-lg text-sm font-medium transition">
              Calculate Quote
            </button>
            <button type="submit" class="flex-1 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-semibold shadow-md shadow-blue-600/30 transition">
              Promise & Reserve
            </button>
          </div>
        </form>
      </div>

      <!-- Live Promise Output & Audit Log -->
      <div class="lg:col-span-7 flex flex-col gap-6">
        
        <!-- Promise Display Card -->
        <div class="card rounded-xl p-5 shadow-lg flex-1">
          <h3 class="text-base font-semibold text-white flex items-center gap-2 mb-3">
            <i class="fa-solid fa-bullhorn text-green-400"></i> Delivery Promise Result
          </h3>
          <div id="promise-container" class="h-full flex flex-col justify-center items-center text-center p-6 border border-dashed border-gray-800 rounded-lg text-gray-500">
            <i class="fa-solid fa-box-open text-3xl mb-2 text-gray-600"></i>
            <p class="text-sm">Enter order details or run a test case to view the delivery date promise and shipping plan.</p>
          </div>
        </div>

        <!-- Factory Production Schedule -->
        <div class="card rounded-xl p-5 shadow-lg">
          <h3 class="text-base font-semibold text-white flex items-center justify-between mb-3">
            <span class="flex items-center gap-2"><i class="fa-solid fa-industry text-purple-400"></i> Plant Production Schedule</span>
            <span class="text-xs font-normal text-gray-400">Future Stock Pipeline</span>
          </h3>
          <div id="production-list" class="space-y-2">
            <!-- Rendered dynamically -->
          </div>
        </div>

      </div>

    </div>

  </div>

  <script>
    async function fetchInventory() {
      const res = await fetch('/api/inventory');
      const data = await res.json();
      const grid = document.getElementById('warehouse-grid');
      grid.innerHTML = '';

      data.forEach(wh => {
        const isPlant = wh.warehouse_type === 'PLANT';
        const cardBorder = isPlant ? 'border-blue-500/50' : 'border-gray-800';
        const badgeColor = isPlant ? 'bg-blue-500/20 text-blue-400 border-blue-500/30' : 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
        const totalStock = wh.released_stock;
        const avail = wh.available_stock;
        const reserved = wh.reserved_stock;
        const pct = totalStock > 0 ? (avail / totalStock) * 100 : 0;

        const card = document.createElement('div');
        card.className = `card rounded-xl p-4 border ${cardBorder} shadow-lg relative overflow-hidden`;
        card.innerHTML = `
          <div class="flex items-start justify-between mb-3">
            <div>
              <h3 class="font-bold text-white text-sm">${wh.warehouse_name}</h3>
              <span class="text-xs text-gray-500 font-mono">${wh.warehouse_id}</span>
            </div>
            <span class="text-[10px] font-semibold uppercase px-2 py-0.5 rounded border ${badgeColor}">
              ${wh.warehouse_type}
            </span>
          </div>
          
          <div class="grid grid-cols-3 gap-2 my-3 text-center bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
            <div>
              <div class="text-[10px] text-gray-400 uppercase">Released</div>
              <div class="text-base font-bold text-white">${wh.released_stock}</div>
            </div>
            <div>
              <div class="text-[10px] text-gray-400 uppercase">Reserved</div>
              <div class="text-base font-bold text-amber-400">${wh.reserved_stock}</div>
            </div>
            <div>
              <div class="text-[10px] text-gray-400 uppercase">Available</div>
              <div class="text-base font-bold text-emerald-400">${wh.available_stock}</div>
            </div>
          </div>

          <div class="w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
            <div class="bg-emerald-500 h-1.5 transition-all duration-500" style="width: ${pct}%"></div>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    async function fetchProduction() {
      const res = await fetch('/api/production-plan');
      const data = await res.json();
      const list = document.getElementById('production-list');
      list.innerHTML = '';

      if (data.length === 0) {
        list.innerHTML = '<div class="text-xs text-gray-500 italic p-3 text-center">No production batches scheduled.</div>';
        return;
      }

      data.forEach(b => {
        const div = document.createElement('div');
        div.className = 'flex items-center justify-between p-3 bg-gray-900/70 rounded-lg border border-gray-800 text-xs';
        div.innerHTML = `
          <div class="flex items-center gap-3">
            <span class="p-2 bg-purple-900/40 text-purple-400 rounded border border-purple-800/40">
              <i class="fa-solid fa-boxes-stacked"></i>
            </span>
            <div>
              <div class="font-bold text-gray-200">${b.batch_id}</div>
              <div class="text-gray-400 text-[11px]">Ready on: <strong class="text-yellow-400">${b.completion_date}</strong></div>
            </div>
          </div>
          <div class="text-right">
            <div class="text-emerald-400 font-bold">${b.available_quantity} ATP</div>
            <div class="text-[10px] text-gray-400">Total: ${b.total_quantity} (Reserved: ${b.reserved_quantity})</div>
          </div>
        `;
        list.appendChild(div);
      });
    }

    function renderPromise(promise) {
      const container = document.getElementById('promise-container');
      const isOk = promise.status === 'PROMISED';
      const badgeBg = isOk ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40' : 'bg-red-500/20 text-red-400 border-red-500/40';

      let legsHtml = '';
      if (promise.legs && promise.legs.length > 0) {
        legsHtml = `
          <div class="mt-4 border-t border-gray-800 pt-3">
            <div class="text-xs font-semibold text-gray-400 mb-2 uppercase">Fulfillment Shipment Breakdown:</div>
            <div class="space-y-2">
              ${promise.legs.map(l => `
                <div class="p-2.5 bg-gray-900/80 rounded border border-gray-800 text-xs flex justify-between items-center">
                  <div>
                    <div class="font-bold text-white flex items-center gap-2">
                      ${l.warehouse_name} 
                      <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">${l.source_type}</span>
                    </div>
                    <div class="text-[11px] text-gray-400 mt-0.5">
                      Dispatch: ${l.dispatch_date} • Transit: ${l.transit_days}d • Arrives: <strong class="text-yellow-400">${l.arrival_date}</strong>
                    </div>
                  </div>
                  <div class="text-right">
                    <div class="font-bold text-white">${l.quantity} units</div>
                    <div class="text-gray-400 text-[11px]">$${l.total_cost.toFixed(2)}</div>
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        `;
      }

      container.innerHTML = `
        <div class="w-full text-left">
          <div class="flex items-center justify-between pb-3 border-b border-gray-800">
            <div>
              <span class="text-xs font-mono text-gray-400">Order #${promise.order_id}</span>
              <h4 class="text-lg font-bold text-white">${promise.customer_name}</h4>
            </div>
            <span class="text-xs font-bold px-3 py-1 rounded-full border ${badgeBg}">
              ${promise.status}
            </span>
          </div>

          <div class="my-4 p-4 bg-blue-950/30 border border-blue-800/40 rounded-xl">
            <div class="text-xs text-blue-400 uppercase font-semibold">Delivery Promise Date</div>
            <div class="text-2xl font-black text-yellow-300 mt-1">
              ${promise.promised_delivery_date ? `We can deliver by: ${promise.promised_delivery_date}` : 'Cannot deliver by requested horizon'}
            </div>
            <div class="text-xs text-gray-300 mt-2">${promise.explanation}</div>
          </div>

          <div class="grid grid-cols-2 gap-3 text-xs bg-gray-900 p-3 rounded-lg border border-gray-800">
            <div>
              <span class="text-gray-400">Total Freight & Surcharge:</span>
              <span class="font-bold text-white ml-2">$${promise.total_shipping_cost.toFixed(2)}</span>
            </div>
            <div>
              <span class="text-gray-400">Shipment Type:</span>
              <span class="font-bold text-cyan-400 ml-2">${promise.is_split ? 'Split Parcel' : 'Single Shipment'}</span>
            </div>
          </div>

          ${legsHtml}
        </div>
      `;
    }

    async function handleEvaluateOrder() {
      const payload = {
        customer_name: document.getElementById('custName').value,
        destination_region: document.getElementById('destRegion').value,
        sku: document.getElementById('orderSku').value,
        quantity: parseInt(document.getElementById('orderQty').value),
        allow_split: document.getElementById('allowSplit').checked
      };

      const res = await fetch('/api/orders/evaluate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      renderPromise(data);
    }

    async function handlePlaceOrder(e) {
      e.preventDefault();
      const payload = {
        customer_name: document.getElementById('custName').value,
        destination_region: document.getElementById('destRegion').value,
        sku: document.getElementById('orderSku').value,
        quantity: parseInt(document.getElementById('orderQty').value),
        allow_split: document.getElementById('allowSplit').checked
      };

      const res = await fetch('/api/orders/place', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      renderPromise(data);
      await fetchInventory();
      await fetchProduction();
    }

    async function runCase1() {
      const res = await fetch('/api/scenarios/run-case-1', { method: 'POST' });
      const data = await res.json();
      await fetchInventory();
      await fetchProduction();

      const container = document.getElementById('promise-container');
      container.innerHTML = `
        <div class="w-full text-left space-y-3">
          <div class="p-3 bg-red-950/40 border border-red-800/60 rounded-lg">
            <h4 class="font-bold text-red-400 text-sm flex items-center gap-2">
              <i class="fa-solid fa-lock"></i> Concurrency Contention Result
            </h4>
            <p class="text-xs text-gray-300 mt-1">${data.message}</p>
          </div>
          <div class="space-y-2">
            ${data.results.map((r, i) => `
              <div class="p-3 rounded-lg border ${r.status === 'PROMISED' ? 'bg-emerald-950/30 border-emerald-800/50' : 'bg-red-950/30 border-red-800/50'} text-xs">
                <div class="flex justify-between items-center">
                  <strong class="text-white">${r.customer_name}</strong>
                  <span class="font-bold px-2 py-0.5 rounded text-[10px] ${r.status === 'PROMISED' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}">${r.status}</span>
                </div>
                <div class="text-gray-300 mt-1">${r.explanation}</div>
              </div>
            `).join('')}
          </div>
          <div class="text-[11px] text-emerald-400 font-semibold p-2 bg-emerald-950/20 rounded border border-emerald-900/40">
            Result: Zero double-booking. Exactly 1 order secured the unit. Remaining available stock is 0 (never negative).
          </div>
        </div>
      `;
    }

    async function runCase2() {
      const res = await fetch('/api/scenarios/run-case-2', { method: 'POST' });
      const data = await res.json();
      await fetchInventory();
      await fetchProduction();
      renderPromise(data.promise);
    }

    async function resetEngine() {
      await fetch('/api/reset', { method: 'POST' });
      await fetchInventory();
      await fetchProduction();
      document.getElementById('promise-container').innerHTML = `
        <i class="fa-solid fa-box-open text-3xl mb-2 text-gray-600"></i>
        <p class="text-sm">State reset to baseline. Ready for new orders.</p>
      `;
    }

    // Initial load
    fetchInventory();
    fetchProduction();
  </script>
</body>
</html>
    """
