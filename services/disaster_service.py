"""
Live natural hazard and disaster telemetry service.
Integrates live USGS Earthquake API and NASA EONET (Earth Observatory Natural Event Tracker).

Zero API keys required — immediate real-time execution.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import API_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

USGS_ALL_DAY_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
USGS_SIGNIFICANT_MONTH_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson"
NASA_EONET_EVENTS_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"


class DisasterService:
    """Live disaster feed consumer for USGS and NASA EONET."""

    def __init__(self, timeout: float = API_TIMEOUT_SECONDS):
        self.timeout = timeout
        self._client = httpx.Client(timeout=self.timeout)

    def close(self):
        self._client.close()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    def fetch_live_usgs_earthquakes(self, min_magnitude: float = 4.0) -> List[Dict[str, Any]]:
        """
        Fetch LIVE earthquakes from USGS GeoJSON feed.
        Filters for significant seismic events.
        """
        try:
            resp = self._client.get(USGS_ALL_DAY_URL)
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])

            events = []
            for feat in features:
                props = feat.get("properties", {})
                geom = feat.get("geometry", {})
                coords = geom.get("coordinates", [])

                mag = props.get("mag") or 0.0
                if mag >= min_magnitude and len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    events.append({
                        "id": feat.get("id"),
                        "title": props.get("title", "Earthquake"),
                        "hazard_type": "earthquake",
                        "severity": "critical" if mag >= 6.0 else "high",
                        "magnitude": mag,
                        "lat": lat,
                        "lon": lon,
                        "depth_km": coords[2] if len(coords) > 2 else 10.0,
                        "place": props.get("place", "Unknown"),
                        "time": props.get("time"),
                        "source": "USGS Live Seismic Feed",
                        "radius_km": max(30.0, mag * 12.0),
                    })
            return events
        except Exception as e:
            logger.warning(f"USGS live earthquake fetch error: {e}")
            return []

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    def fetch_live_nasa_eonet_hazards(self) -> List[Dict[str, Any]]:
        """
        Fetch LIVE active natural hazards from NASA EONET v3.
        Includes severe storms, wildfires, cyclones, and volcanoes.
        """
        try:
            params = {"status": "open", "limit": 25}
            resp = self._client.get(NASA_EONET_EVENTS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            events_raw = data.get("events", [])

            hazards = []
            for ev in events_raw:
                categories = [c.get("title", "") for c in ev.get("categories", [])]
                cat_title = categories[0] if categories else "Severe Weather"
                geoms = ev.get("geometry", [])
                if not geoms:
                    continue

                latest_geom = geoms[-1]
                coords = latest_geom.get("coordinates", [])

                # NASA EONET points are [lon, lat]
                if isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
                    lon, lat = coords[0], coords[1]
                    hazards.append({
                        "id": ev.get("id"),
                        "title": ev.get("title"),
                        "hazard_type": cat_title.lower().replace(" ", "_"),
                        "category": cat_title,
                        "severity": "high",
                        "lat": lat,
                        "lon": lon,
                        "date": latest_geom.get("date"),
                        "source": "NASA EONET v3 Live Feed",
                        "radius_km": 60.0,
                    })
            return hazards
        except Exception as e:
            logger.warning(f"NASA EONET live fetch error: {e}")
            return []

    def get_regional_hazards(
        self,
        min_lat: float = 6.0,
        max_lat: float = 38.0,
        min_lon: float = 68.0,
        max_lon: float = 98.0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch live disasters from USGS & NASA EONET filtered to the Indian subcontinent / Indian Ocean region.
        """
        eqs = self.fetch_live_usgs_earthquakes(min_magnitude=3.5)
        nasa_events = self.fetch_live_nasa_eonet_hazards()

        all_events = eqs + nasa_events
        regional = []
        for ev in all_events:
            lat = ev.get("lat")
            lon = ev.get("lon")
            if lat and lon and (min_lat <= lat <= max_lat) and (min_lon <= lon <= max_lon):
                regional.append(ev)

        return regional


_disaster_service: Optional[DisasterService] = None


def get_disaster_service() -> DisasterService:
    global _disaster_service
    if _disaster_service is None:
        _disaster_service = DisasterService()
    return _disaster_service

