"""
Configuration module for Emergency Response & Supply Chain Logistics Swarm.
All API endpoints, corridor coordinates, environment variables, and system constants.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ─── API Endpoints (Zero API Key Required) ───────────────────────────────────

OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1/forecast"
OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
GDACS_RSS_URL = "https://www.gdacs.org/xml/rss.xml"
EONET_API_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"

# ─── Database Configuration ──────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///supply_chain_swarm.db")
DATABASE_ECHO = os.getenv("DATABASE_ECHO", "false").lower() == "true"

# ─── Indian Logistics Corridor Hub Coordinates ───────────────────────────────

@dataclass(frozen=True)
class HubCoordinate:
    """Represents an official logistics terminal / disaster response facility."""
    name: str
    facility_name: str
    facility_code: str
    facility_type: str
    lat: float
    lon: float
    region: str
    capacity_tier: str  # "primary", "secondary", "tertiary"
    address: str


# Real national logistics infrastructure: CONCOR Multi-Modal Logistics Parks (MMLPs),
# Inland Container Depots (ICD), Port Container Terminals, and NDRF Regional Response Bases
LOGISTICS_HUBS: List[HubCoordinate] = [
    HubCoordinate(
        "Bangalore", "CONCOR MMLP Whitefield Logistics Hub", "INWFD6",
        "Multi-Modal Logistics Park (MMLP)", 12.9830, 77.7580, "South", "primary",
        "Whitefield Goods Yard, K.R. Puram, Bengaluru 560066, Karnataka"
    ),
    HubCoordinate(
        "Chennai", "CONCOR CFS Tondiarpet & Chennai Port Terminal", "INTPA6",
        "Container Freight Station & Port Depot", 13.1256, 80.2925, "South", "primary",
        "Tondiarpet Marshalling Yard, Chennai Port Zone, Chennai 600081, Tamil Nadu"
    ),
    HubCoordinate(
        "Mumbai", "JNPT Multi-Modal Logistics Terminal, Navi Mumbai", "INNSA1",
        "Port Terminal & Inland Container Depot", 18.9500, 72.9500, "West", "primary",
        "Jawaharlal Nehru Port Trust, Nhava Sheva, Navi Mumbai 400707, Maharashtra"
    ),
    HubCoordinate(
        "Hyderabad", "CONCOR Inland Container Depot Sanathnagar", "INSNT6",
        "Inland Container Depot (ICD)", 17.4589, 78.4412, "South", "primary",
        "Sanathnagar Industrial Area, Hyderabad 500018, Telangana"
    ),
    HubCoordinate(
        "Guwahati", "CONCOR MMLP Amingaon & NDRF 1st Bn Depot", "INAMI6",
        "Multi-Modal Logistics Park & NDRF Base", 26.1820, 91.6820, "Northeast", "primary",
        "Amingaon Freight Terminal, Kamrup, Guwahati 781031, Assam"
    ),
    HubCoordinate(
        "Delhi", "CONCOR Multi-Modal Logistics Park Dadri / NCR", "INDER6",
        "National Multi-Modal Logistics Hub", 28.5355, 77.5647, "North", "primary",
        "Greater Noida Logistics Zone, Dadri, Gautam Buddha Nagar 201306, Uttar Pradesh"
    ),
    HubCoordinate(
        "Kolkata", "Syama Prasad Mookerjee Port Majerhat Freight Terminal", "INCCU1",
        "Port Inland Freight Station", 22.5180, 88.3180, "East", "primary",
        "Majerhat Railway Goods Yard, Diamond Harbour Rd, Kolkata 700053, West Bengal"
    ),
    HubCoordinate(
        "Pune", "IndoSpace Industrial & Logistics Park Chakan", "INPNQ6",
        "Industrial Logistics Hub", 18.7562, 73.8614, "West", "secondary",
        "MIDC Phase II, Chakan Logistics Corridor, Pune 410501, Maharashtra"
    ),
    HubCoordinate(
        "Ahmedabad", "CONCOR ICD Khodiyar Multi-Modal Hub", "INKHD6",
        "Inland Container Depot", 23.1840, 72.5820, "West", "secondary",
        "Khodiyar Railway Yard, Gandhinagar-Ahmedabad Corridor 382421, Gujarat"
    ),
    HubCoordinate(
        "Jaipur", "CONCOR Inland Container Depot Kanakpura", "INKKP6",
        "Inland Container Depot", 26.9380, 75.7240, "North", "secondary",
        "Kanakpura Railway Terminal, Sirsi Road, Jaipur 302012, Rajasthan"
    ),
    HubCoordinate(
        "Lucknow", "Central Warehousing Corp Transport Nagar Logistics Depot", "INLKO6",
        "Central Warehousing Depot", 26.8850, 80.9850, "North", "secondary",
        "Transport Nagar, Kanpur Road Corridor, Lucknow 226012, Uttar Pradesh"
    ),
    HubCoordinate(
        "Bhopal", "CONCOR Inland Container Depot Mandideep", "INMDP6",
        "Inland Container Depot", 23.0560, 77.5240, "Central", "secondary",
        "Mandideep Industrial Growth Centre, Bhopal 462046, Madhya Pradesh"
    ),
    HubCoordinate(
        "Nagpur", "CONCOR Multi-Modal Logistics Park MIHAN", "INNAG6",
        "Multi-Modal Logistics Park (MMLP)", 21.0420, 79.0340, "Central", "secondary",
        "MIHAN Multi-Modal Cargo Zone, Wardha Road, Nagpur 441108, Maharashtra"
    ),
    HubCoordinate(
        "Visakhapatnam", "Visakhapatnam Port Container Terminal Marripalem", "INVTZ1",
        "Port Freight Station", 17.7420, 83.2740, "East", "tertiary",
        "Port Connectivity Industrial Zone, Marripalem, Visakhapatnam 530018, Andhra Pradesh"
    ),
    HubCoordinate(
        "Kochi", "Cochin Port Trust CFS Willingdon Island", "INCOK1",
        "Port Container Freight Station", 9.9530, 76.2710, "South", "tertiary",
        "Willingdon Island Port Zone, Harbour Road, Kochi 682003, Kerala"
    ),
    HubCoordinate(
        "Coimbatore", "CONCOR Inland Container Depot Irugur", "INIGU6",
        "Inland Container Depot", 11.0250, 77.0620, "South", "tertiary",
        "Irugur Railway Freight Terminal, Coimbatore 641103, Tamil Nadu"
    ),
    HubCoordinate(
        "Patna", "Central Railside Warehouse Complex Fatuha", "INFUT6",
        "Railside Logistics Warehouse", 25.5180, 85.3120, "East", "tertiary",
        "Fatuha Industrial Area, Patna-Bhaktiyarpur Corridor 803201, Bihar"
    ),
    HubCoordinate(
        "Bhubaneswar", "CONCOR MMLP Janla Logistics Hub", "INBBI6",
        "Multi-Modal Logistics Park", 20.2150, 85.7420, "East", "tertiary",
        "Janla Industrial Estate, NH-16, Bhubaneswar 752054, Odisha"
    ),
    HubCoordinate(
        "Thiruvananthapuram", "Vizhinjam Port International Logistics Zone", "INVZJ1",
        "Transshipment Port Logistics Base", 8.3750, 76.9920, "South", "tertiary",
        "Vizhinjam International Seaport Corridor, Thiruvananthapuram 695521, Kerala"
    ),
    HubCoordinate(
        "Indore", "CONCOR Inland Container Depot Tihi Logistics Park", "INTIH6",
        "Inland Container Depot & SEZ", 22.5850, 75.6820, "Central", "tertiary",
        "Pithampur Industrial Corridor, Tihi, Dhar-Indore 454775, Madhya Pradesh"
    ),
]

# ─── Weather Thresholds for Hazard Detection ─────────────────────────────────

@dataclass
class WeatherThresholds:
    """Configurable thresholds for triggering hazard alerts."""
    precipitation_mm_per_hour: float = 30.0       # Heavy rain threshold
    wind_speed_kmh: float = 80.0                  # Storm/cyclone threshold
    temperature_max_celsius: float = 45.0          # Extreme heat threshold
    temperature_min_celsius: float = 2.0           # Frost/freeze threshold
    wind_gusts_kmh: float = 100.0                  # Dangerous gust threshold
    snowfall_cm: float = 10.0                      # Heavy snowfall threshold
    visibility_meters: float = 500.0               # Low visibility threshold
    uv_index: float = 11.0                         # Extreme UV threshold

    # WMO severe weather codes (thunderstorms, heavy rain, etc.)
    severe_weather_codes: List[int] = field(default_factory=lambda: [
        65, 67, 73, 75, 77, 80, 81, 82,  # Heavy rain/snow/showers
        85, 86,                            # Snow showers
        95, 96, 99,                        # Thunderstorms
    ])


DEFAULT_WEATHER_THRESHOLDS = WeatherThresholds()

# ─── Hazard Zone Configuration ───────────────────────────────────────────────

HAZARD_RADIUS_KM = 50.0           # Default radius around hazard epicenter
HAZARD_BUFFER_KM = 20.0           # Buffer zone beyond hazard radius for routing
ROUTE_COLLISION_TOLERANCE_KM = 5.0  # Tolerance for route-hazard intersection check

# ─── Emergency Relief Supply Categories ──────────────────────────────────────

SUPPLY_CATEGORIES = {
    "medical_kits": {
        "display_name": "Medical First-Aid Kits",
        "unit": "kit",
        "weight_kg": 5.0,
        "priority": 1,
        "min_reserve": 50,
    },
    "water_purification": {
        "display_name": "Water Purification Units",
        "unit": "unit",
        "weight_kg": 12.0,
        "priority": 1,
        "min_reserve": 30,
    },
    "solar_generators": {
        "display_name": "Portable Solar Generators",
        "unit": "unit",
        "weight_kg": 25.0,
        "priority": 2,
        "min_reserve": 10,
    },
    "food_rations": {
        "display_name": "Emergency Food Ration Packs",
        "unit": "pack",
        "weight_kg": 8.0,
        "priority": 1,
        "min_reserve": 100,
    },
    "tents_shelters": {
        "display_name": "Emergency Shelter Tents",
        "unit": "unit",
        "weight_kg": 15.0,
        "priority": 2,
        "min_reserve": 20,
    },
    "blankets": {
        "display_name": "Thermal Blankets",
        "unit": "pack_of_10",
        "weight_kg": 6.0,
        "priority": 3,
        "min_reserve": 40,
    },
    "communication_kits": {
        "display_name": "Emergency Communication Kits",
        "unit": "kit",
        "weight_kg": 3.0,
        "priority": 2,
        "min_reserve": 15,
    },
}

# ─── Fleet Configuration ─────────────────────────────────────────────────────

VEHICLE_TYPES = {
    "heavy_truck": {
        "display_name": "Heavy Cargo Truck",
        "capacity_kg": 10000,
        "avg_speed_kmh": 60,
        "fuel_type": "diesel",
    },
    "medium_truck": {
        "display_name": "Medium Cargo Truck",
        "capacity_kg": 5000,
        "avg_speed_kmh": 70,
        "fuel_type": "diesel",
    },
    "light_van": {
        "display_name": "Light Delivery Van",
        "capacity_kg": 2000,
        "avg_speed_kmh": 80,
        "fuel_type": "diesel",
    },
    "helicopter": {
        "display_name": "Cargo Helicopter",
        "capacity_kg": 3000,
        "avg_speed_kmh": 200,
        "fuel_type": "aviation",
    },
}

# ─── Carbon & Fuel Consumption Constants ─────────────────────────────────────
# Used by the dispatch agent to compute environmental impact metrics.

# Average fuel consumption by vehicle type (liters per 100 km)
FUEL_CONSUMPTION_L_PER_100KM = {
    "heavy_truck": 35.0,    # Loaded heavy cargo truck (~35 L/100km)
    "medium_truck": 25.0,   # Medium cargo (~25 L/100km)
    "light_van": 15.0,      # Light delivery van (~15 L/100km)
    "helicopter": 250.0,    # Cargo helicopter (~250 L/100km equivalent)
}

# CO₂ emission factors (kg CO₂ per liter of fuel burned)
CO2_KG_PER_LITER = {
    "diesel": 2.68,         # IPCC default for diesel: 2.68 kg CO₂ / liter
    "aviation": 2.52,       # Jet-A1 aviation fuel: ~2.52 kg CO₂ / liter
}

# Cost per liter of fuel (INR)
FUEL_COST_INR_PER_LITER = {
    "diesel": 89.62,        # Avg Indian diesel price ~₹89.62/L
    "aviation": 105.00,     # Aviation turbine fuel ~₹105/L
}

# ─── Open-Meteo API Parameters ───────────────────────────────────────────────

OPEN_METEO_HOURLY_PARAMS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "visibility",
]

OPEN_METEO_CURRENT_PARAMS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]

# ─── WMO Weather Code Descriptions ───────────────────────────────────────────

WMO_WEATHER_CODES: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# ─── Streamlit UI Configuration ──────────────────────────────────────────────

STREAMLIT_PAGE_TITLE = "🚨 Emergency Response & Supply Chain Swarm Command Center"
STREAMLIT_PAGE_ICON = "🚨"
STREAMLIT_LAYOUT = "wide"

MAP_CENTER_LAT = 20.5937  # India center
MAP_CENTER_LON = 78.9629
MAP_DEFAULT_ZOOM = 5

# ─── Agent Configuration ─────────────────────────────────────────────────────

AGENT_NAMES = {
    "telemetry": "Telemetry & Threat Monitor Agent",
    "inventory": "Regional Inventory & Supplier Agent",
    "routing": "Dynamic Routing & Avoidance Agent",
    "dispatch": "Autonomous Dispatch & Execution Coordinator",
}

MAX_RETRY_ATTEMPTS = 3
API_TIMEOUT_SECONDS = 15
ROUTING_MAX_DETOUR_ATTEMPTS = 5

