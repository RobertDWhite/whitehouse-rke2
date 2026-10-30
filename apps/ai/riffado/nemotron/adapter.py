"""OpenAI-compatible /v1/audio/transcriptions in front of an on-demand vLLM
serving NVIDIA Nemotron 3.5 Transcribe (NVFP4) on the shared RTX 5090.

vLLM only runs while there is work: a request unloads ollama-5090, starts
`vllm serve`, transcribes, and the server is stopped after IDLE_SECONDS, so the
5090 is free for ollama/comfyui the rest of the time (as with VibeVoice before).

The response matches what Riffado stored from VibeVoice: `text` holds
`[hh:mm:ss] Speaker N` blocks, plus `segments`, `language` and `duration`.
"""

import asyncio
import base64
import io
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.request
import wave

import av
import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

log = logging.getLogger("nemotron-adapter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

API_KEY = os.environ.get("TRANSCRIPTION_API_KEY", "")
MODEL_DIR = os.environ["MODEL_DIR"]
SERVED_NAME = os.environ.get("SERVED_MODEL_NAME", "nemotron-transcribe")
VLLM_BIN = os.environ.get("VLLM_BIN", "/data/venv/bin/vllm")
VLLM_PORT = int(os.environ.get("VLLM_PORT", "8001"))
VLLM_EXTRA_ARGS = json.loads(os.environ.get("VLLM_EXTRA_ARGS", "[]"))
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "600"))
STARTUP_TIMEOUT = int(os.environ.get("VLLM_STARTUP_TIMEOUT", "1800"))
# The model takes up to an hour per request; stay under it and split long
# recordings near a pause.
CHUNK_TARGET = float(os.environ.get("CHUNK_TARGET_SECONDS", "3300"))
CHUNK_MAX = float(os.environ.get("CHUNK_MAX_SECONDS", "3540"))
OLLAMA_URL = os.environ.get("OLLAMA_UNLOAD_URL", "")
# vLLM refuses to start unless --gpu-memory-utilization of the card is free.
VRAM_NEEDED_GIB = float(os.environ.get("VRAM_NEEDED_GIB", "24.0"))
VRAM_WAIT_SECONDS = int(os.environ.get("VRAM_WAIT_SECONDS", "1800"))
TMP_DIR = os.environ.get("ADAPTER_TMP", "/data/tmp")
SR = 16000

PROMPT = (
    "Transcribe this audio with punctuation and capitalization. Include all spoken "
    "words and verbal fillers. Write numbers in their standard written form. Put "
    "speaker tags <spk:0> <spk:1> <spk:2> <spk:3> on the transcription."
)

app = FastAPI()


@app.exception_handler(HTTPException)
async def openai_error(_: Request, exc: HTTPException) -> JSONResponse:
    # Riffado's OpenAI client only reads {"error": {"message": ...}}; FastAPI's
    # default {"detail": ...} showed up there as "503 status code (no body)".
    return JSONResponse(status_code=exc.status_code,
                        content={"error": {"message": str(exc.detail), "type": "server_error",
                                           "code": exc.status_code}})
work_lock = asyncio.Lock()
state = {"vllm": "stopped", "busy": False, "last_used": 0.0}
proc: subprocess.Popen | None = None
idle_task: asyncio.Task | None = None


def check_auth(request: Request) -> None:
    if not API_KEY:
        return
    if request.headers.get("authorization", "") != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def unload_ollama() -> None:
    """Free the 5090: drop every model ollama-5090 has resident. Never fatal."""
    if not OLLAMA_URL:
        return
    try:
        ps = json.load(urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=10))
        for m in ps.get("models", []):
            body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
            urllib.request.urlopen(
                urllib.request.Request(
                    OLLAMA_URL + "/api/generate", data=body,
                    headers={"Content-Type": "application/json"}),
                timeout=60)
            log.info("unloaded ollama model %s", m["name"])
    except Exception as e:  # noqa: BLE001
        log.info("ollama unload skipped: %s", e)


def free_vram_gib() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30, check=True).stdout
        return int(out.split()[0]) / 1024
    except Exception as e:  # noqa: BLE001
        log.info("nvidia-smi failed: %s", e)
        return None


async def wait_for_vram() -> None:
    """ollama only lets go of a model once its in-flight request finishes, so
    keep asking until vLLM's share of the card is actually free."""
    deadline = time.monotonic() + VRAM_WAIT_SECONDS
    while True:
        await asyncio.to_thread(unload_ollama)
        free = await asyncio.to_thread(free_vram_gib)
        if free is None or free >= VRAM_NEEDED_GIB:
            return
        if time.monotonic() > deadline:
            raise HTTPException(status_code=503,
                                detail=f"only {free:.1f} GiB VRAM free, need {VRAM_NEEDED_GIB:.1f}")
        log.info("waiting for VRAM: %.1f GiB free, need %.1f", free, VRAM_NEEDED_GIB)
        await asyncio.sleep(15)


async def ensure_vllm() -> None:
    global proc
    if proc is not None and proc.poll() is None and state["vllm"] == "ready":
        return
    if proc is not None and proc.poll() is not None:
        proc = None
    if proc is None:
        await wait_for_vram()
        cmd = [VLLM_BIN, "serve", MODEL_DIR, "--served-model-name", SERVED_NAME,
               "--host", "127.0.0.1", "--port", str(VLLM_PORT), *VLLM_EXTRA_ARGS]
        log.info("starting vLLM: %s", " ".join(cmd))
        state["vllm"] = "starting"
        proc = subprocess.Popen(cmd, start_new_session=True)
    deadline = time.monotonic() + STARTUP_TIMEOUT
    async with httpx.AsyncClient() as c:
        while time.monotonic() < deadline:
            # sdr-viewer-api and LAN clients (via ollama-router) reload a model
            # on ollama-5090 every couple of minutes. Until vLLM has claimed
            # its share of the card, keep evicting it; afterwards ollama finds
            # no room and runs those requests mostly on CPU.
            await asyncio.to_thread(unload_ollama)
            if proc.poll() is not None:
                state["vllm"] = "stopped"
                raise HTTPException(status_code=503, detail=f"vLLM exited with {proc.returncode}")
            try:
                r = await c.get(f"http://127.0.0.1:{VLLM_PORT}/health", timeout=5)
                if r.status_code == 200:
                    state["vllm"] = "ready"
                    log.info("vLLM ready")
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(5)
    stop_vllm()
    raise HTTPException(status_code=503, detail="vLLM did not start in time")


def stop_vllm() -> None:
    global proc
    if proc is None:
        return
    log.info("stopping vLLM")
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=60)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc = None
    state["vllm"] = "stopped"


async def idle_reaper() -> None:
    while True:
        await asyncio.sleep(30)
        if (proc is not None and not state["busy"]
                and time.time() - state["last_used"] > IDLE_SECONDS):
            async with work_lock:
                if not state["busy"] and time.time() - state["last_used"] > IDLE_SECONDS:
                    await asyncio.to_thread(stop_vllm)


@app.on_event("startup")
async def _startup() -> None:
    global idle_task
    os.makedirs(TMP_DIR, exist_ok=True)
    idle_task = asyncio.create_task(idle_reaper())


@app.on_event("shutdown")
def _shutdown() -> None:
    stop_vllm()


def decode(path: str) -> np.ndarray:
    """Any audio/video container -> 16 kHz mono float32."""
    out = []
    with av.open(path) as f:
        stream = f.streams.audio[0]
        res = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for frame in f.decode(stream):
            for r in res.resample(frame):
                out.append(r.to_ndarray().reshape(-1))
        for r in res.resample(None):
            out.append(r.to_ndarray().reshape(-1))
    if not out:
        raise HTTPException(status_code=400, detail="no audio decoded")
    return np.concatenate(out).astype(np.float32) / 32768.0


def split_points(pcm: np.ndarray) -> list[tuple[int, int]]:
    """Chunk boundaries near the quietest 0.5 s window before CHUNK_MAX."""
    n = len(pcm)
    if n <= CHUNK_MAX * SR:
        return [(0, n)]
    chunks, start, win = [], 0, SR // 2
    while n - start > CHUNK_MAX * SR:
        lo, hi = start + int(CHUNK_TARGET * 0.85 * SR), start + int(CHUNK_MAX * SR)
        seg = pcm[lo:hi]
        k = len(seg) // win
        rms = np.sqrt((seg[: k * win].reshape(k, win) ** 2).mean(axis=1))
        cut = lo + int(np.argmin(rms)) * win + win // 2
        chunks.append((start, cut))
        start = cut
    chunks.append((start, n))
    return chunks


def to_wav_b64(pcm: np.ndarray) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    return base64.b64encode(buf.getvalue()).decode()


SPK_RE = re.compile(r"<spk:(\d+)>")


def segments_from_tags(text: str, offset: float, dur: float) -> list[dict]:
    """Fallback when word timestamps are missing: split on inline speaker tags."""
    parts = SPK_RE.split(text)
    segs, spk = [], 0
    if parts[0].strip():
        segs.append({"speaker": 0, "start": offset, "end": offset + dur, "text": parts[0].strip()})
    for i in range(1, len(parts), 2):
        spk, body = int(parts[i]), parts[i + 1].strip()
        if body:
            segs.append({"speaker": spk, "start": offset, "end": offset + dur, "text": body})
    return segs


def segments_from_words(words: list[dict], offset: float) -> list[dict]:
    segs: list[dict] = []
    for w in words:
        spk = w.get("speaker")
        spk = int(spk) if spk is not None else (segs[-1]["speaker"] if segs else 0)
        token = SPK_RE.sub("", str(w.get("word", ""))).strip()
        if not token:
            continue
        start, end = offset + float(w["start"]), offset + float(w["end"])
        if segs and segs[-1]["speaker"] == spk:
            segs[-1]["text"] += " " + token
            segs[-1]["end"] = end
        else:
            segs.append({"speaker": spk, "start": start, "end": end, "text": token})
    return segs


async def transcribe_chunk(client: httpx.AsyncClient, pcm: np.ndarray, offset: float) -> tuple[list[dict], str]:
    body = {
        "model": SERVED_NAME,
        "temperature": 0,
        "seed": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64," + to_wav_b64(pcm)}},
        ]}],
        "chat_template_kwargs": {"enable_thinking": False},
        "skip_special_tokens": False,
        "spaces_between_special_tokens": False,
        "mm_processor_kwargs": {"capture_ctc_timestamps": True},
    }
    r = await client.post(f"http://127.0.0.1:{VLLM_PORT}/v1/chat/completions", json=body, timeout=None)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"vLLM {r.status_code}: {r.text[:500]}")
    data = r.json()
    text = data["choices"][0]["message"].get("content") or ""
    ts = data.get("ctc_timestamps") or {}
    words = ts.get("words") or []
    if words and not ts.get("error"):
        segs = segments_from_words(words, offset)
    else:
        if ts.get("error"):
            log.warning("ctc timestamps unavailable: %s", ts.get("error"))
        segs = segments_from_tags(text, offset, len(pcm) / SR)
    return segs, text


def hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


@app.get("/health")
async def health() -> dict:
    return {"status": "running", "busy": state["busy"], "vllm": state["vllm"]}


@app.get("/v1/models")
async def models(request: Request) -> dict:
    check_auth(request)
    return {"object": "list", "data": [{"id": SERVED_NAME, "object": "model", "owned_by": "nvidia"}]}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(SERVED_NAME),
    language: str | None = Form(None),
    response_format: str = Form("json"),
):
    check_auth(request)
    fd, path = tempfile.mkstemp(dir=TMP_DIR, suffix=os.path.splitext(file.filename or "")[1])
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        pcm = await asyncio.to_thread(decode, path)
    finally:
        os.unlink(path)
    duration = len(pcm) / SR
    async with work_lock:
        state["busy"] = True
        t0 = time.monotonic()
        try:
            try:
                await ensure_vllm()
            except HTTPException as e:
                # ollama can grab the card again between the VRAM check and
                # vLLM's own check; one retry covers that race.
                if e.status_code != 503:
                    raise
                log.warning("vLLM start failed (%s), retrying once", e.detail)
                await ensure_vllm()
            segs: list[dict] = []
            async with httpx.AsyncClient() as client:
                bounds = split_points(pcm)
                for i, (a, b) in enumerate(bounds):
                    log.info("chunk %d/%d %s-%s", i + 1, len(bounds), hms(a / SR), hms(b / SR))
                    s, _ = await transcribe_chunk(client, pcm[a:b], a / SR)
                    segs.extend(s)
        finally:
            state["busy"] = False
            state["last_used"] = time.time()
    log.info("transcribed %.0fs of audio in %.0fs", duration, time.monotonic() - t0)
    blocks = [f"[{hms(s['start'])}] Speaker {s['speaker'] + 1}\n{s['text']}" for s in segs]
    text = "\n\n".join(blocks)
    if response_format == "text":
        return PlainTextResponse(text)
    return JSONResponse({
        "text": text,
        "segments": [{"id": str(i), "speaker": f"Speaker {s['speaker'] + 1}", "start": round(s["start"], 2),
                      "end": round(s["end"], 2), "text": s["text"]} for i, s in enumerate(segs)],
        "language": language or "en",
        "duration": round(duration, 2),
    })


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8765")), log_level="info")
