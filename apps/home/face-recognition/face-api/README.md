# face-api

CompreFace-compatible face recognition for Double Take, replacing CompreFace
1.2.0 (unmaintained since 2023; Debian 11/Python 3.8/MXNet image with ~170
critical CVEs).

- **Models:** InsightFace `buffalo_l` (SCRFD-10GF detector, ArcFace R50 /
  WebFace600K recognizer) on onnxruntime CPU. The zip is fetched at build time
  and checksum-pinned. InsightFace's pretrained models are licensed for
  non-commercial research use only.
- **Code:** `app/engine.py` reimplements insightface's SCRFD decoding and
  `norm_crop` alignment without the `insightface` package; on test images it
  matches the reference exactly (same boxes/scores, embedding cosine 1.0).
- **API:** the CompreFace recognition endpoints Double Take and the warmer use:
  `POST /api/v1/recognition/recognize`, `POST|GET|DELETE
  /api/v1/recognition/faces`, `DELETE /api/v1/recognition/faces/{id}`,
  `/api/v1/recognition/subjects`. Same `x-api-key` (Double Take's
  `compreface_key`), response shapes and error codes (`code: 28` = no face, HTTP
  400). `prediction_count` returns distinct subjects (CompreFace returned
  examples); Double Take only reads the first.
- **Gallery:** SQLite (`/data/faces.db`) plus the example images
  (`/data/images/<image_id>.jpg`, kept so the gallery can be re-embedded if
  the model changes). Imported from CompreFace's `front-door` model
  (854 examples, 23 subjects) with `python -m app.importer`.
- **Similarity scale:** `similarity = (tanh((c0 - d) * c1) + 1) / 2`, with
  `d` the euclidean distance between L2-normalised embeddings (CompreFace's
  formula). `c0`/`c1` (`SIM_C0`/`SIM_C1`) were least-squares fitted to the
  scores CompreFace gave the same faces, so Double Take's `confidence`
  thresholds (match 60, unknown 40) keep their meaning.
- **Detection threshold:** SCRFD scores faces lower than CompreFace's
  RetinaFace; Double Take uses `det_prob_threshold: 0.55` (≈ CompreFace 0.7).

Build (no CI; internal registry, pin the digest in the kustomization):

    docker buildx build --builder k8s-builder --platform linux/amd64 \
      -t registry.internal.white.fm/face-api:<ver> --push .
