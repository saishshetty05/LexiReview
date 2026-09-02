"""Minimal Celery app for the `relay` docker-compose service's `celery beat`
process -- ticks the schedule and sends task messages by name only; it never
imports or executes a task body.

Deliberately does NOT import app.worker: that module pulls in
anthropic/analysis_pipeline/llm_client (the whole heavy LLM dependency
chain) purely to define tasks this process never runs, and app.db (which
requires APP_DATABASE_URL) for a role this process never connects as. Same
"thin producer-side client" pattern as app.main's old _celery_producer.
"""
from __future__ import annotations

import os

from celery import Celery

from app.beat_schedule import BEAT_SCHEDULE

beat_app = Celery("lexireview-beat", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
beat_app.conf.beat_schedule = BEAT_SCHEDULE
