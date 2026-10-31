"""
Agent 1: Telemetry & Threat Monitor Agent

Continuously polls live weather telemetry (Open-Meteo API) across active Indian logistics corridors.
Assesses hazards against configurable thresholds and registers hazard zones in SQLite DB.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from swarm.orchestrator import run_telemetry_agent
from swarm.state import SwarmState

__all__ = ["run_telemetry_agent"]

