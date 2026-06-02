"""SDR Research MCP server.

Exposes the read-only SDR Viewer HTTP API (recordings, spots, APRS, repeaters,
station stats, SDR hardware health) as MCP tools over streamable HTTP so Claude
can query the ham-radio monitoring station directly.

Auth: a static bearer token (env MCP_TOKEN). Every request to the MCP endpoint
must send `Authorization: Bearer <token>`. The /healthz probe is unauthenticated.

The server is a thin wrapper over the in-cluster API at SDR_API_BASE
(default http://sdr-viewer-api:8000) — no database access of its own.
"""

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

API_BASE = os.environ.get("SDR_API_BASE", "http://sdr-viewer-api:8000")
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")

# DNS-rebinding protection guards browser-embedded servers against malicious
# origins. This endpoint is server-to-server, bearer-gated, and sits behind the
# TLS gateway, so we disable it rather than maintain a brittle Host allowlist.
mcp = FastMCP(
    "sdr-research",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_client = httpx.AsyncClient(base_url=API_BASE, timeout=30.0)


async def _get(path: str, params: Optional[dict] = None) -> Any:
    """GET a JSON endpoint on the SDR API, dropping unset params.

    Returns parsed JSON on success, or a structured error dict so the model
    sees why a call failed instead of an opaque exception.
    """
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = await _client.get(path, params=clean)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        detail = None
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text
        return {"error": f"HTTP {exc.response.status_code}", "detail": detail}
    except httpx.HTTPError as exc:
        return {"error": "request failed", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_recordings(
    q: str,
    mode: Optional[str] = None,
    callsign: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> Any:
    """Full-text search across recording transcripts and CW decodes.

    Args:
        q: Search text (matches transcript / decoded text).
        mode: Optional filter, either "cw" or "voice".
        callsign: Optional callsign filter.
        page: 1-based page number.
        limit: Results per page (1-200).
    """
    return await _get(
        "/api/v1/search/text",
        {"q": q, "mode": mode, "callsign": callsign, "page": page, "limit": limit},
    )


@mcp.tool()
async def browse_recordings(
    mode: Optional[str] = None,
    frequency_min: Optional[float] = None,
    frequency_max: Optional[float] = None,
    q: Optional[str] = None,
    callsign: Optional[str] = None,
    duration_min: Optional[float] = None,
    duration_max: Optional[float] = None,
    page: int = 1,
    limit: int = 50,
) -> Any:
    """Browse/filter recordings (most recent first).

    Args:
        mode: "cw" or "voice".
        frequency_min: Lower bound in Hz.
        frequency_max: Upper bound in Hz.
        q: Optional text filter.
        callsign: Optional callsign filter.
        duration_min: Minimum duration in seconds.
        duration_max: Maximum duration in seconds.
        page: 1-based page number.
        limit: Results per page (1-200).
    """
    return await _get(
        "/api/v1/files/browse",
        {
            "mode": mode,
            "frequency_min": frequency_min,
            "frequency_max": frequency_max,
            "q": q,
            "callsign": callsign,
            "duration_min": duration_min,
            "duration_max": duration_max,
            "page": page,
            "limit": limit,
        },
    )


@mcp.tool()
async def get_recording(recording_id: int) -> Any:
    """Get full detail for a single recording by id (transcript, frequency,
    timestamp, mode, signal strength, AI tags)."""
    return await _get(f"/api/v1/files/{recording_id}")


# ---------------------------------------------------------------------------
# Repeaters
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_repeaters(
    state: Optional[str] = None,
    callsign: Optional[str] = None,
    page: int = 1,
    limit: int = 100,
) -> Any:
    """List known repeaters (from the RepeaterBook sync).

    Args:
        state: Optional 2-4 char state/region code.
        callsign: Optional callsign filter.
        page: 1-based page number.
        limit: Results per page (1-500).
    """
    return await _get(
        "/api/v1/repeaters",
        {"state": state, "callsign": callsign, "page": page, "limit": limit},
    )


# ---------------------------------------------------------------------------
# APRS
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_aprs_stations(hours: int = 24) -> Any:
    """List APRS stations heard in the last N hours (1-168) with their latest
    decoded position."""
    return await _get("/api/v1/aprs/stations", {"hours": hours})


@mcp.tool()
async def list_aprs_packets(
    hours: int = 24,
    callsign: Optional[str] = None,
    page: int = 1,
) -> Any:
    """List decoded APRS packets from the last N hours (1-168), newest first.

    Args:
        hours: Lookback window in hours.
        callsign: Optional callsign filter.
        page: 1-based page number.
    """
    return await _get(
        "/api/v1/aprs/packets",
        {"hours": hours, "callsign": callsign, "page": page},
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@mcp.tool()
async def station_stats() -> Any:
    """Overall station statistics: total recordings, counts by mode, top
    frequencies, recent activity summary."""
    return await _get("/api/v1/stats")


@mcp.tool()
async def frequency_stats(frequency_hz: float, tolerance_hz: float = 10000.0) -> Any:
    """Activity stats for a specific frequency.

    Args:
        frequency_hz: Center frequency in Hz.
        tolerance_hz: Match window around the frequency (100-500000 Hz).
    """
    return await _get(
        f"/api/v1/stats/frequency/{frequency_hz}",
        {"tolerance_hz": tolerance_hz},
    )


@mcp.tool()
async def activity_heatmap(days: int = 30) -> Any:
    """Recording-activity heatmap over the last N days (1-90), bucketed by
    hour-of-day and day-of-week."""
    return await _get("/api/v1/stats/activity", {"days": days})


# ---------------------------------------------------------------------------
# Admin / health (read-only subset)
# ---------------------------------------------------------------------------

@mcp.tool()
async def sdr_health() -> Any:
    """Current health of the SDR capture pipeline: per-band decoder status,
    last-heard timestamps, and any stalled/failed hardware."""
    return await _get("/api/v1/admin/sdr-health")


@mcp.tool()
async def storage_status() -> Any:
    """Storage usage for recordings/artifacts and retention info."""
    return await _get("/api/v1/admin/storage")


@mcp.tool()
async def recent_alerts() -> Any:
    """Recent alerts fired by the station (matched callsigns/keywords)."""
    return await _get("/api/v1/admin/alerts")


# ---------------------------------------------------------------------------
# HTTP app: bearer auth + health probe wrapped around the MCP endpoint (/mcp)
# ---------------------------------------------------------------------------

class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if MCP_TOKEN:
            if request.headers.get("authorization", "") != f"Bearer {MCP_TOKEN}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def _healthz(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


app = mcp.streamable_http_app()
app.add_middleware(BearerAuthMiddleware)
app.router.routes.append(Route("/healthz", _healthz, methods=["GET"]))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
