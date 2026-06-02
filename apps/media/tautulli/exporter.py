"""Tautulli Prometheus Exporter — polls the Tautulli API and exposes metrics.

Metrics exposed:
  tautulli_up                          1 if API reachable, 0 otherwise
  tautulli_active_streams              Total active streams
  tautulli_streams_direct_play         Direct-play stream count
  tautulli_streams_direct_stream       Direct-stream count
  tautulli_streams_transcode           Transcode stream count
  tautulli_bandwidth_kbps              Bandwidth by location (lan/wan/total)
  tautulli_library_items               Item count per library
  tautulli_stream_info                 Per-stream gauge (1 per active stream)
  tautulli_total_plays                 Lifetime play count from home stats
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from prometheus_client import Gauge, Info, start_http_server

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TAUTULLI_URL = os.environ.get(
    "TAUTULLI_URL", "http://tautulli.tautulli.svc.cluster.local:8181"
).rstrip("/")
TAUTULLI_API_KEY = os.environ["TAUTULLI_API_KEY"]
LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", "9487"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

UP = Gauge("tautulli_up", "1 if the Tautulli API is reachable")

ACTIVE_STREAMS = Gauge("tautulli_active_streams", "Total active streams")
STREAMS_DIRECT_PLAY = Gauge("tautulli_streams_direct_play", "Direct play streams")
STREAMS_DIRECT_STREAM = Gauge("tautulli_streams_direct_stream", "Direct stream count")
STREAMS_TRANSCODE = Gauge("tautulli_streams_transcode", "Transcode streams")

BANDWIDTH = Gauge("tautulli_bandwidth_kbps", "Bandwidth in kbps", ["location"])

LIBRARY_ITEMS = Gauge(
    "tautulli_library_items", "Item count per library",
    ["library_name", "section_type"],
)

STREAM_INFO = Gauge(
    "tautulli_stream_info", "Active stream (gauge=1 per stream)",
    ["user", "title", "player", "quality", "transcode_decision",
     "media_type", "library_name", "video_resolution", "state"],
)

TOTAL_DURATION = Gauge(
    "tautulli_total_duration_seconds", "Total watch duration from home stats",
    ["stat_type"],
)

SERVER_INFO = Info("tautulli_server", "Plex/Tautulli server metadata")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _api(cmd: str, **params: Any) -> dict | None:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{TAUTULLI_URL}/api/v2?apikey={TAUTULLI_API_KEY}&cmd={cmd}"
    if qs:
        url += f"&{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data.get("response", {}).get("result") == "success":
            return data["response"]["data"]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        print(f"[exporter] API error ({cmd}): {exc}", flush=True)
    return None


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_activity() -> None:
    data = _api("get_activity")
    if data is None:
        UP.set(0)
        return
    UP.set(1)

    ACTIVE_STREAMS.set(int(data.get("stream_count", 0)))
    STREAMS_DIRECT_PLAY.set(int(data.get("stream_count_direct_play", 0)))
    STREAMS_DIRECT_STREAM.set(int(data.get("stream_count_direct_stream", 0)))
    STREAMS_TRANSCODE.set(int(data.get("stream_count_transcode", 0)))

    BANDWIDTH.labels(location="total").set(int(data.get("total_bandwidth", 0)))
    BANDWIDTH.labels(location="lan").set(int(data.get("lan_bandwidth", 0)))
    BANDWIDTH.labels(location="wan").set(int(data.get("wan_bandwidth", 0)))

    # Clear old stream_info gauges, then set current ones.
    STREAM_INFO._metrics.clear()
    for s in data.get("sessions", []):
        STREAM_INFO.labels(
            user=s.get("friendly_name") or s.get("user", "?"),
            title=s.get("full_title") or s.get("title", "?"),
            player=s.get("player", "?"),
            quality=s.get("quality_profile", "?"),
            transcode_decision=s.get("transcode_decision", "direct play"),
            media_type=s.get("media_type", "?"),
            library_name=s.get("library_name", "?"),
            video_resolution=s.get("stream_video_full_resolution") or s.get("video_full_resolution", "?"),
            state=s.get("state", "?"),
        ).set(1)


def collect_libraries() -> None:
    data = _api("get_libraries")
    if data is None:
        return
    LIBRARY_ITEMS._metrics.clear()
    for lib in data:
        LIBRARY_ITEMS.labels(
            library_name=lib.get("section_name", "?"),
            section_type=lib.get("section_type", "?"),
        ).set(int(lib.get("count", 0)))


def collect_home_stats() -> None:
    data = _api("get_home_stats", stat_id="duration", stats_count="1",
                time_range="30")
    if data is None:
        return
    for stat in data if isinstance(data, list) else [data]:
        stat_id = stat.get("stat_id", "?")
        total = sum(int(r.get("total_duration", 0)) for r in stat.get("rows", []))
        TOTAL_DURATION.labels(stat_type=stat_id).set(total)


def collect_server_info() -> None:
    si = _api("get_server_info")
    ti = _api("get_tautulli_info")
    info = {}
    if si:
        info["plex_version"] = si.get("pms_version", "?")
        info["plex_platform"] = si.get("pms_platform", "?")
        info["plex_name"] = si.get("pms_name", "?")
    if ti:
        info["tautulli_version"] = ti.get("tautulli_version", "?")
    if info:
        SERVER_INFO.info(info)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print(
        f"[exporter] Tautulli Prometheus Exporter starting "
        f"url={TAUTULLI_URL} port={LISTEN_PORT} interval={POLL_INTERVAL}s",
        flush=True,
    )
    start_http_server(LISTEN_PORT)
    collect_server_info()

    while True:
        try:
            collect_activity()
            collect_libraries()
            collect_home_stats()
        except Exception as exc:
            print(f"[exporter] collection error: {exc}", flush=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
