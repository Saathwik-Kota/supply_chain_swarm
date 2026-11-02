"""
Live OSRM routing service with geometric hazard intersection detection
and autonomous detour calculation.

Provides:
  - Real-time route queries via OSRM public API
  - Route polyline decoding and geometric analysis
  - Hazard zone polygon intersection detection using Shapely
  - Autonomous detour waypoint computation outside hazard radius
  - Self-correction loop with re-query and validation

All routing data comes from LIVE OSRM API calls — zero mock routes.
"""

import os
import sys
import math
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import httpx
import polyline as polyline_codec
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config import (
    OSRM_BASE_URL,
    HAZARD_RADIUS_KM,
    HAZARD_BUFFER_KM,
    ROUTE_COLLISION_TOLERANCE_KM,
    ROUTING_MAX_DETOUR_ATTEMPTS,
    API_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# Earth radius in km for distance calculations
EARTH_RADIUS_KM = 6371.0


class RoutingServiceError(Exception):
    """Raised when routing operations fail after all retries."""
    pass


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in km."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_KM * c


def offset_point(
    lat: float, lon: float, bearing_deg: float, distance_km: float
) -> Tuple[float, float]:
    """
    Calculate a new coordinate given a starting point, bearing, and distance.
    Used for computing detour waypoints outside hazard zones.
    """
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    bearing_r = math.radians(bearing_deg)
    d = distance_km / EARTH_RADIUS_KM

    new_lat = math.asin(
        math.sin(lat_r) * math.cos(d)
        + math.cos(lat_r) * math.sin(d) * math.cos(bearing_r)
    )
    new_lon = lon_r + math.atan2(
        math.sin(bearing_r) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(new_lat),
    )

    return math.degrees(new_lat), math.degrees(new_lon)


def create_hazard_circle(
    center_lat: float, center_lon: float, radius_km: float, num_points: int = 36
) -> Polygon:
    """
    Create a Shapely Polygon approximating a circle around a hazard center.
    Used for route intersection detection.
    """
    # Convert radius to approximate degrees (rough but fast)
    # 1 degree lat ≈ 111 km, 1 degree lon ≈ 111 * cos(lat) km
    points = []
    for i in range(num_points):
        angle = (360.0 / num_points) * i
        plat, plon = offset_point(center_lat, center_lon, angle, radius_km)
        points.append((plon, plat))  # Shapely uses (x=lon, y=lat)

    points.append(points[0])  # Close the polygon
    return Polygon(points)


class RoutingService:
    """
    Live routing engine using OSRM public API with hazard-aware
    detour computation and self-correction.
    """

    def __init__(
        self,
        base_url: str = OSRM_BASE_URL,
        timeout: float = API_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=self.timeout)

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _query_osrm(
        self, coordinates: List[Tuple[float, float]], overview: str = "full"
    ) -> Dict[str, Any]:
        """
        Query the OSRM API for a route between coordinates.
        coordinates: list of (lon, lat) tuples — OSRM uses lon,lat order.
        """
        coord_str = ";".join(f"{lon},{lat}" for lon, lat in coordinates)
        url = f"{self.base_url}/{coord_str}"

        params = {
            "overview": overview,
            "geometries": "polyline",
            "steps": "true",
            "annotations": "true",
        }

        start_time = time.time()
        response = self._client.get(url, params=params)
        latency_ms = (time.time() - start_time) * 1000

        response.raise_for_status()
        data = response.json()

        if data.get("code") != "Ok":
            raise RoutingServiceError(
                f"OSRM API error: {data.get('code')} - {data.get('message', 'Unknown error')}"
            )

        data["_meta"] = {
            "request_url": str(response.url),
            "latency_ms": round(latency_ms, 2),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        return data

    def get_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        waypoints: Optional[List[Tuple[float, float]]] = None,
    ) -> Dict[str, Any]:
        """
        Get a LIVE route from origin to destination, optionally via waypoints.
        waypoints: list of (lat, lon) tuples to route through.

        Returns decoded route geometry, distance, duration, and step-by-step directions.
        """
        # Build coordinate list in OSRM format (lon, lat)
        coords = [(origin_lon, origin_lat)]
        if waypoints:
            coords.extend([(lon, lat) for lat, lon in waypoints])
        coords.append((dest_lon, dest_lat))

        raw = self._query_osrm(coords)
        route = raw["routes"][0]

        # Decode the polyline geometry
        encoded_polyline = route["geometry"]
        decoded_points = polyline_codec.decode(encoded_polyline)

        distance_km = route["distance"] / 1000.0
        duration_min = route["duration"] / 60.0

        # Extract step-by-step instructions
        steps = []
        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                steps.append({
                    "instruction": step.get("maneuver", {}).get("type", ""),
                    "name": step.get("name", ""),
                    "distance_m": step.get("distance", 0),
                    "duration_s": step.get("duration", 0),
                })

        return {
            "origin": {"lat": origin_lat, "lon": origin_lon},
            "destination": {"lat": dest_lat, "lon": dest_lon},
            "waypoints_used": waypoints or [],
            "distance_km": round(distance_km, 2),
            "duration_min": round(duration_min, 2),
            "encoded_polyline": encoded_polyline,
            "decoded_points": decoded_points,  # List of (lat, lon) tuples
            "steps": steps,
            "_meta": raw.get("_meta", {}),
        }

    def check_route_hazard_intersection(
        self,
        route_data: Dict[str, Any],
        hazard_zones: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Check if a route's polyline intersects with any active hazard zones.

        hazard_zones: list of dicts with keys:
          - center_lat, center_lon, radius_km
          - Optional: polygon_coords (list of [lon, lat] pairs)

        Returns intersection analysis with collision points and affected segments.
        """
        decoded = route_data.get("decoded_points", [])
        if not decoded:
            return {
                "intersects": False,
                "reason": "No route geometry to check",
                "collisions": [],
            }

        # Build route LineString (Shapely uses x=lon, y=lat)
        route_line = LineString([(lon, lat) for lat, lon in decoded])

        collisions = []

        for hz in hazard_zones:
            h_lat = hz["center_lat"]
            h_lon = hz["center_lon"]
            h_radius = hz.get("radius_km", HAZARD_RADIUS_KM)

            # Create hazard polygon
            if hz.get("polygon_coords"):
                try:
                    hazard_poly = Polygon(hz["polygon_coords"])
                except Exception:
                    hazard_poly = create_hazard_circle(h_lat, h_lon, h_radius)
            else:
                hazard_poly = create_hazard_circle(h_lat, h_lon, h_radius)

            # Check intersection
            if route_line.intersects(hazard_poly):
                intersection = route_line.intersection(hazard_poly)
                # Find the closest point on the route to the hazard center
                hazard_center = Point(h_lon, h_lat)
                nearest_on_route, _ = nearest_points(route_line, hazard_center)

                collision_dist = haversine_distance(
                    nearest_on_route.y, nearest_on_route.x, h_lat, h_lon
                )

                collisions.append({
                    "hazard_name": hz.get("name", "Unknown Hazard"),
                    "hazard_type": hz.get("hazard_type", "unknown"),
                    "hazard_center": {"lat": h_lat, "lon": h_lon},
                    "hazard_radius_km": h_radius,
                    "closest_route_point": {
                        "lat": nearest_on_route.y,
                        "lon": nearest_on_route.x,
                    },
                    "distance_to_center_km": round(collision_dist, 2),
                    "intersection_length_deg": (
                        intersection.length if hasattr(intersection, "length") else 0
                    ),
                })

        return {
            "intersects": len(collisions) > 0,
            "collision_count": len(collisions),
            "collisions": collisions,
            "route_distance_km": route_data.get("distance_km"),
        }

    def compute_detour_waypoints(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        hazard_zones: List[Dict[str, Any]],
        buffer_km: float = HAZARD_BUFFER_KM,
    ) -> List[Tuple[float, float]]:
        """
        Compute waypoints that route around hazard zones.

        Strategy:
        1. For each hazard zone in the path, compute perpendicular offset points
           outside the hazard radius + buffer.
        2. Choose the side (left/right) that is closer to the direct path.
        3. Return ordered waypoints.
        """
        waypoints = []

        for hz in hazard_zones:
            h_lat = hz["center_lat"]
            h_lon = hz["center_lon"]
            h_radius = hz.get("radius_km", HAZARD_RADIUS_KM) + buffer_km

            # Calculate bearing from origin to destination
            bearing_od = self._bearing(origin_lat, origin_lon, dest_lat, dest_lon)

            # Perpendicular bearings (left and right)
            bearing_left = (bearing_od - 90) % 360
            bearing_right = (bearing_od + 90) % 360

            # Compute candidate detour points
            wp_left = offset_point(h_lat, h_lon, bearing_left, h_radius)
            wp_right = offset_point(h_lat, h_lon, bearing_right, h_radius)

            # Choose the point that results in a shorter total path
            dist_left = (
                haversine_distance(origin_lat, origin_lon, wp_left[0], wp_left[1])
                + haversine_distance(wp_left[0], wp_left[1], dest_lat, dest_lon)
            )
            dist_right = (
                haversine_distance(origin_lat, origin_lon, wp_right[0], wp_right[1])
                + haversine_distance(wp_right[0], wp_right[1], dest_lat, dest_lon)
            )

            chosen = wp_left if dist_left <= dist_right else wp_right
            waypoints.append(chosen)

        return waypoints

    def find_safe_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        hazard_zones: List[Dict[str, Any]],
        max_attempts: int = ROUTING_MAX_DETOUR_ATTEMPTS,
    ) -> Dict[str, Any]:
        """
        Self-correction loop: Find a route that avoids all hazard zones.

        1. Filter out hazard zones that envelop both origin AND destination
           (intra-zone delivery — detour is pointless, truck is already inside)
        2. Query primary route
        3. Check for hazard intersections
        4. If collision detected, compute detour waypoints and re-query
        5. Validate the new route
        6. Repeat up to max_attempts if still colliding

        Returns the final safe route with full reasoning trace.
        """
        reasoning_trace = []
        attempt = 0

        # ── Pre-filter: exempt terminal / origin hazard zones from detour ──
        # Geometrically, if destination is inside a hazard zone (the disaster target),
        # any route MUST terminate inside that zone — detouring outside and re-entering
        # causes absurd multi-hundred-kilometer loops.
        # Similarly, if origin is inside the zone (intra-zone delivery or depot egress),
        # direct transit is required.
        # Only INTERMEDIATE corridor obstacles (between origin and destination) should
        # trigger autonomous detours.
        routable_hazards = []
        skipped_hazards = []
        for hz in hazard_zones:
            h_lat = hz["center_lat"]
            h_lon = hz["center_lon"]
            h_radius = hz.get("radius_km", HAZARD_RADIUS_KM)

            origin_inside = haversine_distance(origin_lat, origin_lon, h_lat, h_lon) <= h_radius
            dest_inside   = haversine_distance(dest_lat, dest_lon, h_lat, h_lon) <= h_radius

            if origin_inside and dest_inside:
                skipped_hazards.append({
                    "hazard": hz,
                    "reason": "intra_zone",
                    "msg": f"Intra-Zone Emergency Transit: Both warehouse and destination are within '{hz.get('name', 'Hazard')}'. Direct delivery route deployed — exterior detours bypassed to minimize latency."
                })
            elif dest_inside:
                skipped_hazards.append({
                    "hazard": hz,
                    "reason": "target_ingress",
                    "msg": f"Disaster Target Ingress: Destination is inside '{hz.get('name', 'Hazard')}'. Direct relief ingress route authorized (cannot detour around the destination itself)."
                })
            elif origin_inside:
                skipped_hazards.append({
                    "hazard": hz,
                    "reason": "depot_egress",
                    "msg": f"Depot Egress: Source warehouse is within '{hz.get('name', 'Hazard')}'. Direct egress authorized to exit disaster perimeter."
                })
            else:
                routable_hazards.append(hz)

        if skipped_hazards:
            for s in skipped_hazards:
                reasoning_trace.append({
                    "step": f"terminal_zone_filter_{s['reason']}",
                    "description": s["msg"],
                    "hazard_name": s["hazard"].get("name", "Unknown"),
                })

        # Use only the routable intermediate hazards for collision checking
        hazard_zones = routable_hazards

        # Step 1: Get primary route
        reasoning_trace.append({
            "step": "primary_route_query",
            "description": f"Querying OSRM for primary route: ({origin_lat},{origin_lon}) → ({dest_lat},{dest_lon})",
        })

        try:
            primary_route = self.get_route(origin_lat, origin_lon, dest_lat, dest_lon)
        except Exception as e:
            reasoning_trace.append({
                "step": "primary_route_error",
                "error": str(e),
            })
            return {
                "success": False,
                "error": f"Failed to get primary route: {e}",
                "reasoning_trace": reasoning_trace,
            }

        reasoning_trace.append({
            "step": "primary_route_received",
            "distance_km": primary_route["distance_km"],
            "duration_min": primary_route["duration_min"],
            "meta": primary_route.get("_meta", {}),
        })

        if not hazard_zones:
            _desc = (
                f"Direct route confirmed ({len(skipped_hazards)} terminal/intra-zone hazard perimeters bypassed for direct emergency transit). Primary route is clear."
                if skipped_hazards
                else "No active hazard zones on transit corridor — primary route is safe."
            )
            reasoning_trace.append({
                "step": "no_hazards",
                "description": _desc,
            })
            return {
                "success": True,
                "route": primary_route,
                "hazard_avoided": False,
                "detour_applied": False,
                "attempts": 1,
                "reasoning_trace": reasoning_trace,
            }

        # Step 2: Check hazard intersection
        collision_check = self.check_route_hazard_intersection(
            primary_route, hazard_zones
        )

        reasoning_trace.append({
            "step": "collision_check",
            "intersects": collision_check["intersects"],
            "collision_count": collision_check["collision_count"],
            "collisions": collision_check["collisions"],
        })

        if not collision_check["intersects"]:
            reasoning_trace.append({
                "step": "route_clear",
                "description": "Primary route does not intersect any hazard zones — safe to proceed.",
            })
            return {
                "success": True,
                "route": primary_route,
                "hazard_avoided": False,
                "detour_applied": False,
                "primary_route_collision": collision_check,
                "attempts": 1,
                "reasoning_trace": reasoning_trace,
            }

        # Step 3: Self-correction loop
        reasoning_trace.append({
            "step": "collision_detected",
            "description": f"Primary route collides with {collision_check['collision_count']} hazard zone(s). Initiating detour computation.",
        })

        current_route = primary_route
        current_waypoints: List[Tuple[float, float]] = []
        colliding_zones = [c for c in collision_check["collisions"]]

        for attempt in range(1, max_attempts + 1):
            reasoning_trace.append({
                "step": f"detour_attempt_{attempt}",
                "description": f"Computing detour waypoints (attempt {attempt}/{max_attempts})",
            })

            # Build hazard zone dicts from collisions for waypoint computation
            active_hazards = [
                {
                    "center_lat": c["hazard_center"]["lat"],
                    "center_lon": c["hazard_center"]["lon"],
                    "radius_km": c["hazard_radius_km"],
                    "name": c.get("hazard_name", ""),
                    "hazard_type": c.get("hazard_type", ""),
                }
                for c in colliding_zones
            ]

            # Increase buffer with each attempt
            buffer = HAZARD_BUFFER_KM * (1 + attempt * 0.5)

            detour_wps = self.compute_detour_waypoints(
                origin_lat, origin_lon,
                dest_lat, dest_lon,
                active_hazards,
                buffer_km=buffer,
            )

            # Merge with any existing waypoints
            all_waypoints = current_waypoints + detour_wps

            reasoning_trace.append({
                "step": f"detour_waypoints_{attempt}",
                "waypoints": [{"lat": w[0], "lon": w[1]} for w in all_waypoints],
                "buffer_km": buffer,
            })

            # Re-query route with waypoints
            try:
                detour_route = self.get_route(
                    origin_lat, origin_lon,
                    dest_lat, dest_lon,
                    waypoints=all_waypoints,
                )
            except Exception as e:
                reasoning_trace.append({
                    "step": f"detour_route_error_{attempt}",
                    "error": str(e),
                })
                continue

            reasoning_trace.append({
                "step": f"detour_route_received_{attempt}",
                "distance_km": detour_route["distance_km"],
                "duration_min": detour_route["duration_min"],
                "distance_increase_km": round(
                    detour_route["distance_km"] - primary_route["distance_km"], 2
                ),
            })

            # Validate the detour route
            recheck = self.check_route_hazard_intersection(
                detour_route, hazard_zones
            )

            reasoning_trace.append({
                "step": f"detour_validation_{attempt}",
                "still_collides": recheck["intersects"],
                "remaining_collisions": recheck["collision_count"],
            })

            if not recheck["intersects"]:
                reasoning_trace.append({
                    "step": "safe_route_found",
                    "description": f"Safe detour route found after {attempt} attempt(s). "
                    f"Distance: {detour_route['distance_km']}km "
                    f"(+{round(detour_route['distance_km'] - primary_route['distance_km'], 2)}km detour).",
                })

                return {
                    "success": True,
                    "route": detour_route,
                    "primary_route": primary_route,
                    "hazard_avoided": True,
                    "detour_applied": True,
                    "detour_waypoints": all_waypoints,
                    "primary_route_collision": collision_check,
                    "distance_increase_km": round(
                        detour_route["distance_km"] - primary_route["distance_km"], 2
                    ),
                    "attempts": attempt + 1,  # +1 for the primary attempt
                    "reasoning_trace": reasoning_trace,
                }

            # Update for next iteration
            current_route = detour_route
            current_waypoints = all_waypoints
            colliding_zones = recheck["collisions"]

        # Exhausted attempts
        reasoning_trace.append({
            "step": "max_attempts_reached",
            "description": f"Could not find a completely safe route after {max_attempts} attempts. "
            "Returning best detour with warnings.",
        })

        return {
            "success": True,
            "route": current_route,
            "primary_route": primary_route,
            "hazard_avoided": True,
            "detour_applied": True,
            "detour_complete": False,
            "warning": "Route may still pass near hazard zones",
            "attempts": max_attempts + 1,
            "reasoning_trace": reasoning_trace,
        }

    @staticmethod
    def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate initial bearing from point 1 to point 2 in degrees."""
        lat1_r = math.radians(lat1)
        lat2_r = math.radians(lat2)
        dlon_r = math.radians(lon2 - lon1)

        x = math.sin(dlon_r) * math.cos(lat2_r)
        y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(
            lat2_r
        ) * math.cos(dlon_r)

        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360


# ─── Module-level convenience instance ────────────────────────────────────────

_routing_service: Optional[RoutingService] = None


def get_routing_service() -> RoutingService:
    """Get or create the singleton RoutingService instance."""
    global _routing_service
    if _routing_service is None:
        _routing_service = RoutingService()
    return _routing_service

