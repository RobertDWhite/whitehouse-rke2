from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from .db import init_db
from .metrics import register as register_metrics
from .routers import (
    ai, context, feeds, filings, ideas, leaderboard, members, portfolio, signals_api,
    stats, strategies, tickers, trades, watchlist,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    register_metrics()
    yield


app = FastAPI(title="Congress Trades", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/metrics", make_asgi_app())

app.include_router(trades.router, prefix="/api")
app.include_router(members.router, prefix="/api")
app.include_router(tickers.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(filings.router, prefix="/api")
app.include_router(signals_api.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(ideas.router, prefix="/api")
app.include_router(leaderboard.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(feeds.router, prefix="/api")
app.include_router(context.router, prefix="/api")
