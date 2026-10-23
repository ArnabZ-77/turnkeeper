"""FastAPI entry point.

Routers are mounted by the builders (see docs/CONTRACTS.md). This file must always boot even when
no PayPal or Anthropic credentials are configured, so /healthz works on a fresh deploy.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

log = logging.getLogger("turnkeeper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info(
        "starting turnkeeper env=%s paypal_configured=%s anthropic_configured=%s",
        settings.paypal_env,
        settings.paypal_configured,
        settings.anthropic_configured,
    )
    yield


app = FastAPI(title="Turnkeeper", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "ok": True,
            "env": s.paypal_env,
            "paypal_configured": s.paypal_configured,
            "anthropic_configured": s.anthropic_configured,
            "frozen": s.freeze,
        }
    )


# Builders register routers here (import inside a function so a missing module never breaks boot).
def _include_routers() -> None:
    import importlib

    for mod in ("app.routes.circles", "app.routes.judge", "app.routes.join", "app.routes.webhooks", "app.routes.events"):
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError as e:  # router not built yet
            log.warning("router %s not available: %s", mod, e)
            continue
        app.include_router(m.router)

    # Public read-only MCP (B3) behind bearer auth; guarded so boot never fails without it.
    try:
        from app.mcp.auth import mount_public_mcp

        mount_public_mcp(app)
    except (ImportError, Exception) as e:  # noqa: B014 - ImportError listed on purpose (missing module is the common case)
        log.warning("public MCP not mounted: %s", e)


_include_routers()

# Static SPA (built by `npm run build` into app/static). Mounted last so API routes win.
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        candidate = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
