"""Provision or verify the non-admin Hermes Media Matrix account and room."""

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys


SECRET = "hermes-media-matrix"
ENCRYPTED_PATH = Path(__file__).resolve().with_name("matrix.sops.yaml")
REMOTE = r"""
import json
import secrets
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8008"
CLIENT = "/_matrix/client/v3"
BOT = "@hermes-media:white.fm"
OPERATOR = "@robert:white.fm"


def request(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(BASE + path, data=data, headers=headers), timeout=12) as response:
        return json.load(response)


def provision():
    request(f"{CLIENT}/profile/{quote(OPERATOR, safe='')}")
    if not any(flow["type"] == "m.login.password" for flow in request(f"{CLIENT}/login")["flows"]):
        raise RuntimeError("Synapse password login is unavailable")
    username = "hermes-media"
    password = secrets.token_urlsafe(48)
    with tempfile.TemporaryFile(mode="w+") as password_file:
        password_file.write(password)
        password_file.flush()
        password_file.seek(0)
        descriptor = password_file.fileno()
        subprocess.run([
            "register_new_matrix_user", "-c", "/config-rendered/homeserver.yaml",
            "--user", username, "--no-admin", "--password-file", f"/proc/self/fd/{descriptor}", BASE,
        ], pass_fds=(descriptor,), capture_output=True, check=True, timeout=25)
    registered = request(f"{CLIENT}/login", {
        "type": "m.login.password", "identifier": {"type": "m.id.user", "user": username},
        "password": password, "device_id": "HERMESMEDIA", "initial_device_display_name": "Hermes Media",
    })
    if registered["user_id"] != BOT:
        raise RuntimeError("Unexpected bot identity")
    token = registered["access_token"]
    device = registered["device_id"]
    created = request(f"{CLIENT}/createRoom", {
        "name": "Hermes Media", "visibility": "private", "preset": "private_chat",
        "creation_content": {"m.federate": False}, "power_level_content_override": {"invite": 100},
        "initial_state": [
            {"type": "m.room.encryption", "state_key": "", "content": {"algorithm": "m.megolm.v1.aes-sha2"}},
            {"type": "m.room.history_visibility", "state_key": "", "content": {"history_visibility": "joined"}},
        ],
    }, token)
    return {"MATRIX_ACCESS_TOKEN": token, "MATRIX_DEVICE_ID": device, "MATRIX_ALLOWED_ROOMS": created["room_id"]}


def verify(data, invite):
    token = data["MATRIX_ACCESS_TOKEN"]
    room = data["MATRIX_ALLOWED_ROOMS"]
    whoami = request(f"{CLIENT}/account/whoami", token=token)
    if whoami["user_id"] != BOT or whoami.get("device_id") != data["MATRIX_DEVICE_ID"]:
        raise RuntimeError("Matrix account or device mismatch")
    room_path = f"{CLIENT}/rooms/{quote(room, safe='')}"
    expected = {
        "m.room.encryption": ("algorithm", "m.megolm.v1.aes-sha2"),
        "m.room.create": ("m.federate", False),
        "m.room.join_rules": ("join_rule", "invite"),
        "m.room.history_visibility": ("history_visibility", "joined"),
        "m.room.power_levels": ("invite", 100),
    }
    for event, (key, value) in expected.items():
        state = request(f"{room_path}/state/{event}/", token=token)
        if state.get(key) != value:
            raise RuntimeError("Matrix room privacy settings mismatch")
    members = request(f"{room_path}/members", token=token)["chunk"]
    active = {item["state_key"]: item["content"]["membership"] for item in members
              if item["content"].get("membership") in {"invite", "join"}}
    if set(active) - {BOT, OPERATOR} or active.get(BOT) != "join":
        raise RuntimeError("Unexpected active room membership")
    if invite and OPERATOR not in active:
        request(f"{room_path}/invite", {"user_id": OPERATOR}, token)
        active[OPERATOR] = "invite"
    return {"room": room, "bot": BOT, "operator_membership": active.get(OPERATOR, "not invited"),
            "encrypted": True, "federated": False}


action = sys.argv[1]
try:
    if action == "provision":
        print(json.dumps(provision()))
    else:
        print(json.dumps(verify(json.load(sys.stdin), action == "invite")))
except HTTPError as error:
    sys.exit(f"Synapse returned HTTP {error.code}; no existing account or room was reset.")
except Exception as error:
    sys.exit(f"Synapse operation failed: {type(error).__name__}")
"""


def synapse(action, data=None):
    command = ["kubectl", "-n", "matrix", "exec", "-i", "deploy/synapse", "-c", "synapse", "--",
               "python", "-c", REMOTE, action]
    process = subprocess.run(command, input=json.dumps(data or {}).encode(), capture_output=True, check=False)
    if process.returncode:
        known = next((line for line in process.stderr.decode(errors="replace").splitlines()
                      if line.startswith(("Synapse returned HTTP ", "Synapse operation failed: "))), None)
        raise SystemExit(known or "Could not reach the local Synapse administration endpoint")
    return json.loads(process.stdout)


def read_secret():
    document = subprocess.check_output([
        "kubectl", "-n", "hermes", "get", "secret", SECRET, "--ignore-not-found", "-o", "json",
    ])
    if not document:
        return None
    data = json.loads(document)["data"]
    return {key: base64.b64decode(data[key]).decode() for key in (
        "MATRIX_ACCESS_TOKEN", "MATRIX_DEVICE_ID", "MATRIX_ALLOWED_ROOMS",
    )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("provision", "verify", "invite"))
    action = parser.parse_args().action
    credentials = read_secret()
    if action != "provision":
        if credentials is None:
            raise SystemExit("Provision the Matrix Secret first")
        print(json.dumps(synapse(action, credentials)))
        return

    new = credentials is None
    if new:
        credentials = synapse("provision")
    secret = json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": SECRET, "namespace": "hermes"}, "stringData": credentials,
    }).encode()
    encrypted = subprocess.check_output([
        "sops", "encrypt", "--input-type", "json", "--output-type", "yaml",
        "--filename-override", str(ENCRYPTED_PATH), "/dev/stdin",
    ], input=secret)
    if new:
        subprocess.run(["kubectl", "create", "-f", "-"], input=secret, stdout=sys.stderr, check=True)
    sys.stdout.buffer.write(encrypted)


if __name__ == "__main__":
    main()
