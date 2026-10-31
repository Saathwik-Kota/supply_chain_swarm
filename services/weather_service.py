"""
Live weather telemetry service using Open-Meteo API.

Provides:
  - Real-time current weather at any coordinate
  - Hourly forecast polling for logistics corridors
  - Hazard detection based on configurable thresholds
  - Automatic retry with exponential backoff
  - Full response logging for agent reasoning traces

All data comes from LIVE API calls — zero hardcoded/mock data.
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

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config import (
    OPEN_METEO_BASE_URL,
    OPEN_METEO_CURRENT_PARAMS,
    OPEN_METEO_HOURLY_PARAMS,
    LOGISTICS_HUBS,
    DEFAULT_WEATHER_THRESHOLDS,
    WMO_WEATHER_CODES,
    HAZARD_RADIUS_KM,
    API_TIMEOUT_SECONDS,
    HubCoordinate,
    WeatherThresholds,
)

logger = logging.getLogger(__name__)


class WeatherServiceError(Exception):
    """Raised when weather API calls fail after all retries."""
    pass


class WeatherService:
    """
    Live weather data client for Open-Meteo API.
    All methods make real HTTP requests — no cached or mock data.
    """

    def __init__(
        self,
        base_url: str = OPEN_METEO_BASE_URL,
        timeout: float = API_TIMEOUT_SECONDS,
        thresholds: Optional[WeatherThresholds] = None,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.thresholds = thresholds or DEFAULT_WEATHER_THRESHOLDS
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
    def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a GET request to Open-Meteo with retry logic."""
        start_time = time.time()
        response = self._client.get(self.base_url, params=params)
        latency_ms = (time.time() - start_time) * 1000

        response.raise_for_status()
        data = response.json()

        if "error" in data and data.get("error"):
            raise WeatherServiceError(
                f"Open-Meteo API error: {data.get('reason', 'Unknown error')}"
            )

        data["_meta"] = {
            "request_url": str(response.url),
            "latency_ms": round(latency_ms, 2),
            "status_code": response.status_code,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        return data

    def get_current_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        """
        Fetch LIVE current weather conditions at a specific coordinate.
        Returns temperature, humidity, precipitation, wind, weather code, etc.
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": ",".join(OPEN_METEO_CURRENT_PARAMS),
            "timezone": "auto",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }

        raw = self._get(params)
        current = raw.get("current", {})

        weather_code = current.get("weather_code", 0)
        weather_desc = WMO_WEATHER_CODES.get(weather_code, "Unknown")

        result = {
            "latitude": raw.get("latitude", lat),
            "longitude": raw.get("longitude", lon),
            "temperature_c": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation", 0),
            "rain_mm": current.get("rain", 0),
            "weather_code": weather_code,
            "weather_description": weather_desc,
            "wind_speed_kmh": current.get("wind_speed_10m", 0),
            "wind_gusts_kmh": current.get("wind_gusts_10m", 0),
            "observation_time": current.get("time"),
            "_meta": raw.get("_meta", {}),
        }

        return result

    def get_hourly_forecast(
        self, lat: float, lon: float, forecast_hours: int = 24
    ) -> Dict[str, Any]:
        """
        Fetch LIVE hourly weather forecast at a specific coordinate.
        Returns arrays of hourly values for the next N hours.
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": ",".join(OPEN_METEO_HOURLY_PARAMS),
            "timezone": "auto",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
            "forecast_hours": forecast_hours,
        }

        raw = self._get(params)
        hourly = raw.get("hourly", {})

        return {
            "latitude": raw.get("latitude", lat),
            "longitude": raw.get("longitude", lon),
            "hours": hourly.get("time", []),
            "temperature_c": hourly.get("temperature_2m", []),
            "humidity_pct": hourly.get("relative_humidity_2m", []),
            "precipitation_mm": hourly.get("precipitation", []),
            "rain_mm": hourly.get("rain", []),
            "weather_code": hourly.get("weather_code", []),
            "wind_speed_kmh": hourly.get("wind_speed_10m", []),
            "wind_gusts_kmh": hourly.get("wind_gusts_10m", []),
            "visibility_m": hourly.get("visibility", []),
            "_meta": raw.get("_meta", {}),
        }

    def assess_hazard(
        self,
        weather_data: Dict[str, Any],
        thresholds: Optional[WeatherThresholds] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate weather data against hazard thresholds.
        Returns hazard assessment with severity and type.
        """
        t = thresholds or self.thresholds
        hazards_detected = []
        max_severity = "none"

        precip = weather_data.get("precipitation_mm", 0) or 0
        wind = weather_data.get("wind_speed_kmh", 0) or 0
        gusts = weather_data.get("wind_gusts_kmh", 0) or 0
        temp = weather_data.get("temperature_c")
        code = weather_data.get("weather_code", 0) or 0

        # Check precipitation
        if precip >= t.precipitation_mm_per_hour:
            severity = "critical" if precip >= t.precipitation_mm_per_hour * 2 else "high"
            hazards_detected.append({
                "type": "heavy_precipitation",
                "value": precip,
                "threshold": t.precipitation_mm_per_hour,
                "severity": severity,
                "description": f"Precipitation {precip}mm exceeds threshold {t.precipitation_mm_per_hour}mm",
            })
            max_severity = self._escalate_severity(max_severity, severity)

        # Check wind speed
        if wind >= t.wind_speed_kmh:
            severity = "critical" if wind >= t.wind_speed_kmh * 1.5 else "high"
            hazards_detected.append({
                "type": "high_wind",
                "value": wind,
                "threshold": t.wind_speed_kmh,
                "severity": severity,
                "description": f"Wind speed {wind}km/h exceeds threshold {t.wind_speed_kmh}km/h",
            })
            max_severity = self._escalate_severity(max_severity, severity)

        # Check wind gusts
        if gusts >= t.wind_gusts_kmh:
            severity = "critical" if gusts >= t.wind_gusts_kmh * 1.5 else "high"
            hazards_detected.append({
                "type": "dangerous_gusts",
                "value": gusts,
                "threshold": t.wind_gusts_kmh,
                "severity": severity,
                "description": f"Wind gusts {gusts}km/h exceeds threshold {t.wind_gusts_kmh}km/h",
            })
            max_severity = self._escalate_severity(max_severity, severity)

        # Check extreme temperature
        if temp is not None:
            if temp >= t.temperature_max_celsius:
                hazards_detected.append({
                    "type": "extreme_heat",
                    "value": temp,
                    "threshold": t.temperature_max_celsius,
                    "severity": "high",
                    "description": f"Temperature {temp}°C exceeds {t.temperature_max_celsius}°C",
                })
                max_severity = self._escalate_severity(max_severity, "high")

            if temp <= t.temperature_min_celsius:
                hazards_detected.append({
                    "type": "freeze_risk",
                    "value": temp,
                    "threshold": t.temperature_min_celsius,
                    "severity": "medium",
                    "description": f"Temperature {temp}°C below {t.temperature_min_celsius}°C",
                })
                max_severity = self._escalate_severity(max_severity, "medium")

        # Check severe weather codes
        if code in t.severe_weather_codes:
            code_desc = WMO_WEATHER_CODES.get(code, "Severe weather")
            severity = "critical" if code >= 95 else "high"
            hazards_detected.append({
                "type": "severe_weather_code",
                "value": code,
                "threshold": "severe code list",
                "severity": severity,
                "description": f"WMO code {code}: {code_desc}",
            })
            max_severity = self._escalate_severity(max_severity, severity)

        is_hazardous = len(hazards_detected) > 0

        # Determine primary hazard type for zone creation
        primary_type = "clear"
        if hazards_detected:
            type_priority = {
                "severe_weather_code": 6,
                "dangerous_gusts": 5,
                "high_wind": 4,
                "heavy_precipitation": 3,
                "extreme_heat": 2,
                "freeze_risk": 1,
            }
            hazards_detected.sort(
                key=lambda h: type_priority.get(h["type"], 0), reverse=True
            )
            primary_type = hazards_detected[0]["type"]

        return {
            "is_hazardous": is_hazardous,
            "severity": max_severity,
            "primary_hazard_type": primary_type,
            "hazards": hazards_detected,
            "hazard_count": len(hazards_detected),
            "weather_data": weather_data,
        }

    def scan_all_corridors(
        self, thresholds: Optional[WeatherThresholds] = None
    ) -> Dict[str, Any]:
        """
        Poll LIVE weather for all configured logistics corridor hubs.
        Returns comprehensive assessment for each hub.
        """
        results = []
        hazardous_hubs = []
        errors = []

        for hub in LOGISTICS_HUBS:
            try:
                weather = self.get_current_weather(hub.lat, hub.lon)
                assessment = self.assess_hazard(weather, thresholds)

                hub_result = {
                    "hub_name": hub.name,
                    "lat": hub.lat,
                    "lon": hub.lon,
                    "region": hub.region,
                    "capacity_tier": hub.capacity_tier,
                    "weather": weather,
                    "assessment": assessment,
                }

                results.append(hub_result)

                if assessment["is_hazardous"]:
                    hazardous_hubs.append(hub_result)

            except Exception as e:
                error_info = {
                    "hub_name": hub.name,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
                errors.append(error_info)
                logger.error(f"Weather scan failed for {hub.name}: {e}")

        return {
            "scan_time": datetime.now(timezone.utc).isoformat(),
            "total_hubs_scanned": len(results),
            "total_errors": len(errors),
            "hazardous_hub_count": len(hazardous_hubs),
            "hubs": results,
            "hazardous_hubs": hazardous_hubs,
            "errors": errors,
        }

    def scan_custom_location(
        self,
        lat: float,
        lon: float,
        name: str = "Custom Location",
        thresholds: Optional[WeatherThresholds] = None,
    ) -> Dict[str, Any]:
        """
        Scan a user-specified coordinate for live weather and hazard assessment.
        Used by the stress-testing sidebar for judge demonstrations.
        """
        weather = self.get_current_weather(lat, lon)
        assessment = self.assess_hazard(weather, thresholds)

        return {
            "location_name": name,
            "lat": lat,
            "lon": lon,
            "weather": weather,
            "assessment": assessment,
            "scan_time": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _escalate_severity(current: str, new: str) -> str:
        """Return the higher severity level."""
        order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        if order.get(new, 0) > order.get(current, 0):
            return new
        return current


# ─── Module-level convenience instance ────────────────────────────────────────

_weather_service: Optional[WeatherService] = None


def get_weather_service() -> WeatherService:
    """Get or create the singleton WeatherService instance."""
    global _weather_service
    if _weather_service is None:
        _weather_service = WeatherService()
    return _weather_service

