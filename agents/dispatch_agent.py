"""
Agent 4: Autonomous Dispatch & Execution Coordinator

Executes the end-to-end relief order:
- Atomically deducts inventory in the SQL database enforcing minimum reserves
- Assigns available fleet vehicles by weight capacity
- Records recalculated route geometry, duration, distance, and ETA
- Generates a structured dispatch manifest and audit trace
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from swarm.orchestrator import run_dispatch_agent
from swarm.state import SwarmState

__all__ = ["run_dispatch_agent"]

