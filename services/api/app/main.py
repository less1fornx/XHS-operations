"""The API application: create it, install security, mount the groups of endpoints."""

from __future__ import annotations

from fastapi import FastAPI

from . import security
from .routers import batches, replies, revisits, runs, status
from .runtime import check_first_message, graph, store  # noqa: F401  (kept importable from here)


app = FastAPI(title="Agent Operations API", version="0.1.0")
security.install(app)
for group in (status, runs, batches, replies, revisits):
    app.include_router(group.router)
