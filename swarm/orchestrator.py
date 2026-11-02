"""
Multi-agent orchestration engine with self-correction & fallback loops.

Implements a state-graph execution model where 4 agents execute sequentially,
each reading from and writing to the shared SwarmState. Includes:
  - Automatic retry with fallback on API failures
  - Self-correction loop in routing (detour computation)
  - Full reasoning trace logging for UI display
  - Graceful degradation when individual agents encounter errors
"""

import os
import sys
import json
import time
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import HAZARD_RADIUS_KM, AGENT_NAMES, MAX_RETRY_ATTEMPTS
from config import (
    VEHICLE_TYPES,
    FUEL_CONSUMPTION_L_PER_100KM,
    CO2_KG_PER_LITER,
    FUEL_COST_INR_PER_LITER,
)
from swarm.state import SwarmState, create_initial_state, AgentLog
from swarm.tools import (
    tool_scan_weather_corridors,
    tool_scan_custom_location,
    tool_scan_live_disaster_feeds,
    tool_register_hazard_zone,
    tool_find_nearest_warehouses,
    tool_check_warehouse_inventory,
    tool_get_warehouse_full_inventory,
    tool_get_available_fleet,
    tool_find_safe_route,
    tool_deduct_inventory,
    tool_assign_vehicle,
    tool_create_dispatch_order,
    tool_get_active_hazard_zones_from_db,
)

logger = logging.getLogger(__name__)


def _log(state: SwarmState, agent: str, step: str, message: str,
         data: Optional[Dict] = None, error: Optional[str] = None,
         latency_ms: Optional[float] = None) -> None:
    """Append a structured log entry to the swarm state."""
    entry = AgentLog(
        agent=agent,
        step=step,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message=message,
        data=data,
        error=error,
        latency_ms=latency_ms,
    )
    state["agent_logs"].append(entry)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 1: Telemetry & Threat Monitor
# ═══════════════════════════════════════════════════════════════════════════════

def run_telemetry_agent(state: SwarmState) -> SwarmState:
    """
    Agent 1: Scans live weather across all logistics corridors.
    Detects hazard zones and registers them in the database.
    """
    agent = AGENT_NAMES["telemetry"]
    state["current_agent"] = agent
    _log(state, agent, "start", "Starting live weather scan across all logistics corridors")

    start = time.time()

    try:
        # Step 1: Scan all corridors with live Open-Meteo API
        _log(state, agent, "api_call", "Querying Open-Meteo API for all corridor hubs...")

        custom_thresholds = state.get("custom_thresholds")
        scan_result = tool_scan_weather_corridors(custom_thresholds)

        latency = round((time.time() - start) * 1000, 2)
        _log(
            state, agent, "scan_complete",
            f"Scanned {scan_result['total_hubs_scanned']} hubs. "
            f"Found {scan_result['hazardous_hub_count']} hazardous locations. "
            f"Errors: {scan_result['total_errors']}",
            data={
                "hubs_scanned": scan_result["total_hubs_scanned"],
                "hazardous_count": scan_result["hazardous_hub_count"],
                "errors": scan_result["total_errors"],
            },
            latency_ms=latency,
        )

        state["weather_scan"] = scan_result

        # Step 2: Extract and register hazard zones
        hazards = []
        hazard_zones_db = []

        for hub_data in scan_result.get("hazardous_hubs", []):
            assessment = hub_data.get("assessment", {})
            weather = hub_data.get("weather", {})

            hazard_info = {
                "hub_name": hub_data["hub_name"],
                "lat": hub_data["lat"],
                "lon": hub_data["lon"],
                "hazard_type": assessment.get("primary_hazard_type", "unknown"),
                "severity": assessment.get("severity", "high"),
                "weather_data": weather,
                "assessment": assessment,
                "radius_km": HAZARD_RADIUS_KM,
            }

            _log(
                state, agent, "hazard_detected",
                f"⚠️ HAZARD at {hub_data['hub_name']}: "
                f"{assessment.get('primary_hazard_type', 'unknown')} "
                f"(severity: {assessment.get('severity', 'unknown')})",
                data=hazard_info,
            )

            # Register in database
            try:
                zone_result = tool_register_hazard_zone(
                    name=f"Hazard near {hub_data['hub_name']}",
                    hazard_type=assessment.get("primary_hazard_type", "unknown"),
                    severity=assessment.get("severity", "high"),
                    center_lat=hub_data["lat"],
                    center_lon=hub_data["lon"],
                    radius_km=HAZARD_RADIUS_KM,
                )
                hazard_info["hazard_zone_id"] = zone_result["hazard_zone_id"]
                hazard_zones_db.append(zone_result)

                _log(
                    state, agent, "hazard_registered",
                    f"Hazard zone registered in DB: ID={zone_result['hazard_zone_id']}",
                    data=zone_result,
                )
            except Exception as e:
                _log(state, agent, "hazard_register_error",
                     f"Failed to register hazard zone: {e}", error=str(e))

            hazards.append(hazard_info)

        state["hazards"] = hazards
        state["hazard_zones_db"] = hazard_zones_db

        # Log scan errors
        for err in scan_result.get("errors", []):
            _log(state, agent, "scan_error",
                 f"Weather scan failed for {err['hub_name']}: {err['error']}",
                 error=err["error"])
            state["errors"].append({
                "agent": agent,
                "error": err["error"],
                "hub": err["hub_name"],
            })

        # Step 3: Ingest live NASA EONET and USGS live disaster feeds
        try:
            _log(state, agent, "disaster_feed_poll", "Polling live USGS Seismic & NASA EONET natural disaster feeds...")
            disaster_events = tool_scan_live_disaster_feeds()
            _log(state, agent, "disaster_events_found", f"Retrieved {len(disaster_events)} live global & regional disaster events", data={"events": disaster_events[:5]})

            for ev in disaster_events:
                ev_lat = ev.get("lat")
                ev_lon = ev.get("lon")
                if ev_lat is not None and ev_lon is not None:
                    ev_title = ev.get("title", "Natural Hazard")
                    ev_type = ev.get("hazard_type", "disaster")
                    ev_radius = ev.get("radius_km", HAZARD_RADIUS_KM)
                    ev_source = ev.get("source", "USGS/NASA EONET")

                    zone_res = tool_register_hazard_zone(
                        name=f"{ev_title} ({ev_source})",
                        hazard_type=ev_type,
                        severity=ev.get("severity", "high"),
                        center_lat=ev_lat,
                        center_lon=ev_lon,
                        radius_km=ev_radius,
                        source=ev_source,
                    )
                    hazard_info = {
                        "hub_name": ev_title,
                        "lat": ev_lat,
                        "lon": ev_lon,
                        "hazard_type": ev_type,
                        "severity": ev.get("severity", "high"),
                        "radius_km": ev_radius,
                        "source": ev_source,
                        "hazard_zone_id": zone_res.get("hazard_zone_id"),
                    }
                    hazards.append(hazard_info)
                    hazard_zones_db.append(zone_res)
        except Exception as e:
            _log(state, agent, "disaster_feed_error", f"Disaster feed ingestion: {e}", error=str(e))

        state["hazards"] = hazards
        state["hazard_zones_db"] = hazard_zones_db

        _log(
            state, agent, "complete",
            f"Telemetry scan complete. {len(hazards)} hazard(s) detected and registered (Open-Meteo + NASA EONET/USGS).",
            latency_ms=round((time.time() - start) * 1000, 2),
        )

    except Exception as e:
        error_msg = f"Telemetry agent failed: {str(e)}"
        _log(state, agent, "fatal_error", error_msg, error=traceback.format_exc())
        state["errors"].append({"agent": agent, "error": error_msg})
        state["hazards"] = []

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 2: Regional Inventory & Supplier
# ═══════════════════════════════════════════════════════════════════════════════

def run_inventory_agent(state: SwarmState) -> SwarmState:
    """
    Agent 2: Finds nearest warehouses and checks inventory availability.
    Selects the best warehouse that can fulfill the request.
    """
    agent = AGENT_NAMES["inventory"]
    state["current_agent"] = agent
    _log(state, agent, "start", "Starting inventory check for requested supplies")

    start = time.time()
    dest_lat = state["destination_lat"]
    dest_lon = state["destination_lon"]
    requested = state["requested_items"]

    try:
        # Step 1: Find nearest warehouses
        _log(
            state, agent, "finding_warehouses",
            f"Searching for nearest warehouses to destination ({dest_lat}, {dest_lon})",
        )

        nearest = tool_find_nearest_warehouses(dest_lat, dest_lon, limit=7)

        _log(
            state, agent, "warehouses_found",
            f"Found {len(nearest)} candidate warehouses",
            data={"warehouses": nearest},
        )

        # Step 2: Check inventory at each warehouse
        inventory_checks = []
        selected = None

        for wh_info in nearest:
            wh_id = wh_info["warehouse_id"]

            _log(
                state, agent, "checking_inventory",
                f"Checking inventory at {wh_info['name']} (ID: {wh_id}, {wh_info['distance_km']}km away)",
            )

            check_result = tool_check_warehouse_inventory(wh_id, requested)

            # Get full inventory for display
            full_inv = tool_get_warehouse_full_inventory(wh_id)

            # Get available fleet
            fleet = tool_get_available_fleet(wh_id, check_result["total_weight_kg"])

            inv_check = {
                "warehouse_id": wh_id,
                "warehouse_name": wh_info["name"],
                "city": wh_info["city"],
                "lat": wh_info["lat"],
                "lon": wh_info["lon"],
                "distance_km": wh_info["distance_km"],
                "available_items": check_result["items"],
                "total_weight_kg": check_result["total_weight_kg"],
                "can_fulfill": check_result["can_fulfill_all"],
                "full_inventory": full_inv,
                "available_vehicles": fleet,
            }

            inventory_checks.append(inv_check)

            if check_result["can_fulfill_all"] and selected is None:
                selected = inv_check
                _log(
                    state, agent, "warehouse_selected",
                    f"✅ Selected {wh_info['name']} — can fulfill all items. "
                    f"Total weight: {check_result['total_weight_kg']}kg, "
                    f"Distance: {wh_info['distance_km']}km",
                    data=inv_check,
                )

        state["inventory_checks"] = inventory_checks
        state["selected_warehouse"] = selected

        if not selected:
            # Fallback: select the closest warehouse even if partial fulfillment
            _log(
                state, agent, "no_full_fulfillment",
                "⚠️ No single warehouse can fully fulfill the request. "
                "Selecting closest warehouse for partial fulfillment.",
            )
            if inventory_checks:
                selected = inventory_checks[0]
                state["selected_warehouse"] = selected
                _log(
                    state, agent, "fallback_selected",
                    f"Fallback: {selected['warehouse_name']} selected (partial fulfillment)",
                )

        _log(
            state, agent, "complete",
            f"Inventory check complete. Checked {len(inventory_checks)} warehouses.",
            latency_ms=round((time.time() - start) * 1000, 2),
        )

    except Exception as e:
        error_msg = f"Inventory agent failed: {str(e)}"
        _log(state, agent, "fatal_error", error_msg, error=traceback.format_exc())
        state["errors"].append({"agent": agent, "error": error_msg})

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 3: Dynamic Routing & Avoidance
# ═══════════════════════════════════════════════════════════════════════════════

def run_routing_agent(state: SwarmState) -> SwarmState:
    """
    Agent 3: Computes safe route from selected warehouse to destination.
    Self-correction loop: checks hazard intersection, computes detours, re-queries.
    """
    agent = AGENT_NAMES["routing"]
    state["current_agent"] = agent
    _log(state, agent, "start", "Starting dynamic route computation")

    start = time.time()
    selected = state.get("selected_warehouse")

    if not selected:
        _log(state, agent, "no_warehouse", "❌ No warehouse selected — cannot compute route")
        state["errors"].append({"agent": agent, "error": "No warehouse selected"})
        return state

    origin_lat = selected["lat"]
    origin_lon = selected["lon"]
    dest_lat = state["destination_lat"]
    dest_lon = state["destination_lon"]

    try:
        # Build hazard zone list for collision checking
        hazard_zones = []
        for h in state.get("hazards", []):
            hazard_zones.append({
                "name": f"Hazard near {h['hub_name']}",
                "hazard_type": h.get("hazard_type", "unknown"),
                "center_lat": h["lat"],
                "center_lon": h["lon"],
                "radius_km": h.get("radius_km", HAZARD_RADIUS_KM),
            })

        # Also include any hazard zones from DB (may include zones from previous runs)
        db_zones = tool_get_active_hazard_zones_from_db()
        for z in db_zones:
            # Avoid duplicates
            already = any(
                abs(hz["center_lat"] - z["center_lat"]) < 0.01
                and abs(hz["center_lon"] - z["center_lon"]) < 0.01
                for hz in hazard_zones
            )
            if not already:
                hazard_zones.append(z)

        _log(
            state, agent, "routing_request",
            f"Computing route: {selected['warehouse_name']} ({origin_lat},{origin_lon}) "
            f"→ {state['destination_name']} ({dest_lat},{dest_lon}). "
            f"Active hazard zones: {len(hazard_zones)}",
            data={"hazard_zones": hazard_zones},
        )

        # Use the self-correcting route finder
        route_result = tool_find_safe_route(
            origin_lat, origin_lon, dest_lat, dest_lon, hazard_zones
        )

        # Store reasoning trace
        state["routing_reasoning_trace"] = route_result.get("reasoning_trace", [])

        if route_result.get("success"):
            route = route_result["route"]
            hazard_avoided = route_result.get("hazard_avoided", False)
            detour = route_result.get("detour_applied", False)

            state["route_result"] = {
                "origin": route.get("origin"),
                "destination": route.get("destination"),
                "distance_km": route.get("distance_km"),
                "duration_min": route.get("duration_min"),
                "encoded_polyline": route.get("encoded_polyline"),
                "decoded_points": route.get("decoded_points", []),
                "hazard_avoided": hazard_avoided,
                "detour_applied": detour,
                "detour_waypoints": route_result.get("detour_waypoints", []),
                "primary_route_distance_km": (
                    route_result.get("primary_route", {}).get("distance_km")
                    if detour else None
                ),
                "distance_increase_km": route_result.get("distance_increase_km"),
                "collision_data": route_result.get("primary_route_collision"),
                "attempts": route_result.get("attempts", 1),
            }

            state["primary_route_blocked"] = hazard_avoided

            if detour:
                _log(
                    state, agent, "detour_applied",
                    f"🔄 ROUTE DETOUR APPLIED! Primary route blocked by hazard. "
                    f"Detour distance: {route['distance_km']}km "
                    f"(+{route_result.get('distance_increase_km', 0)}km). "
                    f"Attempts: {route_result.get('attempts', 1)}",
                    data=state["route_result"],
                )
            else:
                _log(
                    state, agent, "route_clear",
                    f"✅ Route is CLEAR. Distance: {route['distance_km']}km, "
                    f"Duration: {route['duration_min']} min",
                    data=state["route_result"],
                )
        else:
            error_msg = route_result.get("error", "Unknown routing error")
            _log(state, agent, "routing_failed", f"❌ Routing failed: {error_msg}",
                 error=error_msg)
            state["errors"].append({"agent": agent, "error": error_msg})

        _log(
            state, agent, "complete",
            "Route computation complete.",
            latency_ms=round((time.time() - start) * 1000, 2),
        )

    except Exception as e:
        error_msg = f"Routing agent failed: {str(e)}"
        _log(state, agent, "fatal_error", error_msg, error=traceback.format_exc())
        state["errors"].append({"agent": agent, "error": error_msg})

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Carbon & Fuel Impact Calculator
# ═══════════════════════════════════════════════════════════════════════════════

def compute_carbon_metrics(
    distance_km: float,
    vehicle_type: str,
    primary_distance_km: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute fuel consumption, CO₂ emissions, and cost for a given route.
    If primary_distance_km is given (detour case), also computes the delta.
    """
    fuel_l_per_100 = FUEL_CONSUMPTION_L_PER_100KM.get(vehicle_type, 25.0)
    vtype_config = VEHICLE_TYPES.get(vehicle_type, {})
    fuel_type = vtype_config.get("fuel_type", "diesel")
    co2_per_liter = CO2_KG_PER_LITER.get(fuel_type, 2.68)
    cost_per_liter = FUEL_COST_INR_PER_LITER.get(fuel_type, 89.62)

    # Total route metrics
    fuel_liters = (distance_km / 100.0) * fuel_l_per_100
    co2_kg = fuel_liters * co2_per_liter
    fuel_cost_inr = fuel_liters * cost_per_liter

    result: Dict[str, Any] = {
        "route_distance_km": round(distance_km, 2),
        "vehicle_type": vehicle_type,
        "fuel_type": fuel_type,
        "fuel_consumption_liters": round(fuel_liters, 2),
        "co2_emissions_kg": round(co2_kg, 2),
        "fuel_cost_inr": round(fuel_cost_inr, 2),
    }

    # Detour delta (extra cost of safety)
    if primary_distance_km is not None and primary_distance_km > 0:
        detour_extra_km = max(0, distance_km - primary_distance_km)
        primary_fuel = (primary_distance_km / 100.0) * fuel_l_per_100
        primary_co2 = primary_fuel * co2_per_liter
        primary_cost = primary_fuel * cost_per_liter

        extra_fuel = fuel_liters - primary_fuel
        extra_co2 = co2_kg - primary_co2
        extra_cost = fuel_cost_inr - primary_cost

        result["primary_route_km"] = round(primary_distance_km, 2)
        result["detour_extra_km"] = round(detour_extra_km, 2)
        result["detour_extra_fuel_liters"] = round(extra_fuel, 2)
        result["detour_extra_co2_kg"] = round(extra_co2, 2)
        result["detour_extra_cost_inr"] = round(extra_cost, 2)

        # Safety benefit ratio — how many km of detour per hazard avoided
        result["safety_tradeoff"] = (
            f"+{round(detour_extra_km, 1)}km / +{round(extra_fuel, 1)}L diesel / "
            f"+{round(extra_co2, 1)}kg CO₂ for hazard avoidance"
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 4: Autonomous Dispatch & Execution Coordinator
# ═══════════════════════════════════════════════════════════════════════════════

def run_dispatch_agent(state: SwarmState) -> SwarmState:
    """
    Agent 4: Executes the end-to-end relief order.
    Deducts inventory, assigns fleet vehicles, creates dispatch manifest.
    """
    agent = AGENT_NAMES["dispatch"]
    state["current_agent"] = agent
    _log(state, agent, "start", "Starting autonomous dispatch execution")

    start = time.time()
    selected = state.get("selected_warehouse")
    route = state.get("route_result")

    if not selected:
        _log(state, agent, "no_warehouse", "❌ No warehouse selected — cannot dispatch")
        state["errors"].append({"agent": agent, "error": "No warehouse selected"})
        state["swarm_status"] = "failed"
        return state

    try:
        # Step 1: Deduct inventory
        _log(state, agent, "deducting_inventory",
             f"Deducting inventory from {selected['warehouse_name']}")

        # Only deduct items that are available
        items_to_deduct = {}
        for cat, qty in state["requested_items"].items():
            item_check = selected.get("available_items", {}).get(cat, {})
            if item_check.get("available", False):
                items_to_deduct[cat] = qty
            else:
                avail = item_check.get("available_above_reserve", 0)
                if avail > 0:
                    items_to_deduct[cat] = min(qty, avail)
                    _log(state, agent, "partial_deduction",
                         f"⚠️ Only {avail} of {qty} {cat} available. Dispatching {min(qty, avail)}.")

        deduction_result = tool_deduct_inventory(selected["warehouse_id"], items_to_deduct)
        state["inventory_deductions"] = deduction_result.get("deductions", [])

        if deduction_result["all_success"]:
            _log(state, agent, "inventory_deducted",
                 f"✅ All inventory successfully deducted",
                 data=deduction_result)
        else:
            _log(state, agent, "deduction_partial",
                 "⚠️ Some inventory deductions failed",
                 data=deduction_result)

        # Step 2: Assign vehicle
        total_weight = sum(
            d.get("weight_deducted_kg", 0) for d in deduction_result.get("deductions", [])
            if d.get("success")
        )

        _log(state, agent, "assigning_vehicle",
             f"Finding vehicle with capacity ≥ {total_weight}kg")

        vehicles = tool_get_available_fleet(selected["warehouse_id"], total_weight)
        vehicle_assigned = None

        if vehicles:
            best_vehicle = vehicles[0]  # Sorted by capacity desc
            assign_result = tool_assign_vehicle(best_vehicle["vehicle_id"])

            if assign_result.get("success"):
                vehicle_assigned = assign_result
                state["vehicle_assignment"] = assign_result
                _log(state, agent, "vehicle_assigned",
                     f"✅ Vehicle {best_vehicle['vehicle_id']} ({best_vehicle['display_name']}) assigned",
                     data=assign_result)
            else:
                _log(state, agent, "vehicle_assign_failed",
                     f"⚠️ Vehicle assignment failed: {assign_result.get('error')}",
                     error=assign_result.get("error"))
        else:
            _log(state, agent, "no_vehicles",
                 "⚠️ No vehicles with sufficient capacity available")

        # Step 3: Create dispatch order
        _log(state, agent, "creating_dispatch", "Creating dispatch order with manifest")

        # Build reasoning log
        reasoning_summary = json.dumps(
            [
                {"agent": log["agent"], "step": log["step"], "message": log["message"]}
                for log in state["agent_logs"]
            ],
            indent=2,
        )

        # Determine detour reason
        detour_reason = None
        if route and route.get("hazard_avoided"):
            detour_reason = (
                f"Primary route blocked by {len(state.get('hazards', []))} hazard zone(s). "
                f"Detour adds {route.get('distance_increase_km', 0)}km. "
                f"Self-correction attempts: {route.get('attempts', 1)}"
            )

        dispatch_result = tool_create_dispatch_order(
            source_warehouse_id=selected["warehouse_id"],
            destination_name=state["destination_name"],
            destination_lat=state["destination_lat"],
            destination_lon=state["destination_lon"],
            items_manifest=items_to_deduct,
            vehicle_id=vehicle_assigned["vehicle_id"] if vehicle_assigned else None,
            route_geometry=route.get("encoded_polyline") if route else None,
            route_distance_km=route.get("distance_km") if route else None,
            route_duration_min=route.get("duration_min") if route else None,
            eta_minutes=route.get("duration_min") if route else None,
            hazard_avoided=route.get("hazard_avoided", False) if route else False,
            detour_reason=detour_reason,
            agent_reasoning_log=reasoning_summary,
        )

        state["dispatch_manifest"] = {
            "dispatch_id": dispatch_result["dispatch_id"],
            "source_warehouse_id": dispatch_result["source_warehouse_id"],
            "source_warehouse_name": selected["warehouse_name"],
            "vehicle_id": dispatch_result.get("vehicle_id"),
            "vehicle_type": vehicle_assigned.get("type") if vehicle_assigned else None,
            "destination_name": dispatch_result["destination"],
            "destination_lat": state["destination_lat"],
            "destination_lon": state["destination_lon"],
            "items": dispatch_result["items_manifest"],
            "total_weight_kg": dispatch_result["total_weight_kg"],
            "route_distance_km": dispatch_result.get("route_distance_km"),
            "route_duration_min": dispatch_result.get("route_duration_min"),
            "eta_minutes": dispatch_result.get("eta_minutes"),
            "hazard_avoided": dispatch_result.get("hazard_avoided", False),
            "detour_reason": detour_reason,
            "status": dispatch_result["status"],
        }

        # ── Step 4: Compute Carbon & Fuel Consumption Metrics ────────────
        _v_type = vehicle_assigned.get("type", "medium_truck") if vehicle_assigned else "medium_truck"
        _route_dist = route.get("distance_km") if route else None
        _primary_dist = route.get("primary_route_distance_km") if route else None

        if _route_dist and _route_dist > 0:
            carbon_metrics = compute_carbon_metrics(
                distance_km=_route_dist,
                vehicle_type=_v_type,
                primary_distance_km=_primary_dist,
            )
            state["carbon_metrics"] = carbon_metrics
            state["dispatch_manifest"]["carbon_metrics"] = carbon_metrics

            _co2 = carbon_metrics["co2_emissions_kg"]
            _fuel = carbon_metrics["fuel_consumption_liters"]
            _cost = carbon_metrics["fuel_cost_inr"]

            _carbon_msg = (
                f"🌿 Carbon Impact: {_fuel}L {carbon_metrics['fuel_type']} → "
                f"{_co2}kg CO₂ | Fuel cost: ₹{_cost}"
            )
            if carbon_metrics.get("detour_extra_km"):
                _carbon_msg += (
                    f" | Detour overhead: +{carbon_metrics['detour_extra_fuel_liters']}L / "
                    f"+{carbon_metrics['detour_extra_co2_kg']}kg CO₂ / "
                    f"+₹{carbon_metrics['detour_extra_cost_inr']}"
                )
            _log(state, agent, "carbon_impact", _carbon_msg, data=carbon_metrics)
        else:
            state["carbon_metrics"] = None

        _log(
            state, agent, "dispatch_created",
            f"✅ DISPATCH ORDER CREATED: {dispatch_result['dispatch_id']}. "
            f"Weight: {dispatch_result['total_weight_kg']}kg, "
            f"ETA: {dispatch_result.get('eta_minutes', 'N/A')} min",
            data=dispatch_result,
        )

        state["swarm_status"] = "completed"

        _log(
            state, agent, "complete",
            "🎉 Autonomous dispatch execution complete!",
            latency_ms=round((time.time() - start) * 1000, 2),
        )

    except Exception as e:
        error_msg = f"Dispatch agent failed: {str(e)}"
        _log(state, agent, "fatal_error", error_msg, error=traceback.format_exc())
        state["errors"].append({"agent": agent, "error": error_msg})
        state["swarm_status"] = "failed"

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator: Main Execution Loop
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_PIPELINE = [
    ("telemetry", run_telemetry_agent),
    ("inventory", run_inventory_agent),
    ("routing", run_routing_agent),
    ("dispatch", run_dispatch_agent),
]


def run_swarm(
    destination_name: str,
    destination_lat: float,
    destination_lon: float,
    requested_items: Dict[str, int],
    custom_thresholds: Optional[Dict[str, float]] = None,
    progress_callback: Optional[Callable[[str, str, float], None]] = None,
) -> SwarmState:
    """
    Execute the full 4-agent swarm pipeline.

    Args:
        destination_name: Human-readable destination name
        destination_lat: Destination latitude
        destination_lon: Destination longitude
        requested_items: Dict of category → quantity
        custom_thresholds: Optional weather threshold overrides
        progress_callback: Optional callback(agent_name, status, progress_pct)

    Returns:
        Final SwarmState with all results and reasoning traces
    """
    # Initialize state
    state = create_initial_state(
        destination_name=destination_name,
        destination_lat=destination_lat,
        destination_lon=destination_lon,
        requested_items=requested_items,
        custom_thresholds=custom_thresholds,
    )

    total_start = time.time()
    total_agents = len(AGENT_PIPELINE)

    logger.info(f"Starting swarm execution: destination={destination_name}")

    for idx, (agent_key, agent_fn) in enumerate(AGENT_PIPELINE):
        agent_name = AGENT_NAMES[agent_key]
        progress = (idx / total_agents) * 100

        if progress_callback:
            progress_callback(agent_name, "running", progress)

        logger.info(f"Executing agent {idx + 1}/{total_agents}: {agent_name}")

        try:
            state = agent_fn(state)
        except Exception as e:
            error_msg = f"Agent {agent_name} crashed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            _log(state, agent_name, "crash", error_msg, error=traceback.format_exc())
            state["errors"].append({"agent": agent_name, "error": error_msg})

            # Continue to next agent for graceful degradation
            if agent_key == "telemetry":
                state["hazards"] = []
            elif agent_key == "inventory":
                state["swarm_status"] = "failed"
                break

        if progress_callback:
            progress_callback(agent_name, "completed", ((idx + 1) / total_agents) * 100)

    # Finalize
    total_latency = (time.time() - total_start) * 1000
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    state["total_latency_ms"] = round(total_latency, 2)
    state["current_agent"] = ""

    if state["swarm_status"] == "running":
        state["swarm_status"] = "completed" if not state["errors"] else "partial"

    logger.info(
        f"Swarm execution complete: status={state['swarm_status']}, "
        f"latency={total_latency:.0f}ms, errors={len(state['errors'])}"
    )

    return state

