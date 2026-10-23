"""FastAPI entry point.

Routers are mounted by the builders (see docs/CONTRACTS.md). This file must always boot even when
no PayPal or Anthropic credentials are configured, so /healthz works on a fresh deploy.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import check_deploy_secrets, get_settings, is_deployed

log = logging.getLogger("turnkeeper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_deploy_secrets(settings)  # raises on an https deployment with default/short signing secrets
    log.info(
        "starting turnkeeper env=%s paypal_configured=%s llm_provider=%s llm_configured=%s llm_cache=%s",
        settings.paypal_env,
        settings.paypal_configured,
        settings.llm_provider,
        settings.llm_configured,
        settings.llm_cache,
    )
    if settings.llm_cache in ("replay", "auto"):
        # Recorded AI runs shipped in app/fixtures/llm_cache.jsonl (scripts/export_llm_cache.py): a keyless
        # deploy with no disk replays them instead of falling straight to rules mode.
        try:
            from app.agent.llm import load_shipped_cache

            loaded = load_shipped_cache()
            if loaded:
                log.info("loaded %d recorded AI turns from the shipped replay cache", loaded)
        except Exception as e:  # noqa: BLE001 - the app must boot without its recordings
            log.warning("shipped replay cache not loaded: %s", e)
    yield


app = FastAPI(title="Turnkeeper", version="0.1.0", lifespan=lifespan)


def _vault_seeded() -> bool:
    """True when TK_SEED_VAULT_IDS gives every fixture member a real sandbox vault id."""
    try:
        from app.engine.fixture import fixture_charter, is_placeholder_vault, load_fixture, seed_overrides

        overrides = seed_overrides()
        seeds = load_fixture().get("seed", {}).get("vault_ids", {})
        return all(
            not is_placeholder_vault(overrides.get(m.handle, {}).get("vault_id") or seeds.get(m.handle))
            for m in fixture_charter().members
        )
    except Exception:  # noqa: BLE001 - health must answer even when the engine is broken
        return False


def _config_warnings(request: Request) -> list[str]:
    s = get_settings()
    out = []
    host = (request.url.hostname or "").lower()
    local_host = host in ("localhost", "127.0.0.1", "::1", "testserver")
    if not local_host and not is_deployed(s):
        out.append("PUBLIC_BASE_URL is not the public https URL of this host (cookies, /join return URLs)")
    if not s.paypal_configured:
        out.append("PayPal sandbox credentials not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)")
    if not _vault_seeded():
        out.append("members not vaulted: set TK_SEED_VAULT_IDS (see .env.example)")
    return out


@app.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "ok": True,
            "env": s.paypal_env,
            "paypal_configured": s.paypal_configured,
            "anthropic_configured": s.anthropic_configured,
            # live AI = a model key for the selected provider (free Groq/Gemini by default); without one the
            # treasurer replays recorded runs or runs in rules mode
            "llm_configured": s.llm_configured,
            "llm_provider": s.llm_provider,
            "llm_cache": s.llm_cache,
            "frozen": s.freeze,
            "vault_seeded": _vault_seeded(),
            "warnings": _config_warnings(request),
        }
    )


# Builders register routers here (import inside a function so a missing module never breaks boot).
def _include_routers() -> None:
    import importlib

    for mod in (
        "app.routes.circles",
        "app.routes.recovery",  # before judge: its fixed /api/judge/* recovery actions win
        "app.routes.judge",
        "app.routes.join",
        "app.routes.compile",  # charter compiler + freeze (builder B)
        "app.routes.webhooks",
        "app.routes.events",
    ):
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

    _STATIC_ROOT = os.path.realpath(STATIC_DIR)

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        # Only serve real files that resolve inside app/static; everything else gets the SPA shell.
        candidate = os.path.realpath(os.path.join(_STATIC_ROOT, full_path))
        inside = os.path.commonpath([_STATIC_ROOT, candidate]) == _STATIC_ROOT
        if full_path and inside and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_STATIC_ROOT, "index.html"))
