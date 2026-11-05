"""Face gallery: SQLite rows + original images on disk, embeddings cached in RAM."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid

import numpy as np


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.img_dir = os.path.join(data_dir, "images")
        os.makedirs(self.img_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "faces.db"), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS faces (
                 image_id TEXT PRIMARY KEY,
                 subject TEXT NOT NULL,
                 embedding BLOB NOT NULL,
                 model TEXT NOT NULL,
                 det_score REAL,
                 created REAL NOT NULL)"""
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS faces_subject ON faces(subject)")
        self.db.commit()
        self.lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        rows = self.db.execute("SELECT image_id, subject, embedding FROM faces ORDER BY created").fetchall()
        self.ids = [r[0] for r in rows]
        self.subjects = [r[1] for r in rows]
        self.matrix = (
            np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 512), np.float32)
        )

    def add(self, subject: str, embedding: np.ndarray, image: bytes, model: str, det_score: float) -> str:
        image_id = str(uuid.uuid4())
        with open(os.path.join(self.img_dir, image_id + ".jpg"), "wb") as fh:
            fh.write(image)
        with self.lock:
            self.db.execute(
                "INSERT INTO faces VALUES (?,?,?,?,?,?)",
                (image_id, subject, embedding.astype(np.float32).tobytes(), model, det_score, time.time()),
            )
            self.db.commit()
            self._load()
        return image_id

    def delete_subject(self, subject: str) -> int:
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT image_id FROM faces WHERE subject=?", (subject,))]
            self.db.execute("DELETE FROM faces WHERE subject=?", (subject,))
            self.db.commit()
            self._load()
        for i in ids:
            self._unlink(i)
        return len(ids)

    def delete_image(self, image_id: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT subject FROM faces WHERE image_id=?", (image_id,)).fetchone()
            if not row:
                return None
            self.db.execute("DELETE FROM faces WHERE image_id=?", (image_id,))
            self.db.commit()
            self._load()
        self._unlink(image_id)
        return row[0]

    def rename_subject(self, old: str, new: str) -> int:
        with self.lock:
            cur = self.db.execute("UPDATE faces SET subject=? WHERE subject=?", (new, old))
            self.db.commit()
            self._load()
            return cur.rowcount

    def list_faces(self, subject: str | None):
        q = "SELECT image_id, subject FROM faces"
        args: tuple = ()
        if subject:
            q += " WHERE subject=?"
            args = (subject,)
        return self.db.execute(q + " ORDER BY created", args).fetchall()

    def list_subjects(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT subject FROM faces ORDER BY subject")]

    def image_path(self, image_id: str) -> str:
        return os.path.join(self.img_dir, image_id + ".jpg")

    def _unlink(self, image_id: str) -> None:
        try:
            os.remove(self.image_path(image_id))
        except FileNotFoundError:
            pass

    def best_matches(self, emb: np.ndarray, count: int) -> list[tuple[str, float]]:
        """Top `count` subjects by smallest euclidean distance (L2-normalised)."""
        with self.lock:
            matrix, subjects = self.matrix, self.subjects
        if matrix.shape[0] == 0:
            return []
        cos = matrix @ emb
        dist = np.sqrt(np.clip(2.0 - 2.0 * cos, 0.0, None))
        best: dict[str, float] = {}
        for s, d in zip(subjects, dist):
            if s not in best or d < best[s]:
                best[s] = float(d)
        return sorted(best.items(), key=lambda kv: kv[1])[:count]
