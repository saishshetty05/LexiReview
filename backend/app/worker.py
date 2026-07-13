"""Celery worker — Person B extends this per SETUP_GUIDE.md Phase 5."""
import os

from celery import Celery

celery_app = Celery("lexireview", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                    backend=os.environ.get("REDIS_URL", "redis://redis:6379/0"))


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"
