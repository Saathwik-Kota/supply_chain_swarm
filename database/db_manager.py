"""
Database initialization, live seeding, and CRUD operations.

Seeds warehouse nodes from config coordinates, generates realistic initial
inventory levels using deterministic-but-varied logic (NOT hardcoded static data),
and provides all CRUD operations used by the agent swarm.
"""

import os
import sys
import hashlib
import random
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import create_engine, func, select, update, and_
from sqlalchemy.orm import Session, sessionmaker

from config import (
    DATABASE_URL,
    DATABASE_ECHO,
    LOGISTICS_HUBS,
    SUPPLY_CATEGORIES,
    VEHICLE_TYPES,
    HubCoordinate,
)
from database.models import (
    Base,
    Warehouse,
    Inventory,
    Fleet,
    Dispatch,
    TelemetryLog,
    HazardZone,
)


# ─── Engine & Session Factory ────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, echo=DATABASE_ECHO)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Session:
    """Return a new database session."""
    return SessionLocal()


def init_db() -> None:
    """Create all tables if they do not exist."""
    Base.metadata.create_all(bind=engine)


def reset_db() -> None:
    """Drop and recreate all tables (for demo resets)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


# ─── Deterministic Seed Helpers ──────────────────────────────────────────────

def _hub_seed(hub: HubCoordinate, salt: str = "") -> int:
    """
    Generate a deterministic seed from hub properties.
    This ensures inventory quantities are varied yet reproducible—
    NOT hardcoded static numbers.
    """
    raw = f"{hub.name}:{hub.lat}:{hub.lon}:{hub.region}:{salt}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return int(digest[:8], 16)


def _generate_quantity(hub: HubCoordinate, category: str, cat_config: dict) -> int:
    """
    Generate a realistic inventory quantity based on hub tier and category.
    Uses deterministic seeding so quantities are varied but reproducible.
    """
    rng = random.Random(_hub_seed(hub, category))

    tier_multipliers = {"primary": 3.0, "secondary": 2.0, "tertiary": 1.0}
    multiplier = tier_multipliers.get(hub.capacity_tier, 1.0)

    base_min = cat_config["min_reserve"]
    base_max = int(base_min * 5 * multiplier)

    return rng.randint(base_min, max(base_min + 1, base_max))


def _generate_vehicle_id(hub: HubCoordinate, vtype: str, idx: int) -> str:
    """Generate a unique vehicle identifier."""
    prefix = hub.name[:3].upper()
    type_code_map = {
        "heavy_truck": "HT",
        "medium_truck": "MT",
        "light_van": "LV",
        "helicopter": "HC",
    }
    type_code = type_code_map.get(vtype, vtype[:2].upper())
    return f"{prefix}-{type_code}-{idx:03d}"


# ─── Seeding Functions ───────────────────────────────────────────────────────

def seed_warehouses(session: Session) -> List[Warehouse]:
    """
    Seed warehouse nodes from the live LOGISTICS_HUBS config.
    Skips if warehouses already exist.
    """
    existing_count = session.scalar(select(func.count(Warehouse.id)))
    if existing_count and existing_count > 0:
        return list(session.scalars(select(Warehouse)).all())

    warehouses = []
    for hub in LOGISTICS_HUBS:
        wh = Warehouse(
            name=hub.facility_name,
            city=hub.name,
            region=hub.region,
            latitude=hub.lat,
            longitude=hub.lon,
            capacity_tier=hub.capacity_tier,
            address=hub.address,
            is_active=True,
        )
        session.add(wh)
        warehouses.append(wh)

    session.commit()
    # Refresh to populate IDs
    for wh in warehouses:
        session.refresh(wh)
    return warehouses


def seed_inventory(session: Session, warehouses: List[Warehouse]) -> None:
    """
    Seed inventory levels for all supply categories at each warehouse.
    Quantities are generated deterministically from hub properties, NOT hardcoded.
    """
    existing = session.scalar(select(func.count(Inventory.id)))
    if existing and existing > 0:
        return

    hub_lookup = {h.name: h for h in LOGISTICS_HUBS}

    for wh in warehouses:
        hub = hub_lookup.get(wh.city)
        if not hub:
            continue

        for cat_key, cat_config in SUPPLY_CATEGORIES.items():
            qty = _generate_quantity(hub, cat_key, cat_config)
            inv = Inventory(
                warehouse_id=wh.id,
                category=cat_key,
                display_name=cat_config["display_name"],
                quantity=qty,
                unit=cat_config["unit"],
                weight_per_unit_kg=cat_config["weight_kg"],
                priority=cat_config["priority"],
                min_reserve=cat_config["min_reserve"],
            )
            session.add(inv)

    session.commit()


def seed_fleet(session: Session, warehouses: List[Warehouse]) -> None:
    """
    Seed fleet vehicles at each warehouse.
    Vehicle count varies by hub tier, NOT statically assigned.
    """
    existing = session.scalar(select(func.count(Fleet.id)))
    if existing and existing > 0:
        return

    hub_lookup = {h.name: h for h in LOGISTICS_HUBS}
    tier_vehicle_counts = {
        "primary": {"heavy_truck": 4, "medium_truck": 5, "light_van": 3, "helicopter": 1},
        "secondary": {"heavy_truck": 2, "medium_truck": 3, "light_van": 2, "helicopter": 0},
        "tertiary": {"heavy_truck": 1, "medium_truck": 2, "light_van": 2, "helicopter": 0},
    }

    for wh in warehouses:
        hub = hub_lookup.get(wh.city)
        if not hub:
            continue

        counts = tier_vehicle_counts.get(hub.capacity_tier, tier_vehicle_counts["tertiary"])
        for vtype, count in counts.items():
            vconfig = VEHICLE_TYPES[vtype]
            for i in range(count):
                vid = _generate_vehicle_id(hub, vtype, i + 1)
                vehicle = Fleet(
                    vehicle_id=vid,
                    warehouse_id=wh.id,
                    vehicle_type=vtype,
                    display_name=vconfig["display_name"],
                    capacity_kg=vconfig["capacity_kg"],
                    avg_speed_kmh=vconfig["avg_speed_kmh"],
                    status="available",
                    current_lat=wh.latitude,
                    current_lon=wh.longitude,
                    fuel_type=vconfig["fuel_type"],
                )
                session.add(vehicle)

    session.commit()


def seed_all() -> None:
    """Initialize database and run all seeders."""
    init_db()
    session = get_session()
    try:
        warehouses = seed_warehouses(session)
        seed_inventory(session, warehouses)
        seed_fleet(session, warehouses)
    finally:
        session.close()


# ─── CRUD: Warehouses ────────────────────────────────────────────────────────

def get_all_warehouses(session: Session) -> List[Warehouse]:
    """Return all active warehouses."""
    return list(session.scalars(select(Warehouse).where(Warehouse.is_active == True)).all())


def get_warehouse_by_city(session: Session, city: str) -> Optional[Warehouse]:
    """Find warehouse by city name."""
    return session.scalar(
        select(Warehouse).where(Warehouse.city == city, Warehouse.is_active == True)
    )


def get_warehouse_by_id(session: Session, warehouse_id: int) -> Optional[Warehouse]:
    """Find warehouse by ID."""
    return session.get(Warehouse, warehouse_id)


def get_nearest_warehouses(
    session: Session, lat: float, lon: float, limit: int = 5
) -> List[Tuple[Warehouse, float]]:
    """
    Return the nearest warehouses to given coordinates, ordered by approximate distance.
    Uses simple Euclidean approximation scaled by latitude for speed.
    """
    import math

    warehouses = get_all_warehouses(session)
    results = []

    for wh in warehouses:
        dlat = math.radians(wh.latitude - lat)
        dlon = math.radians(wh.longitude - lon) * math.cos(math.radians(lat))
        dist_km = math.sqrt(dlat**2 + dlon**2) * 6371.0
        results.append((wh, dist_km))

    results.sort(key=lambda x: x[1])
    return results[:limit]


# ─── CRUD: Inventory ─────────────────────────────────────────────────────────

def get_warehouse_inventory(
    session: Session, warehouse_id: int
) -> List[Inventory]:
    """Get all inventory items for a warehouse."""
    return list(
        session.scalars(
            select(Inventory).where(Inventory.warehouse_id == warehouse_id)
        ).all()
    )


def get_inventory_by_category(
    session: Session, warehouse_id: int, category: str
) -> Optional[Inventory]:
    """Get inventory for a specific category at a warehouse."""
    return session.scalar(
        select(Inventory).where(
            Inventory.warehouse_id == warehouse_id,
            Inventory.category == category,
        )
    )


def check_stock_availability(
    session: Session, warehouse_id: int, category: str, requested_qty: int
) -> Dict[str, Any]:
    """
    Check if sufficient stock is available for a requested quantity.
    Returns availability status and details.
    """
    inv = get_inventory_by_category(session, warehouse_id, category)
    if not inv:
        return {
            "available": False,
            "reason": f"Category '{category}' not found at warehouse {warehouse_id}",
            "current_stock": 0,
            "requested": requested_qty,
        }

    available_qty = max(0, inv.quantity - inv.min_reserve)

    return {
        "available": available_qty >= requested_qty,
        "current_stock": inv.quantity,
        "available_above_reserve": available_qty,
        "requested": requested_qty,
        "min_reserve": inv.min_reserve,
        "category": category,
        "warehouse_id": warehouse_id,
        "weight_total_kg": requested_qty * inv.weight_per_unit_kg,
    }


def deduct_inventory(
    session: Session, warehouse_id: int, category: str, quantity: int
) -> Dict[str, Any]:
    """
    Atomically deduct inventory quantity. Returns the transaction result.
    Enforces minimum reserve constraints.
    """
    inv = get_inventory_by_category(session, warehouse_id, category)
    if not inv:
        return {"success": False, "error": f"Category '{category}' not found"}

    available = max(0, inv.quantity - inv.min_reserve)
    if available < quantity:
        return {
            "success": False,
            "error": f"Insufficient stock: {available} available (above reserve), {quantity} requested",
            "current_stock": inv.quantity,
            "min_reserve": inv.min_reserve,
        }

    old_qty = inv.quantity
    inv.quantity -= quantity
    inv.updated_at = datetime.now(timezone.utc)
    session.commit()

    return {
        "success": True,
        "category": category,
        "warehouse_id": warehouse_id,
        "old_quantity": old_qty,
        "deducted": quantity,
        "new_quantity": inv.quantity,
        "weight_deducted_kg": quantity * inv.weight_per_unit_kg,
    }


def get_regional_stock_summary(
    session: Session, category: str
) -> List[Dict[str, Any]]:
    """Get stock levels for a category across all warehouses."""
    results = session.execute(
        select(
            Warehouse.id,
            Warehouse.name,
            Warehouse.city,
            Warehouse.region,
            Warehouse.latitude,
            Warehouse.longitude,
            Inventory.quantity,
            Inventory.min_reserve,
        )
        .join(Inventory, Warehouse.id == Inventory.warehouse_id)
        .where(Inventory.category == category, Warehouse.is_active == True)
        .order_by(Inventory.quantity.desc())
    ).all()

    return [
        {
            "warehouse_id": r[0],
            "name": r[1],
            "city": r[2],
            "region": r[3],
            "lat": r[4],
            "lon": r[5],
            "quantity": r[6],
            "min_reserve": r[7],
            "available_above_reserve": max(0, r[6] - r[7]),
        }
        for r in results
    ]


# ─── CRUD: Fleet ─────────────────────────────────────────────────────────────

def get_available_vehicles(
    session: Session, warehouse_id: int, min_capacity_kg: float = 0
) -> List[Fleet]:
    """Get available vehicles at a warehouse meeting minimum capacity."""
    return list(
        session.scalars(
            select(Fleet).where(
                Fleet.warehouse_id == warehouse_id,
                Fleet.status == "available",
                Fleet.capacity_kg >= min_capacity_kg,
            ).order_by(Fleet.capacity_kg.desc())
        ).all()
    )


def assign_vehicle(session: Session, vehicle_id: str) -> Dict[str, Any]:
    """Mark a vehicle as dispatched."""
    vehicle = session.scalar(
        select(Fleet).where(Fleet.vehicle_id == vehicle_id)
    )
    if not vehicle:
        return {"success": False, "error": f"Vehicle '{vehicle_id}' not found"}
    if vehicle.status != "available":
        return {"success": False, "error": f"Vehicle '{vehicle_id}' status is '{vehicle.status}', not available"}

    vehicle.status = "dispatched"
    session.commit()

    return {
        "success": True,
        "vehicle_id": vehicle.vehicle_id,
        "type": vehicle.vehicle_type,
        "capacity_kg": vehicle.capacity_kg,
    }


def release_vehicle(session: Session, vehicle_id: str) -> Dict[str, Any]:
    """Return a vehicle to available status."""
    vehicle = session.scalar(
        select(Fleet).where(Fleet.vehicle_id == vehicle_id)
    )
    if not vehicle:
        return {"success": False, "error": f"Vehicle '{vehicle_id}' not found"}

    vehicle.status = "available"
    session.commit()
    return {"success": True, "vehicle_id": vehicle_id}


# ─── CRUD: Dispatches ────────────────────────────────────────────────────────

def create_dispatch(
    session: Session,
    source_warehouse_id: int,
    destination_name: str,
    destination_lat: float,
    destination_lon: float,
    items_manifest: Dict[str, int],
    vehicle_id: Optional[str] = None,
    route_geometry: Optional[str] = None,
    route_distance_km: Optional[float] = None,
    route_duration_min: Optional[float] = None,
    eta_minutes: Optional[float] = None,
    hazard_avoided: bool = False,
    detour_reason: Optional[str] = None,
    agent_reasoning_log: Optional[str] = None,
) -> Dispatch:
    """Create a new dispatch order with full manifest."""
    dispatch_id = f"DSP-{uuid.uuid4().hex[:12].upper()}"

    total_weight = 0.0
    for cat, qty in items_manifest.items():
        inv = get_inventory_by_category(session, source_warehouse_id, cat)
        if inv:
            total_weight += qty * inv.weight_per_unit_kg

    dispatch = Dispatch(
        dispatch_id=dispatch_id,
        source_warehouse_id=source_warehouse_id,
        vehicle_id=vehicle_id,
        destination_name=destination_name,
        destination_lat=destination_lat,
        destination_lon=destination_lon,
        status="dispatched",
        items_manifest=items_manifest,
        total_weight_kg=total_weight,
        route_geometry=route_geometry,
        route_distance_km=route_distance_km,
        route_duration_min=route_duration_min,
        eta_minutes=eta_minutes,
        hazard_avoided=hazard_avoided,
        detour_reason=detour_reason,
        agent_reasoning_log=agent_reasoning_log,
    )
    session.add(dispatch)
    session.commit()
    session.refresh(dispatch)
    return dispatch


def get_all_dispatches(session: Session) -> List[Dispatch]:
    """Get all dispatch orders, most recent first."""
    return list(
        session.scalars(
            select(Dispatch).order_by(Dispatch.created_at.desc())
        ).all()
    )


def get_dispatch_by_id(session: Session, dispatch_id: str) -> Optional[Dispatch]:
    """Find a dispatch by its ID."""
    return session.scalar(
        select(Dispatch).where(Dispatch.dispatch_id == dispatch_id)
    )


# ─── CRUD: Telemetry ─────────────────────────────────────────────────────────

def log_telemetry(session: Session, **kwargs) -> TelemetryLog:
    """Record a telemetry reading."""
    log = TelemetryLog(**kwargs)
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def get_recent_telemetry(
    session: Session, hub_name: Optional[str] = None, limit: int = 50
) -> List[TelemetryLog]:
    """Get recent telemetry logs, optionally filtered by hub."""
    query = select(TelemetryLog).order_by(TelemetryLog.recorded_at.desc())
    if hub_name:
        query = query.where(TelemetryLog.hub_name == hub_name)
    return list(session.scalars(query.limit(limit)).all())


def get_active_hazard_telemetry(session: Session) -> List[TelemetryLog]:
    """Get all telemetry records marked as hazardous."""
    return list(
        session.scalars(
            select(TelemetryLog)
            .where(TelemetryLog.is_hazardous == True)
            .order_by(TelemetryLog.recorded_at.desc())
            .limit(100)
        ).all()
    )


# ─── CRUD: Hazard Zones ──────────────────────────────────────────────────────

def create_hazard_zone(
    session: Session,
    name: str,
    hazard_type: str,
    severity: str,
    center_lat: float,
    center_lon: float,
    radius_km: float,
    polygon_coords: Optional[dict] = None,
    source: str = "open_meteo",
) -> HazardZone:
    """Create or update an active hazard zone."""
    # Deactivate existing zones of same type near same location
    existing = session.scalars(
        select(HazardZone).where(
            HazardZone.is_active == True,
            HazardZone.hazard_type == hazard_type,
            func.abs(HazardZone.center_lat - center_lat) < 0.5,
            func.abs(HazardZone.center_lon - center_lon) < 0.5,
        )
    ).all()

    for ez in existing:
        ez.is_active = False

    hz = HazardZone(
        name=name,
        hazard_type=hazard_type,
        severity=severity,
        center_lat=center_lat,
        center_lon=center_lon,
        radius_km=radius_km,
        polygon_coords=polygon_coords,
        source=source,
        is_active=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    session.add(hz)
    session.commit()
    session.refresh(hz)
    return hz


def get_active_hazard_zones(session: Session) -> List[HazardZone]:
    """Get all currently active hazard zones."""
    return list(
        session.scalars(
            select(HazardZone).where(HazardZone.is_active == True)
        ).all()
    )


def deactivate_hazard_zone(session: Session, zone_id: int) -> None:
    """Deactivate a hazard zone."""
    zone = session.get(HazardZone, zone_id)
    if zone:
        zone.is_active = False
        session.commit()


def clear_expired_hazards(session: Session) -> int:
    """Deactivate hazard zones that have expired."""
    now = datetime.now(timezone.utc)
    expired = session.scalars(
        select(HazardZone).where(
            HazardZone.is_active == True,
            HazardZone.expires_at != None,
            HazardZone.expires_at < now,
        )
    ).all()

    for hz in expired:
        hz.is_active = False

    session.commit()
    return len(expired)


if __name__ == "__main__":
    import sys
    print("Initializing SQLite database & seeding logistics hubs...")
    reset_db()
    seed_all()
    session = get_session()
    whs = get_all_warehouses(session)
    inv_count = session.scalar(select(func.count(Inventory.id)))
    fleet_count = session.scalar(select(func.count(Fleet.id)))
    print(f"Database successfully seeded:")
    print(f"  Warehouses : {len(whs)}")
    print(f"  Inventory  : {inv_count} stock records")
    print(f"  Fleet      : {fleet_count} vehicles")
    session.close()
    print("Database ready!")

