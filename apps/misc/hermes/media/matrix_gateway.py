"""Run the pinned Hermes image with a fail-closed, single-room intake."""

import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen


STATE = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "gateway_state.json"
OPERATOR = "@robert:white.fm"
SAFE_COMMANDS = frozenset({"/help", "/new", "/reset", "/stop"})


def pin_intake(adapter, room):
    on_message = adapter._on_room_message
    if not callable(on_message) or not callable(adapter._on_invite) or not callable(adapter._on_reaction):
        raise RuntimeError("Incompatible Matrix adapter; refusing to start")

    async def on_private_text(event):
        if str(getattr(event, "room_id", "")) != room or str(getattr(event, "sender", "")) != OPERATOR:
            return
        crypto = event.get("mautrix", {}) if hasattr(event, "get") else {}
        if not isinstance(crypto, dict) or crypto.get("was_encrypted") is not True:
            return
        content = getattr(event, "content", None)
        msgtype = content.get("msgtype") if isinstance(content, dict) else getattr(content, "msgtype", None)
        body = content.get("body") if isinstance(content, dict) else getattr(content, "body", None)
        if str(msgtype) != "m.text" or not isinstance(body, str) or not body.strip():
            return
        first_word = body.lstrip().split(maxsplit=1)[0]
        if first_word.startswith("/") and first_word not in SAFE_COMMANDS:
            return
        await on_message(event)

    async def ignore_event(_event):
        return None

    adapter._on_room_message = on_private_text
    adapter._on_invite = ignore_event
    adapter._on_reaction = ignore_event
    return adapter


def verify_room(room):
    homeserver = os.environ["MATRIX_HOMESERVER"]
    token = os.environ["MATRIX_ACCESS_TOKEN"]

    def state(event_type):
        request = Request(f"{homeserver}/_matrix/client/v3/rooms/{quote(room, safe='')}/state/{event_type}/",
                          headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=8) as response:
            return json.load(response)

    if state("m.room.encryption").get("algorithm") != "m.megolm.v1.aes-sha2":
        raise RuntimeError("Matrix room is not encrypted")
    if state("m.room.create").get("m.federate") is not False or state("m.room.join_rules").get("join_rule") != "invite":
        raise RuntimeError("Matrix room is not private and non-federated")


def main():
    if sys.argv[1:] == ["--ready"]:
        try:
            matrix = json.loads(STATE.read_text()).get("platforms", {}).get("matrix", {})
        except (OSError, ValueError):
            return 1
        return int(matrix.get("state") != "connected")

    STATE.unlink(missing_ok=True)
    room = os.environ["MATRIX_ALLOWED_ROOMS"]
    if not room.startswith("!") or "," in room or any(char.isspace() for char in room):
        raise RuntimeError("Exactly one Matrix room is required")
    if os.environ.get("MATRIX_ALLOWED_USERS") != OPERATOR:
        raise RuntimeError("The media gateway must have exactly one operator")
    verify_room(room)

    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins
    from hermes_cli.main import main as hermes_main

    discover_plugins()
    entry = platform_registry.get("matrix")
    if entry is None:
        raise RuntimeError("Cannot load pinned Matrix adapter")
    factory = entry.adapter_factory

    def locked_adapter(config):
        adapter = pin_intake(factory(config), room)
        print("Hermes Media: pinned Matrix room and operator intake active", flush=True)
        return adapter

    entry.adapter_factory = locked_adapter
    entry.allow_update_command = False
    sys.argv[0] = "hermes"
    return hermes_main()


if __name__ == "__main__":
    sys.exit(main())
