"""Per-process request counters shared by the routers that serve /chat, /search and /metrics (moved from main.py, S7).

The counters are module attributes: a handler updates them as ``request_stats.total_requests += 1`` and /metrics reads
them from here, so no router needs to import main.py.
"""
from __future__ import annotations

import time

start_time = time.time()
total_requests = 0
error_requests = 0
