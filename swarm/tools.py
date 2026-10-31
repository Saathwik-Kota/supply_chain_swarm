"""
Tool definitions bound to live services & SQLAlchemy DB.

Each tool wraps a live service call (weather API, OSRM routing, database query)
and returns structured results for the agent swarm to consume.
All tools perform REAL operations — zero mock data.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import (
    LOGISTICS_HUBS,
    SUPPLY_CATEGORIES,
    HAZARD_RADIUS_KM,
    WeatherThresholds,
    DEFAULT_WEATHER_THRESHOLDS,
)
from database.db_manager import (
    get_session,
    get_all_warehouses,
    get_nearest_warehouses,
    get_warehouse_inventory,
    check_stock_availability,
    deduct_inventory,
    get_available_vehicles,
    assign_vehicle,
    create_dispatch,
    log_telemetry,
    create_hazard_zone,
    get_active_hazard_zones,
)
from services.weather_service import WeatherService, get_weather_service
from services.routing_service import RoutingService, get_routing_service

logger = logging.getLogger(__name__)


# ─── Telemetry Tools ─────────────────────────────────────────────────────────

def tool_scan_weather_corridors(
    custom_thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    [LIVE API] Scan all logistics corridor hubs for current weather conditions.
    Detects hazards based on configurable thresholds.
    Returns comprehensive weather assessment for all hubs.
    """
    start = time.time()
    ws = get_weather_service()

    thresholds = DEFAULT_WEATHER_THRESHOLDS
    if custom_thresholds:
        thresholds = WeatherThresholds(**{
            k: v for k, v in custom_thresholds.items()
            if hasattr(thresholds, k)
        })

    scan_result = ws.scan_all_corridors(thresholds)
    latency_ms = (time.time() - start) * 1000

    # Log telemetry to database
    session = get_session()
    try:
        for hub_data in scan_result.get("hubs", []):
            weather = hub_data.get("weather", {})
            assessment = hub_data.get("assessment", {})

            log_telemetry(
                session,
                hub_name=hub_data["hub_name"],
                latitude=hub_data["lat"],
                longitude=hub_data["lon"],
                temperature_c=weather.get("temperature_c"),
                humidity_pct=weather.get("humidity_pct"),
                precipitation_mm=weather.get("precipitation_mm"),
                wind_speed_kmh=weather.get("wind_speed_kmh"),
                wind_gusts_kmh=weather.get("wind_gusts_kmh"),
                weather_code=weather.get("weather_code"),
                weather_description=weather.get("weather_description"),
                is_hazardous=assessment.get("is_hazardous", False),
                hazard_type=assessment.get("primary_hazard_type"),
                severity=assessment.get("severity"),
                raw_api_response=weather.get("_meta"),
            )
    finally:
        session.close()

    scan_result["tool_latency_ms"] = round(latency_ms, 2)
    return scan_result


def tool_scan_custom_location(
    lat: float,
    lon: float,
    name: str = "Custom Location",
    custom_thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    [LIVE API] Scan a specific coordinate for weather and hazard conditions.
    Used for stress-testing and judge demonstrations.
    """
    start = time.time()
    ws = get_weather_service()

    thresholds = DEFAULT_WEATHER_THRESHOLDS
    if custom_thresholds:
        thresholds = WeatherThresholds(**{
            k: v for k, v in custom_thresholds.items()
            if hasattr(thresholds, k)
        })

    result = ws.scan_custom_location(lat, lon, name, thresholds)
    latency_ms = (time.time() - start) * 1000

    # Log to DB
    session = get_session()
    try:
        weather = result.get("weather", {})
        assessment = result.get("assessment", {})
        log_telemetry(
            session,
            hub_name=name,
            latitude=lat,
            longitude=lon,
            temperature_c=weather.get("temperature_c"),
            humidity_pct=weather.get("humidity_pct"),
            precipitation_mm=weather.get("precipitation_mm"),
            wind_speed_kmh=weather.get("wind_speed_kmh"),
            wind_gusts_kmh=weather.get("wind_gusts_kmh"),
            weather_code=weather.get("weather_code"),
            weather_description=weather.get("weather_description"),
            is_hazardous=assessment.get("is_hazardous", False),
            hazard_type=assessment.get("primary_hazard_type"),
            severity=assessment.get("severity"),
            raw_api_response=weather.get("_meta"),
        )
    finally:
        session.close()

    result["tool_latency_ms"] = round(latency_ms, 2)
    return result


def tool_scan_live_disaster_feeds() -> List[Dict[str, Any]]:
    """
    [LIVE API] Poll live natural disasters from USGS & NASA EONET feeds.
    Zero API keys required.
    """
    from services.disaster_service import get_disaster_service
    ds = get_disaster_service()
    regional = ds.get_regional_hazards()
    if not regional:
        # Include top global significant seismic / storm events if none directly in Indian landmass
        regional = ds.fetch_live_nasa_eonet_hazards()[:4] + ds.fetch_live_usgs_earthquakes(min_magnitude=4.8)[:4]
    return regional


def tool_register_hazard_zone(
    name: str,
    hazard_type: str,
    severity: str,
    center_lat: float,
    center_lon: float,
    radius_km: float = HAZARD_RADIUS_KM,
    source: str = "open_meteo",
) -> Dict[str, Any]:
    """
    [DATABASE] Register an active hazard zone in the database.
    Called by the Telemetry Agent when hazards are detected.
    """
    session = get_session()
    try:
        zone = create_hazard_zone(
            session, name, hazard_type, severity,
            center_lat, center_lon, radius_km, source=source,
        )
        return {
            "hazard_zone_id": zone.id,
            "name": zone.name,
            "hazard_type": zone.hazard_type,
            "severity": zone.severity,
            "center": {"lat": zone.center_lat, "lon": zone.center_lon},
            "radius_km": zone.radius_km,
            "is_active": zone.is_active,
        }
    finally:
        session.close()


# ─── Inventory Tools ─────────────────────────────────────────────────────────

def tool_find_nearest_warehouses(
    lat: float, lon: float, limit: int = 5
) -> List[Dict[str, Any]]:
    """
    [DATABASE] Find the nearest warehouses to a given coordinate.
    Returns warehouse details with distance.
    """
    session = get_session()
    try:
        results = get_nearest_warehouses(session, lat, lon, limit)
        return [
            {
                "warehouse_id": wh.id,
                "name": wh.name,
                "city": wh.city,
                "region": wh.region,
                "lat": wh.latitude,
                "lon": wh.longitude,
                "capacity_tier": wh.capacity_tier,
                "distance_km": round(dist, 2),
            }
            for wh, dist in results
        ]
    finally:
        session.close()


def tool_check_warehouse_inventory(
    warehouse_id: int, requested_items: Dict[str, int]
) -> Dict[str, Any]:
    """
    [DATABASE] Check if a warehouse can fulfill a set of requested items.
    Returns detailed availability for each category.
    """
    session = get_session()
    try:
        results = {}
        can_fulfill_all = True
        total_weight = 0.0

        for category, qty in requested_items.items():
            check = check_stock_availability(session, warehouse_id, category, qty)
            results[category] = check

            if not check["available"]:
                can_fulfill_all = False
            else:
                total_weight += check.get("weight_total_kg", 0)

        return {
            "warehouse_id": warehouse_id,
            "can_fulfill_all": can_fulfill_all,
            "items": results,
            "total_weight_kg": round(total_weight, 2),
        }
    finally:
        session.close()


def tool_get_warehouse_full_inventory(warehouse_id: int) -> List[Dict[str, Any]]:
    """
    [DATABASE] Get complete inventory listing for a warehouse.
    """
    session = get_session()
    try:
        items = get_warehouse_inventory(session, warehouse_id)
        return [
            {
                "category": item.category,
                "display_name": item.display_name,
                "quantity": item.quantity,
                "unit": item.unit,
                "weight_per_unit_kg": item.weight_per_unit_kg,
                "min_reserve": item.min_reserve,
                "available_above_reserve": max(0, item.quantity - item.min_reserve),
            }
            for item in items
        ]
    finally:
        session.close()


def tool_get_available_fleet(
    warehouse_id: int, min_capacity_kg: float = 0
) -> List[Dict[str, Any]]:
    """
    [DATABASE] Get available vehicles at a warehouse.
    """
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
                "fuel_type": v.fuel_type,
            }
            for v in vehicles
        ]
    finally:
        session.close()


# ─── Routing Tools ───────────────────────────────────────────────────────────

def tool_get_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    waypoints: Optional[List[Tuple[float, float]]] = None,
) -> Dict[str, Any]:
    """
    [LIVE API] Query OSRM for a route between two points.
    """
    start = time.time()
    rs = get_routing_service()
    result = rs.get_route(origin_lat, origin_lon, dest_lat, dest_lon, waypoints)
    latency_ms = (time.time() - start) * 1000
    result["tool_latency_ms"] = round(latency_ms, 2)
    return result


def tool_scan_live_disaster_feeds() -> List[Dict[str, Any]]:
    """
    [LIVE API] Poll live natural disasters from USGS & NASA EONET feeds.
    Zero API keys required.
    """
    from services.disaster_service import get_disaster_service
    ds = get_disaster_service()
    regional = ds.get_regional_hazards()
    if not regional:
        # Include top global significant seismic / storm events if none directly in Indian landmass
        regional = ds.fetch_live_nasa_eonet_hazards()[:4] + ds.fetch_live_usgs_earthquakes(min_magnitude=4.8)[:4]
    return regional


def tool_check_route_hazard_collision(
    route_data: Dict[str, Any],
    hazard_zones: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    [COMPUTATION] Check if a route collides with active hazard zones.
    Uses Shapely geometric intersection.
    """
    rs = get_routing_service()
    return rs.check_route_hazard_intersection(route_data, hazard_zones)


def tool_find_safe_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    hazard_zones: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    [LIVE API + COMPUTATION] Self-correcting route finder.
    Queries OSRM, checks hazard intersections, computes detours, re-queries until safe.
    """
    start = time.time()
    rs = get_routing_service()
    result = rs.find_safe_route(
        origin_lat, origin_lon, dest_lat, dest_lon, hazard_zones
    )
    latency_ms = (time.time() - start) * 1000
    result["tool_latency_ms"] = round(latency_ms, 2)
    return result


def tool_scan_live_disaster_feeds() -> List[Dict[str, Any]]:
    """
    [LIVE API] Poll live natural disasters from USGS & NASA EONET feeds.
    Zero API keys required.
    """
    from services.disaster_service import get_disaster_service
    ds = get_disaster_service()
    regional = ds.get_regional_hazards()
    if not regional:
        # Include top global significant seismic / storm events if none directly in Indian landmass
        regional = ds.fetch_live_nasa_eonet_hazards()[:4] + ds.fetch_live_usgs_earthquakes(min_magnitude=4.8)[:4]
    return regional


# ─── Dispatch Tools ──────────────────────────────────────────────────────────

def tool_deduct_inventory(
    warehouse_id: int, items: Dict[str, int]
) -> Dict[str, Any]:
    """
    [DATABASE TRANSACTION] Atomically deduct inventory items from a warehouse.
    Enforces minimum reserve constraints.
    """
    session = get_session()
    try:
        results = []
        all_success = True

        for category, qty in items.items():
            result = deduct_inventory(session, warehouse_id, category, qty)
            results.append(result)
            if not result["success"]:
                all_success = False

        return {
            "warehouse_id": warehouse_id,
            "all_success": all_success,
            "deductions": results,
        }
    finally:
        session.close()


def tool_assign_vehicle(vehicle_id: str) -> Dict[str, Any]:
    """
    [DATABASE TRANSACTION] Assign a vehicle for dispatch.
    """
    session = get_session()
    try:
        return assign_vehicle(session, vehicle_id)
    finally:
        session.close()


def tool_create_dispatch_order(
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
) -> Dict[str, Any]:
    """
    [DATABASE TRANSACTION] Create a final dispatch order with manifest.
    """
    session = get_session()
    try:
        dispatch = create_dispatch(
            session,
            source_warehouse_id=source_warehouse_id,
            destination_name=destination_name,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            items_manifest=items_manifest,
            vehicle_id=vehicle_id,
            route_geometry=route_geometry,
            route_distance_km=route_distance_km,
            route_duration_min=route_duration_min,
            eta_minutes=eta_minutes,
            hazard_avoided=hazard_avoided,
            detour_reason=detour_reason,
            agent_reasoning_log=agent_reasoning_log,
        )
        return {
            "dispatch_id": dispatch.dispatch_id,
            "status": dispatch.status,
            "source_warehouse_id": dispatch.source_warehouse_id,
            "vehicle_id": dispatch.vehicle_id,
            "destination": dispatch.destination_name,
            "total_weight_kg": dispatch.total_weight_kg,
            "route_distance_km": dispatch.route_distance_km,
            "route_duration_min": dispatch.route_duration_min,
            "eta_minutes": dispatch.eta_minutes,
            "hazard_avoided": dispatch.hazard_avoided,
            "items_manifest": dispatch.items_manifest,
            "created_at": dispatch.created_at.isoformat(),
        }
    finally:
        session.close()


def tool_get_active_hazard_zones_from_db() -> List[Dict[str, Any]]:
    """
    [DATABASE] Retrieve all active hazard zones from the database.
    """
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
                "is_active": z.is_active,
            }
            for z in zones
        ]
    finally:
        session.close()

