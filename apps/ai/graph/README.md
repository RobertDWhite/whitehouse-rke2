# graph

Temporal knowledge graph over the vault repo: [Graphiti](https://github.com/getzep/graphiti)
on Neo4j, with an MCP server for agents and a worker that feeds the Forgejo
vault into it. Like `apps/ai/brain`, nothing here describes what is in the vault.

| Piece | What | Where |
| --- | --- | --- |
| `neo4j` | Neo4j Community + APOC, store on Longhorn (`neo4j-data`) | Browser: https://neo4j.internal.white.fm, Bolt in-cluster: `bolt://neo4j.graph.svc.cluster.local:7687` |
| `graphiti-mcp` | Official Graphiti MCP server (`zepai/knowledge-graph-mcp`, pinned by digest), streamable HTTP | https://graphiti.internal.white.fm/mcp (bearer token) |
| `embeddings` | text-embeddings-inference (CPU) serving `Qwen/Qwen3-Embedding-0.6B`, 1024 dims | `http://embeddings.graph.svc.cluster.local:8080/v1` |
| `graphiti-ingest` | `ingest/brain_ingest.py`: vault notes -> Graphiti episodes | metrics on `:9100/metrics` |

## Models

Set in `15-configmap-models.yaml`; the MCP server and the ingest worker read
the same values through `config/graphiti.yaml`.

- **LLM**: `qwen3.8:27b` through the shared Ollama router
  (`ollama-router.ai-stack`). Graphiti calls it with `json_schema` structured
  output (OpenAI-compatible chat completions). The 27B runs on the Mac mini,
  so extraction is slow (minutes per episode). When `qwen-flash` (node-10) is
  up, pointing `LLM_API_URL`/`LLM_MODEL` at it is faster and stronger; any
  OpenAI-compatible endpoint works, including a hosted one (put its key in a
  SOPS secret and set `LLM_API_KEY` from it).
- **Embeddings**: local TEI, CPU only. Changing the embedding model or its
  dimensions invalidates every stored vector: clear the graph and re-ingest.

## MCP access

The Graphiti MCP server has no auth of its own. The gateway enforces it:
SecurityPolicy `graphiti-mcp-auth` accepts `Authorization: Bearer <key>` for
any key in Secret `graphiti-mcp-api-keys` (one entry per client; key name =
client id) and only from LAN/tailnet addresses. The NetworkPolicy admits only
the gateway to the pod.

```sh
claude mcp add --transport http --scope user graphiti \
  https://graphiti.internal.white.fm/mcp --header "Authorization: Bearer <key>"
```

Default group is `brain` (`GRAPHITI_GROUP_ID`), so `search_nodes` and
`search_memory_facts` search the ingested vault unless a tool call names
other groups. Memories an agent adds with `add_memory` land there too unless
it passes a `group_id`.

## Neo4j Browser

Open https://neo4j.internal.white.fm/browser/ and connect to
`bolt+s://neo4j.internal.white.fm:443` as `neo4j` with the password from
Secret `neo4j-auth`. The Browser's Bolt connection is a WebSocket on the same
host; the HTTPRoute sends requests with `Upgrade: websocket` to port 7687 and
everything else to 7474.

## Ingest

`graphiti-ingest` is a long-running loop (every `INTERVAL_SECONDS`, 15 min):

1. Reads the branch head of `FORGEJO_REPO` with a read-only token (Forgejo user
   `brain-sync`, token `graphiti-ingest`, scope `read:repository`). When the head
   moved, or a backlog remains, it downloads the repo archive.
2. Hashes every Markdown note (dot-folders skipped, `EXCLUDE_GLOBS` optional).
   On generated entity pages under `ENTITIES_DIR` everything from the
   `## Mentions` heading on is dropped: the records stay, the long mention
   lists (which only repeat other notes) don't.
3. Deletes the episodes of notes that changed or disappeared.
4. Adds at most `BATCH_SIZE` (10) new or changed notes per cycle, newest first.
   Each note becomes one episode, or several (`path (part i/n)`) when it is
   longer than `MAX_EPISODE_CHARS` (6000). Reference time: a date field in the
   frontmatter or near the top of the note (`DATE_KEYS`), else a date in the
   file name, else the last commit that touched the file.

State (content hash and episode uuids per note) is `/state/state.json` on the
`graphiti-ingest-state` PVC; deleting it makes the worker re-add everything
(remove the old episodes first, or clear the graph). A note that fails
`MAX_ATTEMPTS` (3) times is skipped until it changes. Bump `RENDER_VERSION` to
re-ingest everything after changing how notes are rendered.

Metrics: `graphiti_ingest_last_success_timestamp_seconds`,
`graphiti_ingest_notes{state="total|ingested|pending|failed"}`,
`graphiti_ingest_episodes_added_total`, `graphiti_ingest_errors_total`
(Prometheus job `graphiti-ingest`).

```sh
kubectl -n graph logs deploy/graphiti-ingest --tail=50
kubectl -n graph exec deploy/graphiti-ingest -- python3 -c \
  "import urllib.request as u; print(u.urlopen('http://localhost:9100/metrics').read().decode())"
```

## Secrets (`11-secret.sops.yaml`)

- `neo4j-auth`: `NEO4J_AUTH` (`neo4j/<password>`, only read on first start of
  an empty store) and `NEO4J_PASSWORD`. Changing the password later means
  changing it in Neo4j too (`ALTER CURRENT USER SET PASSWORD ...`).
- `graphiti-mcp-api-keys`: MCP bearer keys, one per client. Copy in 1Password
  vault RKE2.
- `graphiti-ingest`: `FORGEJO_TOKEN`. Revoke it in Forgejo as `brain-sync`
  under Settings -> Applications (`graphiti-ingest`).
