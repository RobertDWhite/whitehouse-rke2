"""media-mcp — a unified MCP server over the media stack.

One FastMCP streamable-HTTP server that "joins" the *arr suite, Plex and
Tautulli behind a single bearer-gated endpoint so an LLM can run cross-tool
workflows (look up a movie -> add it to Radarr -> force a Prowlarr-backed
search -> trigger a Plex scan -> confirm it landed).

Backends:
  - Radarr   (movies)      Servarr v3 API, header X-Api-Key
  - Sonarr   (TV)          Servarr v3 API, header X-Api-Key
  - Prowlarr (indexers)    Servarr v1 API, header X-Api-Key
  - Readarr  (books)       Servarr v1 API, header X-Api-Key
  - Bazarr   (subtitles)   custom API,      header X-API-KEY
  - Plex     (player)      HTTP API :32400, header X-Plex-Token
  - Tautulli (stats)       /api/v2?apikey=&cmd=

Auth (Claude -> this server): static bearer MCP_TOKEN. /healthz is open.
Auth (this server -> backends): per-backend API key / token from the secret.

All backend URLs are wired as plain env in the Deployment; only the keys and
the inbound MCP_TOKEN come from the SOPS secret. A missing key simply disables
that backend's tools (they return an error string rather than crashing).
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

MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

# (base_url env, key env, api version path). version "" means a non-Servarr API.
_SERVARR = {
    "radarr": ("RADARR_URL", "RADARR_KEY", "v3"),
    "sonarr": ("SONARR_URL", "SONARR_KEY", "v3"),
    "prowlarr": ("PROWLARR_URL", "PROWLARR_KEY", "v1"),
    "readarr": ("READARR_URL", "READARR_KEY", "v1"),
}

PLEX_URL = os.environ.get("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
TAUTULLI_URL = os.environ.get("TAUTULLI_URL", "").rstrip("/")
TAUTULLI_KEY = os.environ.get("TAUTULLI_KEY", "")
BAZARR_URL = os.environ.get("BAZARR_URL", "").rstrip("/")
BAZARR_KEY = os.environ.get("BAZARR_KEY", "")
LAZYLIBRARIAN_URL = os.environ.get("LAZYLIBRARIAN_URL", "").rstrip("/")
LAZYLIBRARIAN_KEY = os.environ.get("LAZYLIBRARIAN_KEY", "")

mcp = FastMCP(
    "media",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)


# ---------------------------------------------------------------------------
# Backend request helpers
# ---------------------------------------------------------------------------

async def _arr(app: str, method: str, path: str, **kw) -> Any:
    """Call a Servarr-family API. Raises RuntimeError on missing config."""
    url_env, key_env, ver = _SERVARR[app]
    base = os.environ.get(url_env, "").rstrip("/")
    key = os.environ.get(key_env, "")
    if not base or not key:
        raise RuntimeError(f"{app} is not configured (set {url_env}/{key_env})")
    r = await _client.request(
        method,
        f"{base}/api/{ver}{path}",
        headers={"X-Api-Key": key},
        **kw,
    )
    r.raise_for_status()
    return r.json() if r.content else None


async def _bazarr(method: str, path: str, **kw) -> Any:
    if not BAZARR_URL or not BAZARR_KEY:
        raise RuntimeError("bazarr is not configured (set BAZARR_URL/BAZARR_KEY)")
    r = await _client.request(
        method, f"{BAZARR_URL}/api{path}", headers={"X-API-KEY": BAZARR_KEY}, **kw
    )
    r.raise_for_status()
    return r.json() if r.content else None


async def _lazylibrarian(cmd: str, **params) -> Any:
    """Call the LazyLibrarian API: GET /api?apikey=&cmd=. DB-read commands return
    CamelCase keys; source-search commands return lowercase keys."""
    if not LAZYLIBRARIAN_URL or not LAZYLIBRARIAN_KEY:
        raise RuntimeError("lazylibrarian is not configured (set LAZYLIBRARIAN_URL/LAZYLIBRARIAN_KEY)")
    r = await _client.get(
        f"{LAZYLIBRARIAN_URL}/api",
        params={"apikey": LAZYLIBRARIAN_KEY, "cmd": cmd, **params},
    )
    r.raise_for_status()
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text.strip()


async def _plex(path: str, params: Optional[dict] = None, method: str = "GET") -> Any:
    if not PLEX_URL or not PLEX_TOKEN:
        raise RuntimeError("plex is not configured (set PLEX_URL/PLEX_TOKEN)")
    p = {"X-Plex-Token": PLEX_TOKEN, **(params or {})}
    r = await _client.request(
        method, f"{PLEX_URL}{path}", params=p, headers={"Accept": "application/json"}
    )
    r.raise_for_status()
    if not r.content:
        return None
    try:
        return r.json().get("MediaContainer", r.json())
    except ValueError:
        return r.text


async def _tautulli(cmd: str, **params) -> Any:
    if not TAUTULLI_URL or not TAUTULLI_KEY:
        raise RuntimeError("tautulli is not configured (set TAUTULLI_URL/TAUTULLI_KEY)")
    r = await _client.get(
        f"{TAUTULLI_URL}/api/v2",
        params={"apikey": TAUTULLI_KEY, "cmd": cmd, **params},
    )
    r.raise_for_status()
    return r.json().get("response", {}).get("data")


async def _defaults(app: str) -> tuple[int, str]:
    """First quality profile id and root folder path for an *arr (add defaults)."""
    profiles = await _arr(app, "GET", "/qualityprofile")
    roots = await _arr(app, "GET", "/rootfolder")
    return profiles[0]["id"], roots[0]["path"]


def _trim_movie(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "title": m.get("title"),
        "year": m.get("year"),
        "tmdbId": m.get("tmdbId"),
        "monitored": m.get("monitored"),
        "hasFile": m.get("hasFile"),
        "status": m.get("status"),
        "sizeOnDisk": m.get("sizeOnDisk"),
    }


def _trim_series(s: dict) -> dict:
    stats = s.get("statistics", {})
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "year": s.get("year"),
        "tvdbId": s.get("tvdbId"),
        "monitored": s.get("monitored"),
        "status": s.get("status"),
        "episodeFileCount": stats.get("episodeFileCount"),
        "episodeCount": stats.get("episodeCount"),
        "sizeOnDisk": stats.get("sizeOnDisk"),
    }


# ---------------------------------------------------------------------------
# Radarr (movies)
# ---------------------------------------------------------------------------

@mcp.tool()
async def radarr_search(term: str) -> Any:
    """Look up movies by title (TMDB search via Radarr). Returns candidates with
    tmdbId — pass a tmdbId to radarr_add to add one."""
    res = await _arr("radarr", "GET", "/movie/lookup", params={"term": term})
    return [
        {"title": m.get("title"), "year": m.get("year"), "tmdbId": m.get("tmdbId"),
         "overview": (m.get("overview") or "")[:280]}
        for m in res[:15]
    ]


@mcp.tool()
async def radarr_list(query: Optional[str] = None, monitored_only: bool = False) -> Any:
    """List the Radarr library. Optional case-insensitive title substring filter."""
    movies = await _arr("radarr", "GET", "/movie")
    out = []
    for m in movies:
        if monitored_only and not m.get("monitored"):
            continue
        if query and query.lower() not in (m.get("title") or "").lower():
            continue
        out.append(_trim_movie(m))
    return out


@mcp.tool()
async def radarr_add(
    tmdb_id: int,
    quality_profile_id: Optional[int] = None,
    root_folder: Optional[str] = None,
    search: bool = True,
) -> Any:
    """Add a movie to Radarr by tmdbId and (by default) immediately search for it.
    Uses the first quality profile / root folder if none given."""
    lookup = await _arr("radarr", "GET", "/movie/lookup", params={"term": f"tmdb:{tmdb_id}"})
    if not lookup:
        return {"error": f"no movie found for tmdbId {tmdb_id}"}
    movie = lookup[0]
    if quality_profile_id is None or root_folder is None:
        qp, rf = await _defaults("radarr")
        quality_profile_id = quality_profile_id or qp
        root_folder = root_folder or rf
    movie.update({
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder,
        "monitored": True,
        "addOptions": {"searchForMovie": search},
    })
    return _trim_movie(await _arr("radarr", "POST", "/movie", json=movie))


@mcp.tool()
async def radarr_force_search(movie_id: int) -> Any:
    """Trigger a search for an existing Radarr movie (MoviesSearch command)."""
    return await _arr("radarr", "POST", "/command",
                      json={"name": "MoviesSearch", "movieIds": [movie_id]})


@mcp.tool()
async def radarr_delete(movie_id: int, delete_files: bool = False) -> Any:
    """Remove a movie from Radarr. delete_files also removes it from disk."""
    await _arr("radarr", "DELETE", f"/movie/{movie_id}",
               params={"deleteFiles": str(delete_files).lower()})
    return {"deleted": movie_id, "files_removed": delete_files}


@mcp.tool()
async def radarr_queue() -> Any:
    """Current Radarr download queue (active grabs)."""
    q = await _arr("radarr", "GET", "/queue", params={"includeMovie": "true"})
    return [
        {"movie": (r.get("movie") or {}).get("title"), "status": r.get("status"),
         "size": r.get("size"), "sizeleft": r.get("sizeleft"),
         "timeleft": r.get("timeleft"), "errorMessage": r.get("errorMessage")}
        for r in q.get("records", [])
    ]


@mcp.tool()
async def radarr_wanted() -> Any:
    """Monitored movies that are missing a file."""
    w = await _arr("radarr", "GET", "/wanted/missing",
                   params={"pageSize": "100", "sortKey": "title"})
    return [_trim_movie(m) for m in w.get("records", [])]


# ---------------------------------------------------------------------------
# Sonarr (TV)
# ---------------------------------------------------------------------------

@mcp.tool()
async def sonarr_search(term: str) -> Any:
    """Look up series by title (TVDB search via Sonarr). Returns candidates with
    tvdbId — pass a tvdbId to sonarr_add."""
    res = await _arr("sonarr", "GET", "/series/lookup", params={"term": term})
    return [
        {"title": s.get("title"), "year": s.get("year"), "tvdbId": s.get("tvdbId"),
         "status": s.get("status"), "overview": (s.get("overview") or "")[:280]}
        for s in res[:15]
    ]


@mcp.tool()
async def sonarr_list(query: Optional[str] = None, monitored_only: bool = False) -> Any:
    """List the Sonarr library. Optional case-insensitive title substring filter."""
    series = await _arr("sonarr", "GET", "/series")
    out = []
    for s in series:
        if monitored_only and not s.get("monitored"):
            continue
        if query and query.lower() not in (s.get("title") or "").lower():
            continue
        out.append(_trim_series(s))
    return out


@mcp.tool()
async def sonarr_add(
    tvdb_id: int,
    quality_profile_id: Optional[int] = None,
    root_folder: Optional[str] = None,
    search: bool = True,
) -> Any:
    """Add a series to Sonarr by tvdbId, monitor all seasons, and (by default)
    search for missing episodes. Uses first quality profile / root folder if none given."""
    lookup = await _arr("sonarr", "GET", "/series/lookup", params={"term": f"tvdb:{tvdb_id}"})
    if not lookup:
        return {"error": f"no series found for tvdbId {tvdb_id}"}
    series = lookup[0]
    if quality_profile_id is None or root_folder is None:
        qp, rf = await _defaults("sonarr")
        quality_profile_id = quality_profile_id or qp
        root_folder = root_folder or rf
    series.update({
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder,
        "monitored": True,
        "addOptions": {"searchForMissingEpisodes": search},
    })
    return _trim_series(await _arr("sonarr", "POST", "/series", json=series))


@mcp.tool()
async def sonarr_force_search(series_id: int) -> Any:
    """Trigger a search for an existing Sonarr series (SeriesSearch command)."""
    return await _arr("sonarr", "POST", "/command",
                      json={"name": "SeriesSearch", "seriesId": series_id})


@mcp.tool()
async def sonarr_delete(series_id: int, delete_files: bool = False) -> Any:
    """Remove a series from Sonarr. delete_files also removes it from disk."""
    await _arr("sonarr", "DELETE", f"/series/{series_id}",
               params={"deleteFiles": str(delete_files).lower()})
    return {"deleted": series_id, "files_removed": delete_files}


@mcp.tool()
async def sonarr_queue() -> Any:
    """Current Sonarr download queue (active grabs)."""
    q = await _arr("sonarr", "GET", "/queue", params={"includeSeries": "true"})
    return [
        {"series": (r.get("series") or {}).get("title"), "status": r.get("status"),
         "size": r.get("size"), "sizeleft": r.get("sizeleft"),
         "timeleft": r.get("timeleft"), "errorMessage": r.get("errorMessage")}
        for r in q.get("records", [])
    ]


@mcp.tool()
async def sonarr_wanted() -> Any:
    """Monitored episodes that are missing a file."""
    w = await _arr("sonarr", "GET", "/wanted/missing",
                   params={"pageSize": "100", "sortKey": "series.title"})
    return [
        {"series": (r.get("series") or {}).get("title"),
         "season": r.get("seasonNumber"), "episode": r.get("episodeNumber"),
         "title": r.get("title"), "airDate": r.get("airDate")}
        for r in w.get("records", [])
    ]


# ---------------------------------------------------------------------------
# Prowlarr (indexers)
# ---------------------------------------------------------------------------

@mcp.tool()
async def prowlarr_indexers() -> Any:
    """List configured Prowlarr indexers with enabled state."""
    idx = await _arr("prowlarr", "GET", "/indexer")
    return [
        {"id": i.get("id"), "name": i.get("name"), "enable": i.get("enable"),
         "protocol": i.get("protocol"), "privacy": i.get("privacy")}
        for i in idx
    ]


@mcp.tool()
async def prowlarr_stats() -> Any:
    """Per-indexer query/grab/failure stats from Prowlarr."""
    return await _arr("prowlarr", "GET", "/indexerstats")


@mcp.tool()
async def prowlarr_search(query: str, limit: int = 25) -> Any:
    """Run a manual search across all Prowlarr indexers. Returns release candidates."""
    res = await _arr("prowlarr", "GET", "/search",
                     params={"query": query, "type": "search"})
    return [
        {"title": r.get("title"), "indexer": r.get("indexer"),
         "size": r.get("size"), "seeders": r.get("seeders"),
         "protocol": r.get("protocol"), "publishDate": r.get("publishDate")}
        for r in res[:limit]
    ]


# ---------------------------------------------------------------------------
# Readarr (books) — read + force-search; full author/book add is intentionally
# omitted (Readarr's add flow is two-step and brittle).
# ---------------------------------------------------------------------------

@mcp.tool()
async def readarr_authors() -> Any:
    """List authors tracked in Readarr."""
    authors = await _arr("readarr", "GET", "/author")
    return [
        {"id": a.get("id"), "name": a.get("authorName"), "monitored": a.get("monitored"),
         "bookCount": (a.get("statistics") or {}).get("bookCount")}
        for a in authors
    ]


@mcp.tool()
async def readarr_search(term: str) -> Any:
    """Search Readarr's catalog for books/authors by term."""
    res = await _arr("readarr", "GET", "/search", params={"term": term})
    return res[:15]


@mcp.tool()
async def readarr_queue() -> Any:
    """Current Readarr download queue."""
    q = await _arr("readarr", "GET", "/queue")
    return q.get("records", q)


# ---------------------------------------------------------------------------
# Bazarr (subtitles)
# ---------------------------------------------------------------------------

@mcp.tool()
async def bazarr_status() -> Any:
    """Bazarr system status."""
    return await _bazarr("GET", "/system/status")


@mcp.tool()
async def bazarr_wanted() -> Any:
    """Media missing subtitles, both movies and episodes."""
    movies = await _bazarr("GET", "/movies/wanted")
    episodes = await _bazarr("GET", "/episodes/wanted")
    return {"movies": movies, "episodes": episodes}


@mcp.tool()
async def bazarr_providers() -> Any:
    """Configured subtitle providers and their health."""
    return await _bazarr("GET", "/providers")


# ---------------------------------------------------------------------------
# LazyLibrarian (books / ebooks / audiobooks)
# ---------------------------------------------------------------------------

@mcp.tool()
async def lazylibrarian_authors() -> Any:
    """List authors tracked in LazyLibrarian."""
    res = await _lazylibrarian("getIndex")
    return [
        {"id": a.get("AuthorID"), "name": a.get("AuthorName"),
         "status": a.get("Status"), "haveBooks": a.get("HaveBooks"),
         "totalBooks": a.get("TotalBooks")}
        for a in (res or [])
    ]


@mcp.tool()
async def lazylibrarian_search_book(term: str) -> Any:
    """Search configured book sources (GoodReads/GoogleBooks) for a book by title
    or author. Returns candidates with a bookid to pass to lazylibrarian_add_book."""
    res = await _lazylibrarian("findBook", name=term)
    items = res if isinstance(res, list) else (res or {}).get("book", [])
    return [
        {"bookid": b.get("bookid"), "title": b.get("bookname"),
         "author": b.get("authorname"), "date": b.get("bookdate"),
         "source": b.get("source")}
        for b in (items or [])[:15]
    ]


@mcp.tool()
async def lazylibrarian_add_book(book_id: str, search: bool = True) -> Any:
    """Add a book to LazyLibrarian by bookid and (by default) trigger a search."""
    await _lazylibrarian("addBook", id=book_id)
    if search:
        await _lazylibrarian("searchBook", id=book_id)
    return {"added": book_id, "searched": search}


@mcp.tool()
async def lazylibrarian_search_author(term: str) -> Any:
    """Search configured sources for an author by name. Returns candidates with an
    authorid to pass to lazylibrarian_add_author."""
    res = await _lazylibrarian("findAuthor", name=term)
    items = res if isinstance(res, list) else (res or {}).get("authors", [])
    return [
        {"authorid": a.get("authorid"), "name": a.get("authorname"),
         "source": a.get("source")}
        for a in (items or [])[:15]
    ]


@mcp.tool()
async def lazylibrarian_add_author(name: str) -> Any:
    """Add an author to LazyLibrarian by name — monitors and pulls their books."""
    return await _lazylibrarian("addAuthor", name=name)


@mcp.tool()
async def lazylibrarian_wanted() -> Any:
    """Books marked Wanted (missing) in LazyLibrarian."""
    res = await _lazylibrarian("getWanted")
    items = res if isinstance(res, list) else (res or {}).get("books", [])
    return [
        {"bookid": b.get("BookID"), "title": b.get("BookName"),
         "author": b.get("AuthorName"), "status": b.get("Status")}
        for b in (items or [])
    ]


@mcp.tool()
async def lazylibrarian_force_search(book_id: str) -> Any:
    """Force a search for a specific book already tracked in LazyLibrarian (bookid)."""
    return await _lazylibrarian("searchBook", id=book_id)


@mcp.tool()
async def lazylibrarian_history() -> Any:
    """Recent LazyLibrarian snatched/grabbed history."""
    return await _lazylibrarian("getSnatched")


# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------

@mcp.tool()
async def plex_sessions() -> Any:
    """What is playing on Plex right now."""
    mc = await _plex("/status/sessions")
    return [
        {"title": v.get("title"), "type": v.get("type"),
         "user": (v.get("User") or {}).get("title"),
         "player": (v.get("Player") or {}).get("title"),
         "state": (v.get("Player") or {}).get("state"),
         "viewOffset": v.get("viewOffset"), "duration": v.get("duration")}
        for v in (mc or {}).get("Metadata", [])
    ]


@mcp.tool()
async def plex_libraries() -> Any:
    """List Plex library sections (key, title, type)."""
    mc = await _plex("/library/sections")
    return [
        {"key": d.get("key"), "title": d.get("title"), "type": d.get("type")}
        for d in (mc or {}).get("Directory", [])
    ]


@mcp.tool()
async def plex_recently_added(limit: int = 20) -> Any:
    """Most recently added items across all Plex libraries."""
    mc = await _plex("/library/recentlyAdded", params={"X-Plex-Container-Size": limit,
                                                        "X-Plex-Container-Start": 0})
    return [
        {"title": m.get("title"), "type": m.get("type"), "year": m.get("year"),
         "addedAt": m.get("addedAt"), "librarySectionTitle": m.get("librarySectionTitle")}
        for m in (mc or {}).get("Metadata", [])[:limit]
    ]


@mcp.tool()
async def plex_search(query: str) -> Any:
    """Search the Plex library by title."""
    mc = await _plex("/search", params={"query": query})
    return [
        {"title": m.get("title"), "type": m.get("type"), "year": m.get("year"),
         "librarySectionTitle": m.get("librarySectionTitle")}
        for m in (mc or {}).get("Metadata", [])
    ]


@mcp.tool()
async def plex_scan_library(section_key: str) -> Any:
    """Trigger a Plex library scan for the given section key (from plex_libraries).
    Useful right after an *arr import so new files show up."""
    await _plex(f"/library/sections/{section_key}/refresh")
    return {"scanning": section_key}


# ---------------------------------------------------------------------------
# Tautulli (watch history / stats)
# ---------------------------------------------------------------------------

@mcp.tool()
async def tautulli_activity() -> Any:
    """Current Plex activity as reported by Tautulli (streams, bandwidth)."""
    return await _tautulli("get_activity")


@mcp.tool()
async def tautulli_history(length: int = 25, user: Optional[str] = None) -> Any:
    """Recent Plex watch history from Tautulli. Optional user filter."""
    params = {"length": length}
    if user:
        params["user"] = user
    data = await _tautulli("get_history", **params)
    rows = (data or {}).get("data", [])
    return [
        {"title": r.get("full_title"), "user": r.get("user"),
         "started": r.get("started"), "stopped": r.get("stopped"),
         "watched_status": r.get("watched_status"), "platform": r.get("platform")}
        for r in rows
    ]


@mcp.tool()
async def tautulli_home_stats() -> Any:
    """Tautulli home stats: most watched, most active users, popular libraries."""
    return await _tautulli("get_home_stats")


@mcp.tool()
async def tautulli_recently_added(count: int = 20) -> Any:
    """Recently added items per Tautulli (cross-library)."""
    data = await _tautulli("get_recently_added", count=count)
    return (data or {}).get("recently_added", data)


# ---------------------------------------------------------------------------
# Join: one-shot overview across the whole stack
# ---------------------------------------------------------------------------

@mcp.tool()
async def media_overview() -> Any:
    """Cross-stack snapshot: each *arr's health + queue depth, Plex now-playing,
    and Tautulli stream count. Backends that are down/unconfigured report an
    error string instead of failing the whole call."""
    out: dict[str, Any] = {}

    async def safe(label, coro):
        try:
            out[label] = await coro
        except Exception as e:  # noqa: BLE001 — surface, don't crash the join
            out[label] = {"error": str(e)}

    async def _arr_health(app):
        status = await _arr(app, "GET", "/system/status")
        q = await _arr(app, "GET", "/queue", params={"pageSize": "1"})
        return {"version": status.get("version"), "queue": q.get("totalRecords", 0)}

    async def _ll_health():
        idx = await _lazylibrarian("getIndex")
        return {"authors": len(idx) if isinstance(idx, list) else None}

    for app in _SERVARR:
        await safe(app, _arr_health(app))
    await safe("lazylibrarian", _ll_health())
    await safe("plex_now_playing", plex_sessions())
    await safe("tautulli_streams", _tautulli("get_activity"))
    return out


# ---------------------------------------------------------------------------
# HTTP app: bearer auth + health probe around the MCP endpoint (/mcp)
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

    uvicorn.run(app, host="0.0.0.0", port=PORT)
