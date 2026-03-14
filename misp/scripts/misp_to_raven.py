#!/usr/bin/env python3
import asyncio
import collections
import ipaddress
import json
import os
import ssl
import urllib.request

import zmq
import zmq.asyncio
from websockets.exceptions import ConnectionClosed
from websockets.legacy.server import serve

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "5678"))
MISP_API_URL = os.environ.get("MISP_API_URL", "https://nginx.misp.svc.cluster.local").rstrip("/")
MISP_API_KEY = os.environ["MISP_API_KEY"]
MISP_ZMQ_URL = os.environ.get("MISP_ZMQ_URL", "tcp://misp-zmq.misp.svc.cluster.local:50000")
BACKFILL_LIMIT = int(os.environ.get("BACKFILL_LIMIT", "50"))
BACKFILL_INTERVAL_SECONDS = int(os.environ.get("BACKFILL_INTERVAL_SECONDS", "300"))
MESSAGE_TIMEOUT_MS = int(os.environ.get("MESSAGE_TIMEOUT_MS", "3500"))
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "120"))
SEEN_LIMIT = int(os.environ.get("SEEN_LIMIT", "4000"))
MISP_NETWORK_TYPES = [
    item.strip()
    for item in os.environ.get(
        "MISP_NETWORK_TYPES",
        "ip-src,ip-dst,ip-src|port,ip-dst|port,domain|ip,hostname|ip,ip-src|ip-dst",
    ).split(",")
    if item.strip()
]

TYPE_COLORS = {
    "ip-src": "#ef476f",
    "ip-dst": "#ffd166",
    "ip-src|port": "#f4a261",
    "ip-dst|port": "#f4a261",
    "domain|ip": "#06d6a0",
    "hostname|ip": "#4cc9f0",
    "ip-src|ip-dst": "#ff6b6b",
}

CLIENTS = set()
HISTORY = collections.deque(maxlen=HISTORY_LIMIT)
SEEN_QUEUE = collections.deque()
SEEN_SET = set()
SSL_CONTEXT = ssl._create_unverified_context()


def remember(item_id):
    if item_id in SEEN_SET:
        return False
    SEEN_QUEUE.append(item_id)
    SEEN_SET.add(item_id)
    if len(SEEN_QUEUE) > SEEN_LIMIT:
        old = SEEN_QUEUE.popleft()
        SEEN_SET.discard(old)
    return True


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def base_metadata(attr, payload):
    event = payload.get("Event") or attr.get("Event") or {}
    metadata = {
        "Type": attr.get("type", ""),
        "Category": attr.get("category", ""),
        "Event": event.get("info", ""),
        "Event ID": str(attr.get("event_id") or event.get("id", "")),
        "Org": (event.get("Orgc") or {}).get("name", ""),
    }
    if attr.get("comment"):
        metadata["Comment"] = attr["comment"]
    if attr.get("last_seen"):
        metadata["Last seen"] = str(attr["last_seen"])
    elif attr.get("timestamp"):
        metadata["Timestamp"] = str(attr["timestamp"])
    return {key: value for key, value in metadata.items() if value not in ("", None)}


def point_event(ip_value, metadata, color):
    return {
        "function": "table",
        "object": {"from": ip_value, "to": None},
        "color": {"line": {"from": color, "to": color}},
        "timeout": MESSAGE_TIMEOUT_MS,
        "options": ["point", "multi-output", "single-output"],
        "custom": {"from": metadata},
    }


def line_event(src_value, dst_value, src_metadata, dst_metadata, color):
    return {
        "function": "table",
        "object": {"from": src_value, "to": dst_value},
        "color": {"line": {"from": color, "to": color}},
        "timeout": MESSAGE_TIMEOUT_MS,
        "options": ["line", "multi-output", "single-output"],
        "custom": {"from": src_metadata, "to": dst_metadata},
    }


def split_compound_value(attr):
    if attr.get("value1") and attr.get("value2"):
        return str(attr["value1"]), str(attr["value2"])
    value = str(attr.get("value", ""))
    if "|" not in value:
        return value, ""
    left, right = value.split("|", 1)
    return left.strip(), right.strip()


def attribute_to_events(payload):
    attr = payload.get("Attribute")
    if not isinstance(attr, dict):
        return []

    attr_type = str(attr.get("type", "")).lower()
    if attr_type not in MISP_NETWORK_TYPES:
        return []

    attr_id = str(attr.get("id", ""))
    if not attr_id or not remember(attr_id):
        return []

    color = TYPE_COLORS.get(attr_type, "#ffd166")
    metadata = base_metadata(attr, payload)

    if attr_type in {"ip-src", "ip-dst"}:
        value = str(attr.get("value", "")).strip()
        if is_ip(value):
            point_meta = {"IP": value, "Role": attr_type}
            point_meta.update(metadata)
            return [point_event(value, point_meta, color)]
        return []

    if attr_type in {"ip-src|port", "ip-dst|port"}:
        ip_value, port_value = split_compound_value(attr)
        if is_ip(ip_value):
            point_meta = {"IP": ip_value, "Port": port_value, "Role": attr_type}
            point_meta.update(metadata)
            return [point_event(f"{ip_value}:{port_value}" if port_value else ip_value, point_meta, color)]
        return []

    if attr_type in {"domain|ip", "hostname|ip"}:
        indicator, ip_value = split_compound_value(attr)
        if is_ip(ip_value):
            point_meta = {"IP": ip_value, "Indicator": indicator, "Role": attr_type}
            point_meta.update(metadata)
            return [point_event(ip_value, point_meta, color)]
        return []

    if attr_type == "ip-src|ip-dst":
        src_value, dst_value = split_compound_value(attr)
        if is_ip(src_value) and is_ip(dst_value):
            src_meta = {"IP": src_value, "Role": "Source"}
            src_meta.update(metadata)
            dst_meta = {"IP": dst_value, "Role": "Destination"}
            dst_meta.update(metadata)
            return [line_event(src_value, dst_value, src_meta, dst_meta, color)]
    return []


def fetch_recent_attributes():
    body = json.dumps(
        {
            "returnFormat": "json",
            "limit": BACKFILL_LIMIT,
            "sort": "timestamp",
            "direction": "desc",
            "type": MISP_NETWORK_TYPES,
        }
    ).encode()
    request = urllib.request.Request(
        f"{MISP_API_URL}/attributes/restSearch",
        data=body,
        headers={
            "Authorization": MISP_API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, context=SSL_CONTEXT, timeout=30) as response:
        payload = json.loads(response.read().decode())
    return payload.get("response", {}).get("Attribute", [])


async def broadcast(events):
    if not events:
        return
    for event in events:
        HISTORY.append(event)
    if not CLIENTS:
        return
    message = json.dumps(events)
    stale = set()
    for client in list(CLIENTS):
        try:
            await client.send(message)
        except ConnectionClosed:
            stale.add(client)
    CLIENTS.difference_update(stale)


async def backfill_loop():
    while True:
        try:
            attributes = await asyncio.to_thread(fetch_recent_attributes)
            events = []
            for attr in reversed(attributes):
                events.extend(attribute_to_events({"Attribute": attr, "Event": attr.get("Event", {})}))
            await broadcast(events)
        except Exception as exc:
            print(f"backfill error: {exc}", flush=True)
        await asyncio.sleep(BACKFILL_INTERVAL_SECONDS)


async def zmq_loop():
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(MISP_ZMQ_URL)
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    while True:
        try:
            raw_message = await socket.recv()
            topic, _, body = raw_message.decode("utf-8", "ignore").partition(" ")
            if topic not in {"misp_json_attribute", "misp_json_sighting"}:
                continue
            payload = json.loads(body)
            events = attribute_to_events(payload)
            await broadcast(events)
        except Exception as exc:
            print(f"zmq error: {exc}", flush=True)
            await asyncio.sleep(2)


async def websocket_handler(websocket, path=None):
    CLIENTS.add(websocket)
    try:
        if HISTORY:
            await websocket.send(json.dumps(list(HISTORY)))
        await websocket.wait_closed()
    finally:
        CLIENTS.discard(websocket)


async def main():
    async with serve(websocket_handler, HOST, PORT):
        await asyncio.gather(backfill_loop(), zmq_loop())


if __name__ == "__main__":
    asyncio.run(main())
