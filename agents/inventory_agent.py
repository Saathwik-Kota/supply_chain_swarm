"""
Agent 2: Regional Inventory & Supplier Agent

Connects via SQLAlchemy ORM / FastAPI endpoints to query regional warehouses for emergency relief stock.
Verifies stock availability, reserve thresholds, and unit weights, selecting the optimal depot.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from swarm.orchestrator import run_inventory_agent
from swarm.state import SwarmState

__all__ = ["run_inventory_agent"]

