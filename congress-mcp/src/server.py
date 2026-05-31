import os
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

CONGRESS_BASE_URL = os.environ.get(
    "CONGRESS_BASE_URL", "http://congress-api.congress-trades.svc.cluster.local:8000"
)
MCP_TOKEN = os.environ.get("MCP_TOKEN")
PORT = int(os.environ.get("PORT", "8080"))

INSTRUCTIONS = """\
Tools for the self-hosted US congressional stock-trading tracker (congress.white.fm).

Data is self-parsed from House Clerk + Senate eFD disclosures plus a live feed. Everything is
informational, NOT investment advice: members disclose trades up to 45 days late (STOCK Act), so
returns are measured from the disclosure date, not the trade date, and are benchmarked vs SPY.

Typical flows:
  - "How has <member> done?"  -> search_members(q=...) to get the id, then member_track_record(id).
  - "What are people buying?"  -> trade_ideas() or market_overview() (hot tickers / net pressure).
  - "Any strong signals?"  -> recent_signals() (cluster buys, large, options, late, conflicts).
  - "Which follow-strategy beats SPY?"  -> strategies(), then follow_strategy(key) for the curve.
  - "What does the AI make of it?"  -> ai_summary() (grounded summary + watchlist candidates).
Conviction is a 0-100 score; treat >=50 as high. Amounts are disclosed as ranges.
"""

mcp = FastMCP("congress-trades", instructions=INSTRUCTIONS, host="0.0.0.0", port=PORT)

_client = httpx.AsyncClient(base_url=CONGRESS_BASE_URL, timeout=60.0)


async def _get(path: str, params: dict | None = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    resp = await _client.get(path, params=clean)
    if resp.status_code >= 400:
        raise RuntimeError(f"congress GET {path} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json()


@mcp.tool()
async def query_trades(
    ticker: str | None = None,
    member_id: int | None = None,
    party: str | None = None,
    chamber: str | None = None,
    state: str | None = None,
    transaction_type: str | None = None,
    signal: str | None = None,
    min_amount: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    sort: str = "transaction_date",
    limit: int = 50,
) -> Any:
    """Search disclosed congressional trades. Filter by ticker, member_id, party
    (Democrat|Republican|Independent), chamber (house|senate), state, transaction_type
    (purchase|sale|exchange), signal (cluster_buy|large|options|late_disclosure|anomaly|conflict),
    min_amount (USD), and a date range on the transaction date (YYYY-MM-DD). `q` is free-text over
    asset/member/ticker. sort is transaction_date|disclosure_date|amount. Returns enriched rows with
    conviction, return_pct and excess vs SPY."""
    return await _get("/api/trades", {
        "ticker": ticker, "member_id": member_id, "party": party, "chamber": chamber,
        "state": state, "transaction_type": transaction_type, "signal": signal,
        "min_amount": min_amount, "start_date": start_date, "end_date": end_date, "q": q,
        "sort": sort, "order": "desc", "limit": min(limit, 500),
    })


@mcp.tool()
async def trade_provenance(trade_id: int) -> Any:
    """Full row-level provenance for a trade: filing metadata, parse status, source URL,
    signal details, source priority, and reconciliation issues."""
    return await _get(f"/api/trades/{trade_id}")


@mcp.tool()
async def search_members(
    q: str | None = None,
    party: str | None = None,
    chamber: str | None = None,
    state: str | None = None,
    limit: int = 25,
) -> Any:
    """Find members of Congress by name (`q`), party, chamber, or state. Returns each member's id
    (use it with member_track_record), party, district, net worth, and trade counts."""
    return await _get("/api/members", {
        "q": q, "party": party, "chamber": chamber, "state": state, "limit": min(limit, 1000),
    })


@mcp.tool()
async def member_track_record(member_id: int) -> Any:
    """Full profile + trading history for one member: party/state/district, committees, estimated
    net worth, disclosure-lag stats, sector mix, recent trades, and weighted excess return vs SPY.
    Get the id from search_members first."""
    return await _get(f"/api/members/{member_id}")


@mcp.tool()
async def top_performers(metric: str = "performance", min_trades: int = 5, limit: int = 25) -> Any:
    """Leaderboard of members. metric = performance (weighted excess vs SPY) | volume | activity |
    late (worst disclosure lag). min_trades filters out small samples."""
    return await _get("/api/leaderboard", {
        "metric": metric, "min_trades": min_trades, "limit": min(limit, 200),
    })


@mcp.tool()
async def trade_ideas(window: int = 90, party: str | None = None, chamber: str | None = None) -> Any:
    """Ranked, disclaimer-wrapped 'ideas' derived from recent disclosures over the last `window`
    days (most-bought, cluster accumulation, high conviction). Informational only — not advice."""
    return await _get("/api/ideas", {"window": min(window, 365), "party": party, "chamber": chamber})


@mcp.tool()
async def recent_signals(signal_type: str | None = None, limit: int = 50) -> Any:
    """Recent trades carrying notable signals, highest summed score first. signal_type =
    cluster_buy | cluster_sell | large | options | late_disclosure | anomaly | conflict |
    corp_event. Omit to get all signal types."""
    return await _get("/api/signals", {"signal_type": signal_type, "limit": min(limit, 300)})


@mcp.tool()
async def ticker_detail(symbol: str) -> Any:
    """Everything disclosed about one ticker: company/sector, live price + retail sentiment, party
    split of traders, and the full list of congressional trades in that name."""
    return await _get(f"/api/tickers/{symbol.upper()}")


@mcp.tool()
async def ticker_price_context(symbol: str, days: int = 365) -> Any:
    """Daily price bars and disclosure markers for a ticker. Disclosure markers are public
    disclosure dates, not transaction dates."""
    return await _get(f"/api/tickers/{symbol.upper()}/bars", {"days": days})


@mcp.tool()
async def ticker_sec_events(symbol: str) -> Any:
    """Recent SEC 8-K and Form 4 events matched to a tracked ticker by CIK."""
    return await _get(f"/api/tickers/{symbol.upper()}/events")


@mcp.tool()
async def ai_summary(window: int = 7) -> Any:
    """LLM-generated, source-grounded summary of insider activity over the last `window` days, with
    observations and a watchlist of accumulation candidates. Generated by the local Ollama model."""
    return await _get("/api/ai/summary", {"window": window})


@mcp.tool()
async def strategies() -> Any:
    """List backtested follow-strategies (All Congress, Democrats, Republicans, Top performers,
    High-conviction basket) with total return and excess vs SPY. Entry = first close on/after the
    disclosure date, so the 45-day STOCK Act lag is baked in."""
    return await _get("/api/strategies")


@mcp.tool()
async def follow_strategy(key: str) -> Any:
    """Detail for one strategy: equity curve vs SPY, current holdings, and metrics. key = all |
    democrat | republican | top_performers | high_conviction."""
    return await _get(f"/api/strategies/{key}")


@mcp.tool()
async def portfolio_overlap() -> Any:
    """Compare the user's paper portfolio against congressional activity: which of your holdings
    members are buying or selling, and net pressure on each."""
    return await _get("/api/portfolio/overlap")


@mcp.tool()
async def market_overview() -> Any:
    """Dashboard snapshot: total/recent trade counts and volume with deltas, house/senate split,
    hot tickers (last 7d, buy/sell pressure), most-active traders, and most-traded tickers."""
    return await _get("/api/stats")


@mcp.tool()
async def data_freshness() -> Any:
    """Ingest health: last successful run per source, stale-source flags, latest disclosure date,
    and source row counts."""
    return await _get("/api/status")


@mcp.tool()
async def committee_exposure() -> Any:
    """Committee-level exposure view: member counts, traded volume, trade counts, and oversight
    sectors inferred from committee assignments."""
    return await _get("/api/committees")


@mcp.tool()
async def policy_context(ticker: str | None = None, member_id: int | None = None, days: int = 120) -> Any:
    """Congress.gov legislative context near trading activity. Filter by ticker sector or member.
    Context only; does not imply causality."""
    return await _get("/api/legislative-events", {"ticker": ticker, "member_id": member_id, "days": days})


@mcp.tool()
async def data_reconciliation() -> Any:
    """Parser/comparison-feed data-quality canaries: missing primary rows and unparsed filings."""
    return await _get("/api/reconciliation")


@mcp.tool()
async def disclosure_lag_analytics(days: int = 365) -> Any:
    """Disclosure-lag analytics: histogram, late rate, slowest members, chamber/party splits,
    and recent 45+ day lag trades."""
    return await _get("/api/analytics/disclosure-lag", {"days": days})


class TokenAuth(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if MCP_TOKEN and request.url.path != "/healthz":
            if request.headers.get("authorization", "") != f"Bearer {MCP_TOKEN}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def main() -> None:
    app = mcp.streamable_http_app()
    app.add_middleware(TokenAuth)
    app.add_route("/healthz", lambda _req: PlainTextResponse("ok"), methods=["GET"])
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
