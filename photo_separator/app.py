import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .catalog import CATALOG_URL, discover_free_vision
from .providers import PRESETS, ProviderError
from .schemas import ManualRequest, SaveRequest, SelectionRequest
from .selection import select
from .store import Conflict, Store

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app):
    app.state.store = Store(os.environ.get("PHOTO_DB", str(ROOT / "data/workbench.sqlite3")))
    app.state.previews = Path(app.state.store.path).parent / "previews"
    app.state.previews.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Photo Separator · Research Workbench", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


@app.middleware("http")
async def local_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
        return JSONResponse({"detail": "Cross-origin requests blocked"}, status_code=403)
    declared = request.headers.get("content-length")
    if declared and int(declared) > 300_000:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # FastAPI's default errors echo the invalid input, potentially including an API key.
    return JSONResponse(
        {"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]},
        status_code=422,
    )


@app.get("/")
async def index():
    return FileResponse(ROOT / "static/index.html")


@app.get("/api/providers")
async def providers():
    return {
        "presets": PRESETS,
        "prices_checked_at": "2026-10-29",
        "actual_model_quality_ranking": "not_measured",
    }


@app.get("/api/remote-models")
async def remote_models():
    try:
        presets = await discover_free_vision()
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise HTTPException(
            503, "Remote catalog unavailable; existing presets and saved runs remain usable"
        ) from None
    return {
        "source": CATALOG_URL,
        "checked_at": datetime.now(UTC).isoformat(),
        "presets": presets,
        "note": "Public catalog metadata only. Inference requires your key, quota and an available endpoint; model quality is not measured.",
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": "local_research"}


@app.post("/api/select")
async def select_photos(body: SelectionRequest, request: Request):
    try:
        run = await select(body, preview_dir=request.app.state.previews)
        await asyncio.to_thread(request.app.state.store.save_run, run)
        return run
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    run = await asyncio.to_thread(request.app.state.store.run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/restaurants/{restaurant}/cover")
async def get_cover(restaurant: str, request: Request):
    return await asyncio.to_thread(request.app.state.store.get, restaurant)


@app.put("/api/restaurants/{restaurant}/cover")
async def save_cover(restaurant: str, body: SaveRequest, request: Request):
    run = await asyncio.to_thread(request.app.state.store.run, body.run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return await mutate(request, restaurant, body.expected_revision, run=run)


@app.patch("/api/restaurants/{restaurant}/cover")
async def manual_cover(restaurant: str, body: ManualRequest, request: Request):
    return await mutate(request, restaurant, body.expected_revision, manual=(body.url, body.action))


async def mutate(request, restaurant, revision, **kwargs):
    try:
        return await asyncio.to_thread(request.app.state.store.mutate, restaurant, revision, **kwargs)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/previews/{digest}")
async def preview(digest: str, request: Request):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(404, "Preview not found")
    path = request.app.state.previews / (digest + ".jpg")
    if not path.is_file():
        raise HTTPException(404, "Preview not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/examples")
async def examples():
    import json

    path = ROOT / "examples/restaurants.json"
    return json.loads(path.read_text()) if path.exists() else []


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
