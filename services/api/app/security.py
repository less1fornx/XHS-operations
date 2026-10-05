"""Access code for other devices, error logging for unhandled failures, and CORS."""

from __future__ import annotations

import os
import traceback
import hmac
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware


PROJECT_ROOT = Path(__file__).resolve().parents[3]


# LAN sharing: other devices on the same network must present this code.
# The machine itself (127.0.0.1) never needs it. Empty means local use only.
ACCESS_CODE = os.getenv("AGENT_ACCESS_CODE", "").strip()
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


# Overridable so test runs never write into the real log.
ERROR_LOG = Path(os.getenv("AGENT_ERROR_LOG", str(PROJECT_ROOT / "data" / "logs" / "api-errors.log")))


async def require_access_code(request: Request, call_next):
    client = request.client.host if request.client else ""
    if client not in LOOPBACK_HOSTS and request.method != "OPTIONS":
        supplied = request.headers.get("X-Access-Code") or request.query_params.get("code") or ""
        # Compare as bytes: compare_digest rejects non-ASCII text.
        if not ACCESS_CODE or not hmac.compare_digest(supplied.encode(), ACCESS_CODE.encode()):
            return JSONResponse(status_code=401, content={"detail": "access_code_required: 请输入访问码"})
    try:
        return await call_next(request)
    except Exception as exc:
        # An unhandled error would otherwise leave as a bare 500 without CORS headers,
        # which the browser can only report as "Failed to fetch". Answer in JSON and
        # keep the traceback on disk so the cause can be found afterwards.
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} {request.method} {request.url.path}\n")
            log.write(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"detail": f"后台处理这一步时出错（{type(exc).__name__}）。详情已记录到 data/logs/api-errors.log，请把这句话告诉 Claude。"},
        )


def install(app: FastAPI) -> None:
    """Attach the access check first, then CORS, so CORS headers are also set on 401 and 500 replies."""
    app.middleware("http")(require_access_code)
    app.add_middleware(
        CORSMiddleware,
        # This machine on any port, plus private-network addresses for LAN sharing.
        allow_origin_regex=(
            r"http://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
            r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[\w-]+\.local):\d+"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
