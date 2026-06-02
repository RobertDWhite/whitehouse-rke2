# congress-mcp

FastMCP HTTP server exposing the self-hosted congressional stock-trading tracker
(`congress-trades`) to LLM clients. Bearer-token gated, internal-only.

- **Endpoint:** `https://congress-mcp.internal.white.fm/mcp`
- **Auth:** `Authorization: Bearer <MCP_TOKEN>` (see `11-secret.sops.yaml`)
- **Upstream:** `congress-api.congress-trades.svc.cluster.local:8000` (read-only API)

## Tools

| Tool | Upstream | Purpose |
|------|----------|---------|
| `query_trades` | `/api/trades` | Search disclosed trades by ticker/member/party/signal/amount/date |
| `search_members` | `/api/members` | Find a member's id + party/district/net-worth/counts |
| `member_track_record` | `/api/members/{id}` | Full profile, committees, sector mix, excess vs SPY |
| `top_performers` | `/api/leaderboard` | Leaderboard by performance/volume/activity/late |
| `trade_ideas` | `/api/ideas` | Ranked, disclaimer-wrapped ideas over a window |
| `recent_signals` | `/api/signals` | Cluster buys, large, options, late, conflict, corp_event |
| `ticker_detail` | `/api/tickers/{sym}` | Company, live price, sentiment, who's trading it |
| `ai_summary` | `/api/ai/summary` | LLM-grounded summary + watchlist |
| `strategies` / `follow_strategy` | `/api/strategies` | Backtested follow-strategies vs SPY |
| `portfolio_overlap` | `/api/portfolio/overlap` | Paper portfolio vs congressional pressure |
| `market_overview` | `/api/stats` | Dashboard snapshot (hot tickers, top traders) |

All tools are read-only. Data carries the STOCK Act 45-day disclosure lag; returns are measured
from the disclosure date and benchmarked vs SPY. Informational, not investment advice.

## Build

```sh
docker buildx build --builder monicabuilder --platform linux/amd64 --load \
  -t registry.internal.white.fm/congress-mcp:<tag> src/
docker push registry.internal.white.fm/congress-mcp:<tag>
```

Then bump `newTag` in `kustomization.yaml`; ArgoCD syncs.
