"""Stream a Home Assistant dashboard as HLS.

Optionally rotates between dashboard views (ROTATION) using the frontend's own in-app navigation,
so switching is instant, and nudges the page a few pixels on every switch (JITTER_PX) so static
elements don't sit in one place on the TV.

Logs in to Home Assistant as a (non-admin) user, opens DASHBOARD_PATH in Chromium on an Xvfb
display, and has ffmpeg capture the display into an HLS playlist in HLS_DIR. The HA top bar is
cropped off (HEADER_PX), and the sidebar is hidden through the frontend's own setting.
"""
import json
import os
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

HA_URL = os.environ["HA_URL"].rstrip("/")
DASHBOARD_PATH = os.environ.get("DASHBOARD_PATH", "/lovelace/0")
USERNAME = os.environ["HA_USERNAME"]
PASSWORD = os.environ["HA_PASSWORD"]
CLIENT_ID = os.environ.get("CLIENT_ID", "https://screen.internal.white.fm/")
WIDTH = int(os.environ.get("WIDTH", "1280"))
HEIGHT = int(os.environ.get("HEIGHT", "720"))
HEADER_PX = int(os.environ.get("HEADER_PX", "56"))
FPS = os.environ.get("FPS", "8")
BITRATE = os.environ.get("BITRATE", "1500k")
HLS_DIR = os.environ.get("HLS_DIR", "/hls")
RELOAD_HOURS = float(os.environ.get("RELOAD_HOURS", "6"))
# Views to cycle through as "path:seconds,...", e.g. "/ha-screen/main:25,/ha-screen/cams:10".
# Empty: stay on DASHBOARD_PATH.
ROTATION = [(p.rsplit(":", 1)[0], float(p.rsplit(":", 1)[1]))
            for p in os.environ.get("ROTATION", "").split(",") if ":" in p]
# Burn-in guard: shift the whole page by up to this many pixels at every view change.
JITTER_PX = int(os.environ.get("JITTER_PX", "0"))
DISPLAY = ":99"


def post(url, data, form=False):
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    ctype = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, body, {"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def login():
    """Run HA's login flow and return a token bundle in the frontend's hassTokens format."""
    flow = post(f"{HA_URL}/auth/login_flow", {
        "client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID})
    res = post(f"{HA_URL}/auth/login_flow/{flow['flow_id']}", {
        "client_id": CLIENT_ID, "username": USERNAME, "password": PASSWORD})
    if res.get("type") != "create_entry":
        sys.exit(f"login failed: {res.get('errors') or res.get('type')}")
    tok = post(f"{HA_URL}/auth/token", {
        "grant_type": "authorization_code", "code": res["result"], "client_id": CLIENT_ID}, form=True)
    tok.update(hassUrl=HA_URL, clientId=CLIENT_ID, expires=int(time.time() * 1000) + tok["expires_in"] * 1000)
    return tok


def start_ffmpeg():
    os.makedirs(HLS_DIR, exist_ok=True)
    for f in os.listdir(HLS_DIR):
        os.remove(os.path.join(HLS_DIR, f))
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-f", "x11grab", "-framerate", FPS, "-video_size", f"{WIDTH}x{HEIGHT + HEADER_PX}",
        "-draw_mouse", "0", "-i", DISPLAY,
        # Silent audio track: some players refuse video-only HLS.
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-vf", f"crop={WIDTH}:{HEIGHT}:0:{HEADER_PX},format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-profile:v", "high",
        "-b:v", BITRATE, "-maxrate", BITRATE, "-bufsize", "3000k",
        "-g", str(int(FPS) * 2), "-keyint_min", str(int(FPS) * 2), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "32k", "-shortest",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
        "-hls_flags", "delete_segments+omit_endlist+independent_segments",
        "-hls_segment_filename", f"{HLS_DIR}/seg%06d.ts", f"{HLS_DIR}/stream.m3u8",
    ])


def main():
    xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{WIDTH}x{HEIGHT + HEADER_PX}x24", "-nolisten", "tcp"])
    os.environ["DISPLAY"] = DISPLAY
    time.sleep(2)
    tokens = login()
    init = (
        "if (!localStorage.getItem('hassTokens')) {"
        f"  localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
        "}"
        "localStorage.setItem('dockedSidebar', JSON.stringify('always_hidden'));"
        "localStorage.setItem('selectedTheme', JSON.stringify({theme: 'default', dark: true}));"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, ignore_default_args=["--enable-automation"], args=[
            "--kiosk", "--window-position=0,0", f"--window-size={WIDTH},{HEIGHT + HEADER_PX}",
            "--noerrdialogs", "--disable-infobars", "--hide-scrollbars", "--disable-session-crashed-bubble",
            "--force-device-scale-factor=1", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(no_viewport=True, color_scheme="dark")
        ctx.add_init_script(init)
        page = ctx.new_page()
        # --kiosk alone still leaves the toolbar under Playwright; force real fullscreen.
        cdp = ctx.new_cdp_session(page)
        window = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "fullscreen"}})
        page.goto(HA_URL + DASHBOARD_PATH, wait_until="domcontentloaded", timeout=60000)
        print(f"opened {DASHBOARD_PATH}", flush=True)
        ffmpeg = start_ffmpeg()
        last_reload = time.time()
        step, next_switch = 0, 0.0
        last_check = 0.0
        while True:
            time.sleep(0.5)
            now = time.time()
            if ROTATION and now >= next_switch:
                path, secs = ROTATION[step % len(ROTATION)]
                try:
                    show(page, path)
                except Exception as exc:  # page mid-reload; try again next tick
                    print(f"rotate failed: {exc}", flush=True)
                step, next_switch = step + 1, now + secs
            if now - last_check < 15:
                continue
            last_check = now
            if xvfb.poll() is not None or not browser.is_connected():
                sys.exit("display or browser exited")
            if ffmpeg.poll() is not None:
                print("ffmpeg exited, restarting", flush=True)
                ffmpeg = start_ffmpeg()
            # A periodic reload clears any stuck frontend state (lost websocket, stale resources).
            if now - last_reload > RELOAD_HOURS * 3600 or "/auth/authorize" in page.url:
                if "/auth/authorize" in page.url:
                    print("session lost, logging in again", flush=True)
                    tokens = login()
                    page.evaluate("t => localStorage.setItem('hassTokens', t)", json.dumps(tokens))
                page.goto(HA_URL + DASHBOARD_PATH, wait_until="domcontentloaded", timeout=60000)
                last_reload = now


def show(page, path):
    """Switch view the way the HA frontend's navigate() does, and nudge the page for burn-in."""
    dx = random.randint(-JITTER_PX, JITTER_PX) if JITTER_PX else 0
    dy = -random.randint(0, JITTER_PX) if JITTER_PX else 0  # never down: the top bar is cropped off
    page.evaluate(
        """([path, dx, dy]) => {
            if (location.pathname !== path) {
                history.replaceState(null, "", path);
                window.dispatchEvent(new CustomEvent("location-changed", {detail: {replace: true}}));
            }
            document.body.style.transform = `translate(${dx}px, ${dy}px)`;
        }""",
        [path, dx, dy],
    )

if __name__ == "__main__":
    main()
