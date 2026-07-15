"""SQLAlchemy engine/session setup, shared by the API and the worker."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _normalize(url: str) -> str:
    # requirements.txt pins psycopg3, but DATABASE_URL in .env.example uses the
    # driver-less "postgresql://" scheme, which SQLAlchemy resolves to psycopg2.
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _normalize(os.environ.get("DATABASE_URL", "postgresql://lexireview:lexireview_dev@postgres:5432/lexireview"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
