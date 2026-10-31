"""
FastAPI router / service layer for live inventory & fleet queries.

Exposes REST API endpoints that the agent swarm queries for real-time
inventory checks, fleet availability, and dispatch operations — all backed
by live SQLAlchemy database transactions.
"""

import os
import sys
import logging
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database.db_manager import (
    get_session,
    get_all_warehouses,
    get_warehouse_by_city,
    get_warehouse_by_id,
    get_nearest_warehouses,
    get_warehouse_inventory,
    get_inventory_by_category,
    check_stock_availability,
    deduct_inventory,
    get_regional_stock_summary,
    get_available_vehicles,
    assign_vehicle,
    release_vehicle,
    create_dispatch,
    get_all_dispatches,
    get_dispatch_by_id,
    get_active_hazard_zones,
    create_hazard_zone,
)

logger = logging.getLogger(__name__)

# ─── FastAPI App ──────────────────────────────────────────────────────────────

supplier_app = FastAPI(
    title="Emergency Supply Chain API",
    description="Live REST API for regional inventory, fleet, and dispatch management",
    version="1.0.0",
)

# ─── Pydantic Request/Response Models ────────────────────────────────────────


class StockCheckRequest(BaseModel):
    warehouse_id: int
    category: str
    quantity: int = Field(gt=0)


class DeductRequest(BaseModel):
    warehouse_id: int
    category: str
    quantity: int = Field(gt=0)


class DispatchRequest(BaseModel):
    source_warehouse_id: int
    destination_name: str
    destination_lat: float
    destination_lon: float
    items: Dict[str, int]  # category -> quantity
    vehicle_id: Optional[str] = None
    route_geometry: Optional[str] = None
    route_distance_km: Optional[float] = None
    route_duration_min: Optional[float] = None
    eta_minutes: Optional[float] = None
    hazard_avoided: bool = False
    detour_reason: Optional[str] = None
    agent_reasoning_log: Optional[str] = None


class HazardZoneRequest(BaseModel):
    name: str
    hazard_type: str
    severity: str = "high"
    center_lat: float
    center_lon: float
    radius_km: float = 50.0
    source: str = "open_meteo"


# ─── Warehouse Endpoints ─────────────────────────────────────────────────────


@supplier_app.get("/api/warehouses")
def list_warehouses():
    """List all active warehouses with their coordinates and metadata."""
    session = get_session()
    try:
        warehouses = get_all_warehouses(session)
        return [
            {
                "id": w.id,
                "name": w.name,
                "city": w.city,
                "region": w.region,
                "latitude": w.latitude,
                "longitude": w.longitude,
                "capacity_tier": w.capacity_tier,
                "is_active": w.is_active,
            }
            for w in warehouses
        ]
    finally:
        session.close()


@supplier_app.get("/api/warehouses/{warehouse_id}")
def get_warehouse(warehouse_id: int):
    """Get details for a specific warehouse."""
    session = get_session()
    try:
        wh = get_warehouse_by_id(session, warehouse_id)
        if not wh:
            raise HTTPException(status_code=404, detail="Warehouse not found")
        return {
            "id": wh.id,
            "name": wh.name,
            "city": wh.city,
            "region": wh.region,
            "latitude": wh.latitude,
            "longitude": wh.longitude,
            "capacity_tier": wh.capacity_tier,
            "is_active": wh.is_active,
        }
    finally:
        session.close()


@supplier_app.get("/api/warehouses/nearest")
def nearest_warehouses(lat: float, lon: float, limit: int = 5):
    """Find nearest warehouses to given coordinates."""
    session = get_session()
    try:
        results = get_nearest_warehouses(session, lat, lon, limit)
        return [
            {
                "warehouse": {
                    "id": wh.id,
                    "name": wh.name,
                    "city": wh.city,
                    "latitude": wh.latitude,
                    "longitude": wh.longitude,
                },
                "distance_km": round(dist, 2),
            }
            for wh, dist in results
        ]
    finally:
        session.close()


# ─── Inventory Endpoints ─────────────────────────────────────────────────────


@supplier_app.get("/api/inventory/{warehouse_id}")
def warehouse_inventory(warehouse_id: int):
    """Get all inventory items for a warehouse."""
    session = get_session()
    try:
        items = get_warehouse_inventory(session, warehouse_id)
        return [
            {
                "id": item.id,
                "category": item.category,
                "display_name": item.display_name,
                "quantity": item.quantity,
                "unit": item.unit,
                "weight_per_unit_kg": item.weight_per_unit_kg,
                "priority": item.priority,
                "min_reserve": item.min_reserve,
                "available_above_reserve": max(0, item.quantity - item.min_reserve),
                "last_restocked": item.last_restocked.isoformat() if item.last_restocked else None,
            }
            for item in items
        ]
    finally:
        session.close()


@supplier_app.get("/api/inventory/regional/{category}")
def regional_stock(category: str):
    """Get stock levels for a category across all warehouses."""
    session = get_session()
    try:
        return get_regional_stock_summary(session, category)
    finally:
        session.close()


@supplier_app.post("/api/inventory/check")
def check_stock(req: StockCheckRequest):
    """Check if sufficient stock is available for a requested quantity."""
    session = get_session()
    try:
        return check_stock_availability(
            session, req.warehouse_id, req.category, req.quantity
        )
    finally:
        session.close()


@supplier_app.post("/api/inventory/deduct")
def deduct_stock(req: DeductRequest):
    """Deduct inventory (ACID transaction). Enforces minimum reserves."""
    session = get_session()
    try:
        result = deduct_inventory(session, req.warehouse_id, req.category, req.quantity)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    finally:
        session.close()


# ─── Fleet Endpoints ──────────────────────────────────────────────────────────


@supplier_app.get("/api/fleet/{warehouse_id}")
def fleet_at_warehouse(warehouse_id: int, min_capacity_kg: float = 0):
    """Get available vehicles at a warehouse."""
    session = get_session()
    try:
        vehicles = get_available_vehicles(session, warehouse_id, min_capacity_kg)
        return [
            {
                "vehicle_id": v.vehicle_id,
                "vehicle_type": v.vehicle_type,
                "display_name": v.display_name,
                "capacity_kg": v.capacity_kg,
                "avg_speed_kmh": v.avg_speed_kmh,
                "status": v.status,
                "fuel_type": v.fuel_type,
            }
            for v in vehicles
        ]
    finally:
        session.close()


@supplier_app.post("/api/fleet/assign/{vehicle_id}")
def assign_fleet_vehicle(vehicle_id: str):
    """Assign a vehicle for dispatch."""
    session = get_session()
    try:
        result = assign_vehicle(session, vehicle_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    finally:
        session.close()


@supplier_app.post("/api/fleet/release/{vehicle_id}")
def release_fleet_vehicle(vehicle_id: str):
    """Release a vehicle back to available status."""
    session = get_session()
    try:
        result = release_vehicle(session, vehicle_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    finally:
        session.close()


# ─── Dispatch Endpoints ──────────────────────────────────────────────────────


@supplier_app.post("/api/dispatches")
def create_dispatch_order(req: DispatchRequest):
    """Create a new dispatch order with full manifest."""
    session = get_session()
    try:
        dispatch = create_dispatch(
            session,
            source_warehouse_id=req.source_warehouse_id,
            destination_name=req.destination_name,
            destination_lat=req.destination_lat,
            destination_lon=req.destination_lon,
            items_manifest=req.items,
            vehicle_id=req.vehicle_id,
            route_geometry=req.route_geometry,
            route_distance_km=req.route_distance_km,
            route_duration_min=req.route_duration_min,
            eta_minutes=req.eta_minutes,
            hazard_avoided=req.hazard_avoided,
            detour_reason=req.detour_reason,
            agent_reasoning_log=req.agent_reasoning_log,
        )
        return {
            "dispatch_id": dispatch.dispatch_id,
            "status": dispatch.status,
            "total_weight_kg": dispatch.total_weight_kg,
            "created_at": dispatch.created_at.isoformat(),
        }
    finally:
        session.close()


@supplier_app.get("/api/dispatches")
def list_dispatches():
    """List all dispatch orders."""
    session = get_session()
    try:
        dispatches = get_all_dispatches(session)
        return [
            {
                "dispatch_id": d.dispatch_id,
                "source_warehouse_id": d.source_warehouse_id,
                "vehicle_id": d.vehicle_id,
                "destination_name": d.destination_name,
                "destination_lat": d.destination_lat,
                "destination_lon": d.destination_lon,
                "status": d.status,
                "items_manifest": d.items_manifest,
                "total_weight_kg": d.total_weight_kg,
                "route_distance_km": d.route_distance_km,
                "route_duration_min": d.route_duration_min,
                "eta_minutes": d.eta_minutes,
                "hazard_avoided": d.hazard_avoided,
                "detour_reason": d.detour_reason,
                "created_at": d.created_at.isoformat(),
            }
            for d in dispatches
        ]
    finally:
        session.close()


# ─── Hazard Zone Endpoints ───────────────────────────────────────────────────


@supplier_app.get("/api/hazard-zones")
def list_hazard_zones():
    """List all active hazard zones."""
    session = get_session()
    try:
        zones = get_active_hazard_zones(session)
        return [
            {
                "id": z.id,
                "name": z.name,
                "hazard_type": z.hazard_type,
                "severity": z.severity,
                "center_lat": z.center_lat,
                "center_lon": z.center_lon,
                "radius_km": z.radius_km,
                "source": z.source,
                "is_active": z.is_active,
                "detected_at": z.detected_at.isoformat(),
            }
            for z in zones
        ]
    finally:
        session.close()


@supplier_app.post("/api/hazard-zones")
def add_hazard_zone(req: HazardZoneRequest):
    """Register a new hazard zone."""
    session = get_session()
    try:
        zone = create_hazard_zone(
            session,
            name=req.name,
            hazard_type=req.hazard_type,
            severity=req.severity,
            center_lat=req.center_lat,
            center_lon=req.center_lon,
            radius_km=req.radius_km,
            source=req.source,
        )
        return {
            "id": zone.id,
            "name": zone.name,
            "is_active": zone.is_active,
        }
    finally:
        session.close()


# ─── Health Check ─────────────────────────────────────────────────────────────


@supplier_app.get("/api/health")
def health_check():
    """API health check."""
    session = get_session()
    try:
        from sqlalchemy import text
        session.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )
    finally:
        session.close()

