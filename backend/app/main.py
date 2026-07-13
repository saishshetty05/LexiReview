"""Minimal FastAPI app — grows per SETUP_GUIDE.md Phase 5."""
from fastapi import FastAPI

app = FastAPI(title="LexiReview API")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
