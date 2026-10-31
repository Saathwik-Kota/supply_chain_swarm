"""
Agent 3: Dynamic Routing & Avoidance Agent

Queries the OSRM Live Routing API for highway geometry between supplier depot and affected destination.
Autonomous Self-Correction Loop: Checks if primary route polyline intersects active hazard zones (Shapely).
If collision is detected, autonomously calculates detour waypoints outside hazard radius, re-queries OSRM,
and validates the safe route with complete reasoning traces.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from swarm.orchestrator import run_routing_agent
from swarm.state import SwarmState

__all__ = ["run_routing_agent"]

