"""CompreFace-compatible face recognition API.

Implements the subset of CompreFace's recognition service that Double Take (and
the keep-warm heartbeat) use, with the same paths, x-api-key header, response
shapes and error codes:

  POST   /api/v1/recognition/recognize        recognise faces in an image
  POST   /api/v1/recognition/faces?subject=   add an example face
  GET    /api/v1/recognition/faces            list examples
  DELETE /api/v1/recognition/faces?subject=   delete all examples of a subject
  DELETE /api/v1/recognition/faces/{id}       delete one example
  GET    /api/v1/recognition/faces/{id}/img   example image
  GET/POST/PUT/DELETE /api/v1/recognition/subjects[/{subject}]

Similarity follows CompreFace's scale: (tanh((c0 - d) * c1) + 1) / 2, where d is
the euclidean distance between L2-normalised embeddings and (c0, c1) are
calibrated for this model so Double Take's confidence thresholds keep their
meaning (see README).
"""

from __future__ import annotations

import base64
import hmac
import io
import math
import os
import time

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from .engine import Engine
from .store import Store

API_KEY = os.environ.get("API_KEY", "")
MODEL_DIR = os.environ.get("MODEL_DIR", "/models")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
SIM_C0 = float(os.environ.get("SIM_C0", "1.10"))
SIM_C1 = float(os.environ.get("SIM_C1", "6.0"))
MAX_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(10 * 1024 * 1024)))
MAX_SIDE = int(os.environ.get("MAX_IMAGE_SIDE", "4096"))
MODEL_NAME = "scrfd_10g+arcface_w600k_r50"
VERSIONS = {"detector": "scrfd_10g", "calculator": "arcface_w600k_r50", "service": "face-api"}

engine = Engine(MODEL_DIR)
store = Store(DATA_DIR)
app = FastAPI(title="face-api", docs_url=None, redoc_url=None, openapi_url=None)


class ApiError(Exception):
    def __init__(self, status: int, code: int, message: str):
        self.status, self.code, self.message = status, code, message


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return JSONResponse({"message": exc.message, "code": exc.code}, status_code=exc.status)


def similarity(distance: float) -> float:
    return round((math.tanh((SIM_C0 - distance) * SIM_C1) + 1.0) / 2.0, 5)


def check_key(request: Request) -> None:
    if not API_KEY:
        return
    key = request.headers.get("x-api-key")
    if key is None:
        raise ApiError(401, 20, "Missing header: x-api-key")
    if not hmac.compare_digest(key.encode(), API_KEY.encode()):
        raise ApiError(401, 20, "Invalid API key")


async def read_image(request: Request) -> bytes:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ApiError(400, 5, "Required request part 'file' is not present")
        data = await upload.read()
    elif ctype.startswith("application/json"):
        body = await request.json()
        try:
            data = base64.b64decode(body["file"], validate=False)
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, 5, "Field 'file' must be a base64-encoded image")
    else:
        raise ApiError(400, 5, "Expected multipart/form-data or application/json")
    if not data:
        raise ApiError(400, 5, "Empty file")
    if len(data) > MAX_BYTES:
        raise ApiError(400, 5, "File is too big")
    return data


def decode(data: bytes) -> np.ndarray:
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ApiError(400, 26, "Wrong file format: unable to decode image")
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE))
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def qfloat(request: Request, name: str, default: float) -> float:
    v = request.query_params.get(name)
    try:
        return float(v) if v not in (None, "", "undefined") else default
    except ValueError:
        raise ApiError(400, 5, f"Invalid value for '{name}'")


def qint(request: Request, name: str, default: int) -> int:
    return int(qfloat(request, name, default))


def box_json(f) -> dict:
    x1, y1, x2, y2 = (int(round(v)) for v in f.box)
    return {"probability": round(f.score, 5), "x_max": x2, "y_max": y2, "x_min": x1, "y_min": y1}


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "faces": len(store.ids), "subjects": len(set(store.subjects))}


@app.post("/api/v1/recognition/recognize")
async def recognize(request: Request):
    check_key(request)
    threshold = qfloat(request, "det_prob_threshold", 0.8)
    limit = qint(request, "limit", 0)
    count = max(1, qint(request, "prediction_count", 1))
    img = decode(await read_image(request))
    t0 = time.perf_counter()
    faces = await run_in_threadpool(engine.analyze, img, threshold)
    elapsed = (time.perf_counter() - t0) * 1000
    if not faces:
        raise ApiError(400, 28, "No face is found in the given image")
    if limit > 0:
        faces = faces[:limit]
    with_distance = request.query_params.get("with_distance") == "true"  # calibration aid
    result = []
    for f in faces:
        subjects = []
        for s, d in store.best_matches(f.embedding, count):
            entry = {"subject": s, "similarity": similarity(d)}
            if with_distance:
                entry["distance"] = round(d, 5)
            subjects.append(entry)
        result.append({"box": box_json(f), "subjects": subjects, "execution_time": {"total": round(elapsed, 1)}})
    return {"result": result, "plugins_versions": VERSIONS}


@app.post("/api/v1/recognition/faces")
async def add_face(request: Request):
    check_key(request)
    subject = request.query_params.get("subject", "").strip()
    if not subject:
        raise ApiError(400, 5, "Required parameter 'subject' is not present")
    threshold = qfloat(request, "det_prob_threshold", 0.8)
    data = await read_image(request)
    img = decode(data)
    faces = await run_in_threadpool(engine.analyze, img, threshold)
    if not faces:
        raise ApiError(400, 28, "No face is found in the given image")
    face = max(faces, key=lambda f: f.score)  # like CompreFace: keep the most confident face
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    image_id = store.add(subject, face.embedding, jpg.tobytes() if ok else data, MODEL_NAME, face.score)
    return {"image_id": image_id, "subject": subject}


@app.get("/api/v1/recognition/faces")
async def list_faces(request: Request):
    check_key(request)
    page = max(0, qint(request, "page", 0))
    size = max(1, qint(request, "size", 20))
    rows = store.list_faces(request.query_params.get("subject") or None)
    chunk = rows[page * size : (page + 1) * size]
    return {
        "faces": [{"image_id": i, "subject": s} for i, s in chunk],
        "page_number": page,
        "page_size": size,
        "total_pages": (len(rows) + size - 1) // size,
        "total_elements": len(rows),
    }


@app.delete("/api/v1/recognition/faces")
async def delete_subject_faces(request: Request):
    check_key(request)
    subject = request.query_params.get("subject", "").strip()
    if not subject:
        raise ApiError(400, 5, "Required parameter 'subject' is not present")
    return {"deleted": store.delete_subject(subject)}


@app.delete("/api/v1/recognition/faces/{image_id}")
async def delete_face(image_id: str, request: Request):
    check_key(request)
    subject = store.delete_image(image_id)
    if subject is None:
        raise ApiError(404, 24, f"Image with id {image_id} not found")
    return {"image_id": image_id, "subject": subject}


@app.get("/api/v1/recognition/faces/{image_id}/img")
async def face_image(image_id: str, request: Request):
    check_key(request)
    path = store.image_path(image_id)
    if "/" in image_id or not os.path.exists(path):
        raise ApiError(404, 24, f"Image with id {image_id} not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/v1/recognition/subjects")
async def list_subjects(request: Request):
    check_key(request)
    return {"subjects": store.list_subjects()}


@app.put("/api/v1/recognition/subjects/{subject}")
async def rename_subject(subject: str, request: Request):
    check_key(request)
    new = ((await request.json()) or {}).get("subject", "").strip()
    if not new:
        raise ApiError(400, 5, "Field 'subject' is required")
    return {"updated": store.rename_subject(subject, new) > 0}


@app.delete("/api/v1/recognition/subjects/{subject}")
async def delete_subject(subject: str, request: Request):
    check_key(request)
    store.delete_subject(subject)
    return {"subject": subject}
