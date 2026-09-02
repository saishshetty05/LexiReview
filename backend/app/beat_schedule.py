"""Celery beat schedule -- pure data, deliberately dependency-free.

Shared by app/worker.py (which registers and executes the tasks named here)
and app/beat.py (a separate, minimal Celery app that only ticks this
schedule, for the `relay` docker-compose service -- see that module for why
it exists as its own file rather than reusing worker.py's celery_app).
"""
from __future__ import annotations

BEAT_SCHEDULE = {
    "sweep-outbox": {"task": "relay.sweep_outbox", "schedule": 10.0},
}
