"""app/beat.py — the `relay` docker-compose service's minimal Celery app.

Must never import app.worker (anthropic/analysis_pipeline/llm_client) or
app.db: the whole point of splitting it out of worker.py is that the beat
process only ticks a schedule and sends task messages by name, so it should
never need Postgres access or the heavy LLM dependency chain. Run in a
subprocess -- an in-process check would be meaningless once any other test
in the same session has already imported app.worker (as test_relay.py does).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_importing_beat_never_pulls_in_worker_or_db():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.beat, sys\n"
            "assert 'app.worker' not in sys.modules, 'app.beat must not import app.worker'\n"
            "assert 'app.db' not in sys.modules, 'app.beat must not import app.db'\n"
            "assert 'anthropic' not in sys.modules, 'app.beat must not import anthropic'\n",
        ],
        cwd=BACKEND_DIR,
        env=os.environ,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_beat_app_has_the_sweep_outbox_schedule():
    from app.beat import beat_app
    from app.beat_schedule import BEAT_SCHEDULE

    assert beat_app.conf.beat_schedule == BEAT_SCHEDULE
    assert BEAT_SCHEDULE["sweep-outbox"]["task"] == "relay.sweep_outbox"


def test_worker_registers_the_same_schedule():
    """worker.py and app.beat must tick the identical schedule -- both
    import BEAT_SCHEDULE from app/beat_schedule.py rather than each
    defining their own, so they can't silently drift apart.
    """
    from app.beat_schedule import BEAT_SCHEDULE
    from app.worker import celery_app

    assert celery_app.conf.beat_schedule == BEAT_SCHEDULE
