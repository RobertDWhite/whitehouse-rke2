from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .ai import draft_reply
from .asc import ASCError, client_from_env
from .config import load_config

app = FastAPI(title="App Store Reviews")
cfg = load_config()
asc = client_from_env()


@app.exception_handler(ASCError)
async def asc_error_handler(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/apps")
def get_apps():
    return {"apps": asc.list_apps()}


@app.get("/api/apps/{app_id}/reviews")
def get_reviews(app_id: str, cursor: str | None = None):
    return asc.list_reviews(app_id, cursor=cursor)


@app.post("/api/reviews/{review_id}/response")
def put_response(review_id: str, body: str = Body(..., embed=True)):
    body = body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Response body cannot be empty.")
    asc.put_response(review_id, body)
    return {"status": "ok"}


@app.delete("/api/reviews/{review_id}/response")
def delete_response(review_id: str):
    response_id = asc.get_response_id(review_id)
    if not response_id:
        raise HTTPException(status_code=404, detail="No response exists for this review.")
    asc.delete_response(response_id)
    return {"status": "ok"}


@app.post("/api/reviews/{review_id}/draft")
def draft(review: dict = Body(...)):
    try:
        return draft_reply(cfg, review)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
