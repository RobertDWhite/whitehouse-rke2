"""Bounded, live media tools for the private Hermes Matrix gateway."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import json
import logging
import os
from pathlib import Path
import re
import ssl
import stat
from typing import Literal

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
import uvicorn


UPSTREAM = "http://media-mcp.media-mcp.svc.cluster.local:8080/mcp"
KUBE_API = "https://kubernetes.default.svc.cluster.local"
KUBE_AUTH = Path("/var/run/secrets/kubernetes.io/serviceaccount")
NAS = Path("/nas")
MEDIA_NAMESPACES = Literal[
    "media", "media-mcp", "calibre", "tautulli", "audiobookshelf", "kavita",
    "dispatcharr", "jellystat", "clean-audio-mcp", "immich", "home-videos",
]
AREAS = {
    "radarr-complete": "media/.complete/radarr",
    "sonarr-complete": "media/.complete/tv-sonarr",
    "movies": "media/Movies",
    "tv": "media/TV Shows",
    "documentary-movies": "media/Documentaries (Movies)",
    "documentary-series": "media/Documentaries (Series)",
    "music": "media/Music, Plays, Ballet",
    "qbittorrent": "downloads/qbittorrent",
    "downloads-complete": "downloads/complete",
    "download-station": "downloads/download-station",
    "jdownloader": "downloads/jdownloader",
    "sabnzbd": "downloads/sabnzbd",
    "metube": "downloads/metube",
    "readarr-downloads": "downloads/readarr",
    "readarr-books": "books/readarr",
    "books": "books/Books",
    "audiobooks": "books/Audiobooks",
    "calibre": "books/Calibre",
}
READ_TOOLS = frozenset({
    "radarr_search", "radarr_list", "radarr_queue", "radarr_wanted",
    "sonarr_search", "sonarr_list", "sonarr_queue", "sonarr_wanted",
    "prowlarr_indexers", "prowlarr_stats", "prowlarr_search",
    "readarr_authors", "readarr_search", "readarr_queue",
    "bazarr_status", "bazarr_wanted", "bazarr_providers",
    "lazylibrarian_authors", "lazylibrarian_search_book", "lazylibrarian_search_author",
    "lazylibrarian_wanted", "lazylibrarian_history",
    "plex_libraries", "plex_sessions", "plex_recently_added", "plex_search",
    "tautulli_activity", "tautulli_history", "tautulli_home_stats", "tautulli_recently_added",
})
ACTION_TOOLS = frozenset({
    "radarr_add", "radarr_force_search", "sonarr_add", "sonarr_force_search",
    "lazylibrarian_add_book", "lazylibrarian_add_author", "lazylibrarian_force_search",
    "plex_scan_library",
})
LIST_RESULTS = frozenset({
    "radarr_search", "radarr_list", "radarr_queue", "radarr_wanted", "sonarr_search",
    "sonarr_list", "sonarr_queue", "sonarr_wanted", "prowlarr_indexers", "prowlarr_search",
    "readarr_authors", "readarr_search", "readarr_queue", "lazylibrarian_authors",
    "lazylibrarian_search_book", "lazylibrarian_search_author", "lazylibrarian_wanted",
    "lazylibrarian_history", "plex_libraries", "plex_sessions", "plex_recently_added",
    "plex_search", "tautulli_history", "tautulli_home_stats", "tautulli_recently_added",
})
ADD_TOOLS = frozenset({"radarr_add", "sonarr_add", "lazylibrarian_add_book"})
SCAN_BUDGET = 2_000
MAX_ITEMS = 40
RESULT_BUDGET = 18_000
LINK = re.compile(r"(?:https?://|magnet:)[^\s<>]+", re.IGNORECASE)
SECRET_FIELD = re.compile(r"token|api.?key|passw|credential|authorization|secret|cookie", re.IGNORECASE)
log = logging.getLogger("hermes-media-context")
mcp = Server("hermes-media-context")


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkloadInput(ToolInput):
    namespace: MEDIA_NAMESPACES = "media"


class InventoryInput(ToolInput):
    area: Literal[tuple(AREAS)]
    query: str = Field(default="", max_length=160)
    folder: str = Field(default="", max_length=255)
    limit: int = Field(default=12, ge=1, le=25)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def within_result_budget(value):
    if isinstance(value, dict):
        safe = {
            key: within_result_budget(item)
            for key, item in list(value.items())[:60]
            if isinstance(key, str) and not SECRET_FIELD.search(key)
        }
        if "error" in safe and isinstance(safe["error"], str):
            safe["error"] = "Backend reported an error; details withheld."
        return safe
    if isinstance(value, list):
        return [within_result_budget(item) for item in value[:MAX_ITEMS]]
    if isinstance(value, str):
        return LINK.sub("[link withheld]", value)[:500]
    return value


def result(value, *, error=False):
    if isinstance(value, list) and len(value) > MAX_ITEMS:
        value = {"items": value[:MAX_ITEMS], "source_count": len(value), "truncated": True}
    rendered = json.dumps(within_result_budget(value), ensure_ascii=True)
    if len(rendered) > RESULT_BUDGET:
        rendered = json.dumps({"truncated": True, "preview": rendered[:RESULT_BUDGET]})
    return types.CallToolResult(content=[types.TextContent(type="text", text=rendered)], isError=error)


def read_inventory(area, query="", folder="", limit=12):
    if folder and (folder.startswith(".") or "/" in folder or "\\" in folder or "\x00" in folder):
        raise ValueError("folder must be one direct child, not a path")
    root = NAS / AREAS[area]
    flags = os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY
    candidates = []
    scanned = 0
    truncated = False
    match = query.casefold()
    mount_root = NAS / AREAS[area].split("/", 1)[0]
    fd = os.open(mount_root, flags)
    try:
        for part in (*root.relative_to(mount_root).parts, *((folder,) if folder else ())):
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        with os.scandir(fd) as entries:
            for entry in entries:
                if scanned == SCAN_BUDGET:
                    truncated = True
                    break
                scanned += 1
                if entry.name.startswith(".") or match not in entry.name.casefold():
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                is_dir = stat.S_ISDIR(metadata.st_mode)
                if not is_dir and not stat.S_ISREG(metadata.st_mode):
                    continue
                candidates.append((metadata.st_mtime, {
                    "name": entry.name,
                    "kind": "directory" if is_dir else "file",
                    "bytes": None if is_dir else metadata.st_size,
                    "modified_at": datetime.fromtimestamp(metadata.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                }))
    finally:
        os.close(fd)
    candidates.sort(key=lambda item: item[0], reverse=True)
    return {
        "area": area, "folder": folder or None, "observed_at": now(),
        "entries_examined": scanned, "matches_in_scan": len(candidates), "scan_truncated": truncated,
        "entries": [item for _, item in candidates[:limit]],
    }


def read_storage():
    volumes = {}
    for share in ("media", "books", "downloads"):
        try:
            fs = os.statvfs(NAS / share)
            volumes[share] = {
                "total_bytes": fs.f_blocks * fs.f_frsize,
                "available_bytes": fs.f_bavail * fs.f_frsize,
                "read_only": bool(fs.f_flag & os.ST_RDONLY),
            }
        except OSError:
            volumes[share] = {"status": "unavailable"}
    return {"observed_at": now(), "volumes": volumes, "inventory_areas": list(AREAS)}


async def kube_list(group, namespace, resource, **params):
    token = (KUBE_AUTH / "token").read_text().strip()
    tls = ssl.create_default_context(cafile=KUBE_AUTH / "ca.crt")
    async with httpx.AsyncClient(verify=tls, timeout=8, trust_env=False) as client:
        response = await client.get(
            f"{KUBE_API}/{group}/namespaces/{namespace}/{resource}",
            params={"limit": 100, **params}, headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        data = response.json()
        return data["items"], bool(data.get("metadata", {}).get("continue"))


def summarize_workload(resource, item):
    status = item.get("status", {})
    spec = item.get("spec", {})
    summary = {"name": item["metadata"]["name"]}
    if resource == "pods":
        summary.update(phase=status.get("phase"), reason=status.get("reason"), node=spec.get("nodeName"))
        summary["containers"] = [
            {
                "name": container["name"], "ready": container.get("ready"),
                "restarts": container.get("restartCount"),
                "reason": (container.get("state", {}).get("waiting") or container.get("state", {}).get("terminated") or {}).get("reason"),
            }
            for container in status.get("containerStatuses", [])
        ]
    elif resource == "cronjobs":
        summary.update(suspended=spec.get("suspend", False), last_succeeded=status.get("lastSuccessfulTime"),
                       last_scheduled=status.get("lastScheduleTime"), active=len(status.get("active", [])))
    elif resource == "jobs":
        summary.update(active=status.get("active", 0), failed=status.get("failed", 0),
                       succeeded=status.get("succeeded", 0), completion_time=status.get("completionTime"))
    else:
        summary.update(desired=status.get("desiredNumberScheduled", spec.get("replicas", 1)),
                       ready=status.get("numberReady", status.get("readyReplicas", 0)))
    return summary


async def workloads(namespace):
    resources = {
        "pods": "api/v1",
        "deployments": "apis/apps/v1", "statefulsets": "apis/apps/v1", "daemonsets": "apis/apps/v1",
        "cronjobs": "apis/batch/v1", "jobs": "apis/batch/v1",
    }

    async def collection(resource, group):
        items, truncated = await kube_list(group, namespace, resource)
        summaries = [summarize_workload(resource, item) for item in items]
        if resource == "pods":
            summaries.sort(key=lambda pod: (pod["phase"] == "Running" and all(
                container["ready"] for container in pod["containers"]), pod["name"]))
        return {"items": summaries[:MAX_ITEMS], "truncated": truncated or len(summaries) > MAX_ITEMS}

    observations = await asyncio.gather(
        *(collection(resource, group) for resource, group in resources.items()), return_exceptions=True,
    )
    return {"namespace": namespace, "observed_at": now(), **{
        resource: report if not isinstance(report, Exception) else {"status": "unavailable"}
        for resource, report in zip(resources, observations, strict=True)
    }}


async def filebot_status():
    items, _ = await kube_list("apis/apps/v1", "media", "deployments", fieldSelector="metadata.name=filebot")
    if not items:
        return {"deployment": "filebot", "status": "not found"}
    deployment = items[0]
    container = next(item for item in deployment["spec"]["template"]["spec"]["containers"] if item["name"] == "filebot")
    settings = {
        item["name"]: item["value"] for item in container.get("env", [])
        if item["name"] in {"AMC_INPUT_DIR", "AMC_ACTION", "AMC_CONFLICT", "AMC_INTERVAL"} and "value" in item
    }
    input_dir = settings.get("AMC_INPUT_DIR")
    folders = {"/output/complete": NAS / "media/complete", "/output/.complete": NAS / "media/.complete"}
    expected = NAS / "media/.complete"
    matches_handoff = None
    if input_dir in folders:
        try:
            matches_handoff = await asyncio.to_thread(folders[input_dir].samefile, expected)
        except OSError:
            matches_handoff = False
    return {
        "observed_at": now(), "source": "live Kubernetes Deployment and Synology NFS",
        **summarize_workload("deployments", deployment), "image": container["image"],
        "amc_settings": settings, "input_matches_arr_handoff": matches_handoff,
        "handoff_areas": ["radarr-complete", "sonarr-complete"],
        "note": "Replica readiness and path mapping do not prove an import succeeded.",
    }


@asynccontextmanager
async def upstream():
    token = os.environ["UPSTREAM_TOKEN"].strip()
    if not token:
        raise RuntimeError("Missing broker credential")
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=35, trust_env=False) as http:
        async with streamable_http_client(UPSTREAM, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call_upstream(session, name, arguments=None):
    response = await session.call_tool(name, arguments or {})
    if response.isError:
        raise RuntimeError("Media backend call failed")
    if response.structuredContent is not None:
        value = response.structuredContent
        return value["result"] if value.keys() == {"result"} else value
    values = []
    for block in response.content:
        if not isinstance(block, types.TextContent):
            raise RuntimeError("Unexpected upstream result format")
        try:
            values.append(json.loads(block.text))
        except json.JSONDecodeError:
            values.append(block.text)
    if name in LIST_RESULTS or len(values) != 1:
        return values[0] if len(values) == 1 and isinstance(values[0], list) else values
    return values[0]


async def overview():
    async def backends():
        async with upstream() as session:
            summary = await call_upstream(session, "media_overview")
            summary.pop("filebot", None)
            try:
                indexers = await call_upstream(session, "prowlarr_indexers")
                summary["prowlarr"] = {"configured_indexers": len(indexers)}
            except Exception:
                summary["prowlarr"] = {"status": "unavailable"}
            return summary

    app_status, filebot, storage = await asyncio.gather(
        backends(), filebot_status(), asyncio.to_thread(read_storage), return_exceptions=True,
    )
    return {"observed_at": now(), **{
        name: report if not isinstance(report, Exception) else {"status": "unavailable"}
        for name, report in (("apps", app_status), ("filebot", filebot), ("nas", storage))
    }}


LOCAL_TOOLS = {
    "media_overview": ("Live overview of the media apps, cluster FileBot and Synology mounts.", ToolInput, overview),
    "media_workloads": ("List Kubernetes pods and workload readiness in one media namespace. No logs or writes.", WorkloadInput, workloads),
    "filebot_status": ("Read the live cluster FileBot AMC settings and check its Synology handoff mapping.", ToolInput, filebot_status),
    "nas_storage": ("Synology free space, read-only mount state and available inventory area names.", ToolInput, lambda: asyncio.to_thread(read_storage)),
    "nas_inventory": ("List bounded file metadata for a known media, handoff or downloads area. An optional folder must be one direct child; no content is read.", InventoryInput, lambda **args: asyncio.to_thread(read_inventory, **args)),
}


@mcp.list_tools()
async def list_tools():
    tools = [types.Tool(name=name, description=description, inputSchema=model.model_json_schema(),
                        annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False))
             for name, (description, model, _) in LOCAL_TOOLS.items()]
    try:
        async with upstream() as session:
            discovered = await session.list_tools()
            for tool in discovered.tools:
                if tool.name not in READ_TOOLS | ACTION_TOOLS:
                    continue
                changes_state = tool.name in ACTION_TOOLS
                description = tool.description or tool.name
                schema = tool.inputSchema
                if changes_state:
                    description = f"Use only for a specific, explicit operator request. {description}"
                if tool.name in ADD_TOOLS:
                    properties = dict(schema.get("properties", {}))
                    properties.pop("root_folder", None)
                    properties["search"] = {"type": "boolean", "default": False, "description": "Also start a search, only if explicitly requested."}
                    schema = {**schema, "properties": properties}
                    description = "Add using the configured destination. Do not start a search unless explicitly requested."
                tools.append(tool.model_copy(update={
                    "description": description, "inputSchema": schema, "outputSchema": None,
                    "annotations": types.ToolAnnotations(readOnlyHint=not changes_state, destructiveHint=False),
                }))
    except Exception:
        log.warning("Upstream MCP catalog unavailable; serving local tools only")
    return tools


@mcp.call_tool(validate_input=False)
async def call_tool(name, arguments):
    if name not in LOCAL_TOOLS and name not in READ_TOOLS | ACTION_TOOLS:
        return result({"status": "tool not allowed"}, error=True)
    try:
        if name in LOCAL_TOOLS:
            _, model, handler = LOCAL_TOOLS[name]
            kwargs = model.model_validate(arguments).model_dump()
            return result(await handler(**kwargs))
        arguments = dict(arguments)
        if name == "plex_scan_library" and not re.fullmatch(r"[1-9][0-9]{0,8}", str(arguments.get("section_key", ""))):
            return result({"status": "section_key must be a numeric Plex library ID"}, error=True)
        if name in ADD_TOOLS:
            if "root_folder" in arguments:
                return result({"status": "only the configured library destination is allowed"}, error=True)
            arguments.setdefault("search", False)
        if len(json.dumps(arguments)) > 4_000:
            return result({"status": "arguments exceed size limit"}, error=True)
        for key in {"limit", "length", "count"} & arguments.keys():
            if type(arguments[key]) is not int or not 1 <= arguments[key] <= MAX_ITEMS:
                return result({"status": f"{key} must be an integer from 1 to {MAX_ITEMS}"}, error=True)
        async with upstream() as session:
            return result(await call_upstream(session, name, arguments))
    except (ValidationError, ValueError):
        return result({"status": "invalid tool arguments"}, error=True)
    except FileNotFoundError:
        return result({"status": "directory or file not found"}, error=True)
    except PermissionError:
        return result({"status": "read access denied"}, error=True)
    except Exception:
        log.warning("Media tool %s failed", name)
        return result({"status": "source unavailable or action failed; verify before retrying"}, error=True)


manager = StreamableHTTPSessionManager(
    mcp, json_response=True, stateless=True, max_request_body_size=32_768,
    security_settings=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["hermes-media-context.media-mcp.svc.cluster.local:8080", "127.0.0.1:8080"],
    ),
)


class MCPRoute:
    async def __call__(self, scope, receive, send):
        presented = Headers(scope=scope).get("authorization", "").encode("latin-1")
        expected = f"Bearer {os.environ['HERMES_MEDIA_CONTEXT_TOKEN']}".encode()
        if not hmac.compare_digest(presented, expected):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)


async def health(_request):
    return PlainTextResponse("ok")


@asynccontextmanager
async def lifespan(_app):
    if len(os.environ.get("HERMES_MEDIA_CONTEXT_TOKEN", "")) < 32:
        raise RuntimeError("Missing or invalid context credential")
    async with manager.run():
        yield


app = Starlette(routes=[Route("/mcp", MCPRoute()), Route("/healthz", health)], lifespan=lifespan)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
