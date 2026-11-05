"""Face detection + embedding on onnxruntime.

Detector: SCRFD-10GF (insightface buffalo_l det_10g.onnx).
Recognizer: ArcFace R50 trained on WebFace600K (buffalo_l w600k_r50.onnx).

Pre/post-processing follows insightface's reference implementation
(model_zoo/scrfd.py, model_zoo/arcface_onnx.py, utils/face_align.py) so the
embeddings match the upstream package without pulling in its dependencies.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime as ort

# Reference 5-point landmarks for a 112x112 ArcFace crop.
ARCFACE_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class Face:
    box: np.ndarray  # x1, y1, x2, y2 in source pixels
    score: float
    kps: np.ndarray  # (5, 2)
    embedding: np.ndarray | None = None  # L2-normalised (512,)

    @property
    def area(self) -> float:
        return float((self.box[2] - self.box[0]) * (self.box[3] - self.box[1]))


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Similarity transform (scale, rotation, translation) mapping src->dst.

    Same estimate as skimage.transform.SimilarityTransform, which insightface's
    norm_crop uses. Returns the 2x3 affine matrix.
    """
    num, dim = src.shape
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_demean = src - src_mean
    dst_demean = dst - dst_mean
    a = dst_demean.T @ src_demean / num
    d = np.ones((dim,), dtype=np.float64)
    if np.linalg.det(a) < 0:
        d[dim - 1] = -1
    t = np.eye(dim + 1, dtype=np.float64)
    u, s, v = np.linalg.svd(a)
    rank = np.linalg.matrix_rank(a)
    if rank == 0:
        return np.full((2, 3), np.nan)
    if rank == dim - 1:
        if np.linalg.det(u) * np.linalg.det(v) > 0:
            t[:dim, :dim] = u @ v
        else:
            s_ = d[dim - 1]
            d[dim - 1] = -1
            t[:dim, :dim] = u @ np.diag(d) @ v
            d[dim - 1] = s_
    else:
        t[:dim, :dim] = u @ np.diag(d) @ v
    scale = 1.0 / src_demean.var(axis=0).sum() * (s @ d)
    t[:dim, dim] = dst_mean - scale * (t[:dim, :dim] @ src_mean.T)
    t[:dim, :dim] *= scale
    return t[:2, :]


def _nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


class Engine:
    def __init__(self, model_dir: str, det_size: int = 640, threads: int | None = None):
        opts = ort.SessionOptions()
        threads = threads or int(os.environ.get("ORT_THREADS", "0")) or None
        if threads:
            opts.intra_op_num_threads = threads
        providers = ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(os.path.join(model_dir, "det_10g.onnx"), opts, providers=providers)
        self.rec = ort.InferenceSession(os.path.join(model_dir, "w600k_r50.onnx"), opts, providers=providers)
        self.det_size = det_size
        self.det_input = self.det.get_inputs()[0].name
        self.det_outputs = [o.name for o in self.det.get_outputs()]
        self.rec_input = self.rec.get_inputs()[0].name
        self.strides = (8, 16, 32)
        self.num_anchors = 2
        self._anchor_cache: dict[tuple[int, int, int], np.ndarray] = {}
        # onnxruntime sessions are thread-safe, but one inference at a time keeps
        # latency predictable on a shared CPU node.
        self.lock = threading.Lock()

    # ---- detection -------------------------------------------------------
    def _anchors(self, h: int, w: int, stride: int) -> np.ndarray:
        key = (h, w, stride)
        if key not in self._anchor_cache:
            centers = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            centers = (centers * stride).reshape((-1, 2))
            centers = np.stack([centers] * self.num_anchors, axis=1).reshape((-1, 2))
            self._anchor_cache[key] = centers
        return self._anchor_cache[key]

    def detect(self, img: np.ndarray, threshold: float, nms_thresh: float = 0.4) -> list[Face]:
        """img: BGR uint8. Returns faces with score >= threshold, best first."""
        size = self.det_size
        im_ratio = img.shape[0] / img.shape[1]
        if im_ratio > 1:
            new_h, new_w = size, int(size / im_ratio)
        else:
            new_w, new_h = size, int(size * im_ratio)
        scale = new_h / img.shape[0]
        resized = cv2.resize(img, (new_w, new_h))
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        canvas[:new_h, :new_w, :] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128.0, (size, size), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.det.run(self.det_outputs, {self.det_input: blob})

        fmc = len(self.strides)
        scores_l, boxes_l, kps_l = [], [], []
        for idx, stride in enumerate(self.strides):
            scores = outs[idx].reshape(-1)
            bbox = outs[idx + fmc].reshape(-1, 4) * stride
            kps = outs[idx + fmc * 2].reshape(-1, 10) * stride
            h, w = size // stride, size // stride
            centers = self._anchors(h, w, stride)
            pos = np.where(scores >= threshold)[0]
            if pos.size == 0:
                continue
            c = centers[pos]
            b = bbox[pos]
            boxes = np.stack([c[:, 0] - b[:, 0], c[:, 1] - b[:, 1], c[:, 0] + b[:, 2], c[:, 1] + b[:, 3]], axis=-1)
            k = kps[pos].reshape(-1, 5, 2) + c[:, None, :]
            scores_l.append(scores[pos])
            boxes_l.append(boxes)
            kps_l.append(k)
        if not scores_l:
            return []
        scores = np.concatenate(scores_l)
        boxes = np.concatenate(boxes_l) / scale
        kpss = np.concatenate(kps_l) / scale
        order = scores.argsort()[::-1]
        dets = np.hstack([boxes, scores[:, None]])[order]
        kpss = kpss[order]
        keep = _nms(dets, nms_thresh)
        faces = []
        for i in keep:
            box = dets[i, :4].copy()
            box[[0, 2]] = box[[0, 2]].clip(0, img.shape[1])
            box[[1, 3]] = box[[1, 3]].clip(0, img.shape[0])
            faces.append(Face(box=box, score=float(dets[i, 4]), kps=kpss[i]))
        return faces

    # ---- recognition -----------------------------------------------------
    def embed(self, img: np.ndarray, faces: list[Face]) -> None:
        if not faces:
            return
        crops = []
        for f in faces:
            m = _umeyama(f.kps.astype(np.float64), ARCFACE_DST.astype(np.float64))
            crops.append(cv2.warpAffine(img, m, (112, 112), borderValue=0.0))
        blob = cv2.dnn.blobFromImages(crops, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        feats = self.rec.run(None, {self.rec_input: blob})[0]
        feats = feats / np.linalg.norm(feats, axis=1, keepdims=True)
        for f, e in zip(faces, feats):
            f.embedding = e.astype(np.float32)

    def analyze(self, img: np.ndarray, threshold: float) -> list[Face]:
        with self.lock:
            faces = self.detect(img, threshold)
            self.embed(img, faces)
        return faces
