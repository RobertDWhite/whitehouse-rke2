#!/usr/bin/python3
"""
claude-stats-exporter — Prometheus exporter for local AI coding-assistant usage.

Scrapes, on the Mac it runs on:
  * Claude Code transcripts   ~/.claude/projects/**/*.jsonl
  * Claude Code stats cache   ~/.claude/stats-cache.json           (optional)
  * Codex rollouts            ~/.codex/sessions/**/*.jsonl
  * Cowork sessions           ~/Library/Application Support/Claude/local-agent-mode-sessions/**/local_*.json

and serves them on :9101/metrics for the `claude-code-stats` job in
observability/observability/02-config-prometheus.yaml (one target per Mac,
distinguished by the `host` label set in the scrape config). Metric names,
labels and HELP strings match the original exporter on the primary Mac so
the "AI Code Assistants" (claude-code-obs) and "Claude Code (Solo)"
(claude-code-only) dashboards sum across hosts unchanged.

Install with scripts/install-claude-stats-exporter.sh (launchd, stdlib only,
runs under the system /usr/bin/python3).
"""

import argparse
import glob
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
CODEX_DIR = os.path.join(HOME, ".codex", "sessions")
COWORK_DIR = os.path.join(HOME, "Library", "Application Support", "Claude", "local-agent-mode-sessions")

# USD per 1M tokens: (input, output, cache_read, cache_write). Anthropic list
# prices; cache read = 10% of input (25% on fable-5-1), cache write = 125%.
CLAUDE_PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 2.5, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-mythos-5-1": (10.0, 50.0, 2.5, 12.5),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-7": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-6": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-5": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3, 3.75),
    "claude-sonnet-4-5": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25),
}
# Codex (OpenAI GPT-5 family) — flat estimate: input, output, cached input.
CODEX_PRICE = (1.25, 10.0, 0.125)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read"}
TOP_FILES = 30


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def claude_price(model):
    m = re.sub(r"\[.*\]$", "", model)              # strip "[1m]" suffix
    m = re.sub(r"-\d{8}$", "", m)                  # strip date suffix
    if m in CLAUDE_PRICES:
        return CLAUDE_PRICES[m]
    for k, v in CLAUDE_PRICES.items():             # prefix match (e.g. claude-opus-4-5-2025...)
        if m.startswith(k):
            return v
    return None


def parse_ts(ts):
    """ISO-8601 (Claude/Codex) or epoch-ms (Cowork) -> aware datetime, or None."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None


def local_date(dt):
    return dt.astimezone().strftime("%Y-%m-%d")


def local_hour(dt):
    return dt.astimezone().hour


def short_path(p):
    if not p:
        return ""
    if p == HOME:
        return "~"
    if p.startswith(HOME + "/"):
        return "~" + p[len(HOME):]
    return p


def project_label(cwd):
    """Repo-ish label for a working directory (basename of the checkout for
    anything under GitHub*/, ~-relative otherwise)."""
    if not cwd:
        return "(unknown)"
    parts = cwd.rstrip("/").split("/")
    for i, seg in enumerate(parts):
        if seg.lower().startswith("github") and i + 1 < len(parts):
            return parts[i + 1]
    return short_path(cwd)


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def iter_jsonl(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


# ----------------------------------------------------------------------------
# Claude Code (JSONL transcripts)
# ----------------------------------------------------------------------------
def scan_claude():
    sessions = {}                       # sessionId -> {first,last,messages,cwd}
    seen_msg = set()                    # assistant message ids (streaming dupes)
    seen_tool = set()                   # tool_use ids
    model_tokens = defaultdict(lambda: Counter())
    daily = defaultdict(Counter)        # date -> messages/tool_calls/sessions
    daily_model_tokens = defaultdict(Counter)
    daily_cost = Counter()
    tool_usage = Counter()
    skill_usage = Counter()
    file_edits = Counter()
    file_reads = Counter()
    proj = defaultdict(lambda: {"sessions": set(), "messages": 0, "tool_calls": 0,
                                "edited": set(), "read": set()})
    total_messages = 0
    total_tool_calls = 0
    cost_by_model = Counter()

    files = glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", "*.jsonl"))
    for path in files:
        fallback_sid = os.path.splitext(os.path.basename(path))[0]
        for rec in iter_jsonl(path):
            rtype = rec.get("type")
            if rtype not in ("user", "assistant"):
                continue
            if rec.get("isSidechain"):
                pass                    # sub-agent turns still count as usage
            sid = rec.get("sessionId") or fallback_sid
            dt = parse_ts(rec.get("timestamp"))
            cwd = rec.get("cwd") or ""
            s = sessions.setdefault(sid, {"first": None, "last": None, "messages": 0, "cwd": cwd})
            if dt:
                if s["first"] is None or dt < s["first"]:
                    s["first"] = dt
                if s["last"] is None or dt > s["last"]:
                    s["last"] = dt
            if not s["cwd"] and cwd:
                s["cwd"] = cwd
            pl = project_label(s["cwd"] or cwd)
            proj[pl]["sessions"].add(sid)
            msg = rec.get("message") or {}
            content = msg.get("content")
            day = local_date(dt) if dt else None

            if rtype == "user":
                if rec.get("isMeta"):
                    continue
                # tool_result-only user turns are plumbing, not messages
                if isinstance(content, list) and content and all(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                total_messages += 1
                s["messages"] += 1
                proj[pl]["messages"] += 1
                if day:
                    daily[day]["messages"] += 1
                continue

            # assistant
            mid = msg.get("id") or rec.get("uuid")
            model = msg.get("model") or "unknown"
            if mid not in seen_msg:
                seen_msg.add(mid)
                total_messages += 1
                s["messages"] += 1
                proj[pl]["messages"] += 1
                if day:
                    daily[day]["messages"] += 1
                usage = msg.get("usage") or {}
                inp = usage.get("input_tokens", 0) or 0
                out = usage.get("output_tokens", 0) or 0
                cr = usage.get("cache_read_input_tokens", 0) or 0
                cw = usage.get("cache_creation_input_tokens", 0) or 0
                mt = model_tokens[model]
                mt["input"] += inp
                mt["output"] += out
                mt["cache_read"] += cr
                mt["cache_create"] += cw
                if day:
                    daily_model_tokens[day][model] += inp + out + cr + cw
                price = claude_price(model)
                if price:
                    c = (inp * price[0] + out * price[1] + cr * price[2] + cw * price[3]) / 1e6
                    cost_by_model[model] += c
                    if day:
                        daily_cost[day] += c
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    tid = b.get("id")
                    if tid in seen_tool:
                        continue
                    seen_tool.add(tid)
                    name = b.get("name") or "unknown"
                    inp_ = b.get("input") or {}
                    total_tool_calls += 1
                    tool_usage[name] += 1
                    proj[pl]["tool_calls"] += 1
                    if day:
                        daily[day]["tool_calls"] += 1
                    if name == "Skill" and isinstance(inp_, dict) and inp_.get("skill"):
                        skill_usage[inp_["skill"]] += 1
                    fp = inp_.get("file_path") if isinstance(inp_, dict) else None
                    if fp:
                        if name in EDIT_TOOLS:
                            file_edits[fp] += 1
                            proj[pl]["edited"].add(fp)
                        elif name in READ_TOOLS:
                            file_reads[fp] += 1
                            proj[pl]["read"].add(fp)

    hour_sessions = Counter()
    longest_msgs = 0
    longest_dur = 0.0
    for s in sessions.values():
        if s["first"]:
            hour_sessions[local_hour(s["first"])] += 1
            daily[local_date(s["first"])]["sessions"] += 1
            if s["last"]:
                longest_dur = max(longest_dur, (s["last"] - s["first"]).total_seconds())
        longest_msgs = max(longest_msgs, s["messages"])

    total_tokens = sum(sum(c.values()) for c in model_tokens.values())
    return {
        "sessions": len(sessions),
        "messages": total_messages,
        "tool_calls": total_tool_calls,
        "tokens": total_tokens,
        "model_tokens": model_tokens,
        "daily": daily,
        "daily_model_tokens": daily_model_tokens,
        "daily_cost": daily_cost,
        "cost_by_model": cost_by_model,
        "cost": sum(cost_by_model.values()),
        "tool_usage": tool_usage,
        "skill_usage": skill_usage,
        "file_edits": file_edits,
        "file_reads": file_reads,
        "unique_edited": len(file_edits),
        "unique_read": len(file_reads),
        "projects": proj,
        "hour_sessions": hour_sessions,
        "longest_msgs": longest_msgs,
        "longest_dur": longest_dur,
    }


def load_stats_cache():
    """~/.claude/stats-cache.json (written by `claude`), if present."""
    try:
        with open(os.path.join(CLAUDE_DIR, "stats-cache.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------------------
# Codex (rollout JSONL)
# ----------------------------------------------------------------------------
def codex_source(meta):
    src = (meta.get("source") or "").lower()
    orig = (meta.get("originator") or "").lower()
    if src in ("cli", "vscode", "exec"):
        return src
    if "vscode" in orig or "desktop" in orig:
        return "vscode"
    if "exec" in orig:
        return "exec"
    if "cli" in orig:
        return "cli"
    return "other"


def scan_codex():
    total_sessions = 0
    total_tokens = 0
    total_messages = 0
    total_tool_calls = 0
    by_source_sessions = Counter()
    by_source_tokens = Counter()
    proj_sessions = Counter()
    proj_tokens = Counter()
    daily_sessions = Counter()
    daily_tokens = Counter()
    daily_messages = Counter()
    daily_tool_calls = Counter()
    hour_sessions = Counter()
    tool_usage = Counter()
    cost = 0.0

    for path in glob.glob(os.path.join(CODEX_DIR, "**", "*.jsonl"), recursive=True):
        meta = None
        start = None
        cwd = ""
        last_usage = None
        msgs = 0
        tools = 0
        for rec in iter_jsonl(path):
            rtype = rec.get("type")
            payload = rec.get("payload") or {}
            if rtype == "session_meta":
                meta = payload
                cwd = payload.get("cwd") or cwd
                start = parse_ts(payload.get("timestamp") or rec.get("timestamp"))
            elif rtype == "turn_context":
                cwd = payload.get("cwd") or cwd
            elif rtype == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                if info.get("total_token_usage"):
                    last_usage = info["total_token_usage"]
            elif rtype == "response_item":
                pt = payload.get("type")
                if pt == "message" and payload.get("role") in ("user", "assistant"):
                    msgs += 1
                elif pt == "function_call":
                    tools += 1
                    tool_usage[payload.get("name") or "unknown"] += 1
        if meta is None and start is None:
            continue
        meta = meta or {}
        if start is None:
            # rollout-2026-06-20T14-54-56-<uuid>.jsonl
            m = re.search(r"rollout-(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", os.path.basename(path))
            if m:
                start = datetime.strptime("%sT%s:%s:%s" % m.groups(), "%Y-%m-%dT%H:%M:%S").astimezone()
        src = codex_source(meta)
        pl = project_label(cwd)
        u = last_usage or {}
        tokens = u.get("total_tokens") or ((u.get("input_tokens", 0) or 0) + (u.get("output_tokens", 0) or 0))
        inp = u.get("input_tokens", 0) or 0
        cached = u.get("cached_input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        cost += ((inp - cached) * CODEX_PRICE[0] + cached * CODEX_PRICE[2] + out * CODEX_PRICE[1]) / 1e6

        total_sessions += 1
        total_tokens += tokens
        total_messages += msgs
        total_tool_calls += tools
        by_source_sessions[src] += 1
        by_source_tokens[src] += tokens
        proj_sessions[pl] += 1
        proj_tokens[pl] += tokens
        if start:
            d = local_date(start)
            daily_sessions[d] += 1
            daily_tokens[d] += tokens
            daily_messages[d] += msgs
            daily_tool_calls[d] += tools
            hour_sessions["%02d" % local_hour(start)] += 1

    return {
        "sessions": total_sessions, "tokens": total_tokens, "messages": total_messages,
        "tool_calls": total_tool_calls, "by_source_sessions": by_source_sessions,
        "by_source_tokens": by_source_tokens, "proj_sessions": proj_sessions,
        "proj_tokens": proj_tokens, "daily_sessions": daily_sessions, "daily_tokens": daily_tokens,
        "daily_messages": daily_messages, "daily_tool_calls": daily_tool_calls,
        "hour_sessions": hour_sessions, "tool_usage": tool_usage, "cost": cost,
    }


# ----------------------------------------------------------------------------
# Cowork (Claude desktop local-agent-mode sessions)
# ----------------------------------------------------------------------------
def cowork_project(cwd):
    if not cwd:
        return "(unknown)"
    if "local-agent-mode-sessions" in cwd or cwd.startswith("/sessions/"):
        return "(cowork vm)"
    return project_label(cwd)


def scan_cowork():
    sessions = 0
    projects = set()
    active = 0.0
    by_model = Counter()
    proj_sessions = Counter()
    daily = Counter()
    hourly = Counter()
    for path in glob.glob(os.path.join(COWORK_DIR, "**", "local_*.json"), recursive=True):
        try:
            with open(path) as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(s, dict) or "sessionId" not in s:
            continue
        sessions += 1
        pl = cowork_project(s.get("cwd"))
        projects.add(pl)
        proj_sessions[pl] += 1
        by_model[s.get("model") or "unknown"] += 1
        created = parse_ts(s.get("createdAt"))
        last = parse_ts(s.get("lastActivityAt"))
        if created and last and last > created:
            active += (last - created).total_seconds()
        if created:
            daily[local_date(created)] += 1
            hourly["%02d" % local_hour(created)] += 1
    return {"sessions": sessions, "projects": len(projects), "active": active,
            "by_model": by_model, "proj_sessions": proj_sessions, "daily": daily,
            "hourly": hourly, "project_names": projects}


# ----------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------
class Out:
    def __init__(self):
        self.lines = []

    def family(self, name, help_):
        self.lines.append("# HELP %s %s" % (name, help_))
        self.lines.append("# TYPE %s gauge" % name)

    def sample(self, name, value, **labels):
        if labels:
            lbl = ",".join('%s="%s"' % (k, esc(v)) for k, v in labels.items())
            self.lines.append("%s{%s} %s" % (name, lbl, fmt(value)))
        else:
            self.lines.append("%s %s" % (name, fmt(value)))

    def text(self):
        return "\n".join(self.lines) + "\n"


def fmt(v):
    if isinstance(v, float):
        return "%.4f" % v if v != int(v) else str(int(v))
    return str(v)


def render(cl, cache, cx, cw):
    o = Out()

    # --- claude_stats_* : from stats-cache.json when present, else the JSONL scan
    if cache:
        o.family("claude_stats_total_sessions", "Total CLI sessions (all time)")
        o.sample("claude_stats_total_sessions", cache.get("totalSessions", 0))
        o.family("claude_stats_total_messages", "Total messages (all time)")
        o.sample("claude_stats_total_messages", cache.get("totalMessages", 0))
        ls = cache.get("longestSession") or {}
        o.family("claude_stats_longest_session_messages", "Longest session message count")
        o.sample("claude_stats_longest_session_messages", ls.get("messageCount", 0))
        o.family("claude_stats_longest_session_duration_seconds", "Longest session duration")
        o.sample("claude_stats_longest_session_duration_seconds", (ls.get("duration", 0) or 0) / 1000.0)
        mu = cache.get("modelUsage") or {}
        for key, help_, field in (
                ("claude_stats_model_input_tokens", "Input tokens by model (all time)", "inputTokens"),
                ("claude_stats_model_output_tokens", "Output tokens by model (all time)", "outputTokens"),
                ("claude_stats_model_cache_read_tokens", "Cache read tokens by model", "cacheReadInputTokens"),
                ("claude_stats_model_cache_create_tokens", "Cache creation tokens by model", "cacheCreationInputTokens")):
            o.family(key, help_)
            for model, u in mu.items():
                o.sample(key, (u or {}).get(field, 0), model=model)
        o.family("claude_stats_hour_sessions", "Sessions started by hour of day")
        for h, n in sorted((cache.get("hourCounts") or {}).items(), key=lambda kv: int(kv[0])):
            o.sample("claude_stats_hour_sessions", n, hour=str(int(h)))
        da = cache.get("dailyActivity") or []
        for key, help_, field in (
                ("claude_stats_daily_messages", "Messages per day", "messageCount"),
                ("claude_stats_daily_sessions", "Sessions per day", "sessionCount"),
                ("claude_stats_daily_tool_calls", "Tool calls per day", "toolCallCount")):
            o.family(key, help_)
            for d in da:
                o.sample(key, d.get(field, 0), date=d.get("date"))
        o.family("claude_stats_daily_tokens", "Tokens per day per model")
        for d in cache.get("dailyModelTokens") or []:
            for model, n in (d.get("tokensByModel") or {}).items():
                o.sample("claude_stats_daily_tokens", n, date=d.get("date"), model=model)
    else:
        o.family("claude_stats_total_sessions", "Total CLI sessions (all time)")
        o.sample("claude_stats_total_sessions", cl["sessions"])
        o.family("claude_stats_total_messages", "Total messages (all time)")
        o.sample("claude_stats_total_messages", cl["messages"])
        o.family("claude_stats_longest_session_messages", "Longest session message count")
        o.sample("claude_stats_longest_session_messages", cl["longest_msgs"])
        o.family("claude_stats_longest_session_duration_seconds", "Longest session duration")
        o.sample("claude_stats_longest_session_duration_seconds", cl["longest_dur"])
        for key, help_, field in (
                ("claude_stats_model_input_tokens", "Input tokens by model (all time)", "input"),
                ("claude_stats_model_output_tokens", "Output tokens by model (all time)", "output"),
                ("claude_stats_model_cache_read_tokens", "Cache read tokens by model", "cache_read"),
                ("claude_stats_model_cache_create_tokens", "Cache creation tokens by model", "cache_create")):
            o.family(key, help_)
            for model, c in cl["model_tokens"].items():
                o.sample(key, c[field], model=model)
        o.family("claude_stats_hour_sessions", "Sessions started by hour of day")
        for h in sorted(cl["hour_sessions"]):
            o.sample("claude_stats_hour_sessions", cl["hour_sessions"][h], hour=str(h))
        for key, help_, field in (
                ("claude_stats_daily_messages", "Messages per day", "messages"),
                ("claude_stats_daily_sessions", "Sessions per day", "sessions"),
                ("claude_stats_daily_tool_calls", "Tool calls per day", "tool_calls")):
            o.family(key, help_)
            for d in sorted(cl["daily"]):
                o.sample(key, cl["daily"][d][field], date=d)
        o.family("claude_stats_daily_tokens", "Tokens per day per model")
        for d in sorted(cl["daily_model_tokens"]):
            for model, n in cl["daily_model_tokens"][d].items():
                o.sample("claude_stats_daily_tokens", n, date=d, model=model)

    # --- per project
    P = cl["projects"]
    o.family("claude_project_sessions", "Sessions per project")
    for p, v in P.items():
        o.sample("claude_project_sessions", len(v["sessions"]), project=p)
    o.family("claude_project_messages", "Messages per project")
    for p, v in P.items():
        o.sample("claude_project_messages", v["messages"], project=p)
    o.family("claude_project_tool_calls", "Tool calls per project")
    for p, v in P.items():
        o.sample("claude_project_tool_calls", v["tool_calls"], project=p)
    o.family("claude_project_files_edited", "Unique files edited per project")
    for p, v in P.items():
        o.sample("claude_project_files_edited", len(v["edited"]), project=p)
    o.family("claude_project_files_read", "Unique files read per project")
    for p, v in P.items():
        o.sample("claude_project_files_read", len(v["read"]), project=p)

    o.family("claude_tool_usage_total", "Total tool invocations by tool name")
    for t, n in cl["tool_usage"].most_common():
        o.sample("claude_tool_usage_total", n, tool=t)
    o.family("claude_file_edit_count", "Edit count per file (top 30)")
    for f, n in cl["file_edits"].most_common(TOP_FILES):
        o.sample("claude_file_edit_count", n, file=short_path(f))
    o.family("claude_file_read_count", "Read count per file (top 30)")
    for f, n in cl["file_reads"].most_common(TOP_FILES):
        o.sample("claude_file_read_count", n, file=short_path(f))
    o.family("claude_skill_usage_total", "Skill invocations by name")
    for s, n in cl["skill_usage"].most_common():
        o.sample("claude_skill_usage_total", n, skill=s)

    # --- deep (JSONL) totals
    o.family("claude_deep_total_sessions", "Total sessions (JSONL scan)")
    o.sample("claude_deep_total_sessions", cl["sessions"])
    o.family("claude_deep_total_messages", "Total messages (JSONL scan)")
    o.sample("claude_deep_total_messages", cl["messages"])
    o.family("claude_deep_total_tool_calls", "Total tool calls (JSONL scan)")
    o.sample("claude_deep_total_tool_calls", cl["tool_calls"])
    o.family("claude_deep_unique_files_edited", "Total unique files edited")
    o.sample("claude_deep_unique_files_edited", cl["unique_edited"])
    o.family("claude_deep_unique_files_read", "Total unique files read")
    o.sample("claude_deep_unique_files_read", cl["unique_read"])
    o.family("claude_deep_total_projects", "Total projects")
    o.sample("claude_deep_total_projects", len(P))
    o.family("claude_deep_total_tokens", "Total tokens from JSONL usage data")
    o.sample("claude_deep_total_tokens", cl["tokens"])
    for key, help_, field in (
            ("claude_deep_model_input_tokens", "Input tokens by model (JSONL)", "input"),
            ("claude_deep_model_output_tokens", "Output tokens by model (JSONL)", "output"),
            ("claude_deep_model_cache_read_tokens", "Cache read tokens by model (JSONL)", "cache_read"),
            ("claude_deep_model_cache_create_tokens", "Cache create tokens by model (JSONL)", "cache_create")):
        o.family(key, help_)
        for model, c in cl["model_tokens"].items():
            o.sample(key, c[field], model=model)
    o.family("claude_deep_daily_messages", "Daily messages (JSONL scan)")
    for d in sorted(cl["daily"]):
        o.sample("claude_deep_daily_messages", cl["daily"][d]["messages"], date=d)
    o.family("claude_deep_daily_tool_calls", "Daily tool calls (JSONL scan)")
    for d in sorted(cl["daily"]):
        o.sample("claude_deep_daily_tool_calls", cl["daily"][d]["tool_calls"], date=d)

    o.family("claude_cost_by_model_usd", "Estimated cost in USD by model (JSONL)")
    for model, c in cl["cost_by_model"].items():
        o.sample("claude_cost_by_model_usd", round(c, 4), model=model)
    o.family("claude_total_cost_usd", "Total estimated cost in USD (JSONL)")
    o.sample("claude_total_cost_usd", round(cl["cost"], 4))
    o.family("claude_daily_cost_usd", "Estimated daily cost in USD")
    for d in sorted(cl["daily_cost"]):
        o.sample("claude_daily_cost_usd", round(cl["daily_cost"][d], 4), date=d)

    # --- codex
    o.family("codex_total_sessions", "Total Codex sessions")
    o.sample("codex_total_sessions", cx["sessions"])
    o.family("codex_total_tokens", "Total tokens used by Codex")
    o.sample("codex_total_tokens", cx["tokens"])
    o.family("codex_sessions_by_source", "Sessions by source (cli/vscode/exec)")
    for s, n in cx["by_source_sessions"].items():
        o.sample("codex_sessions_by_source", n, source=s)
    o.family("codex_tokens_by_source", "Tokens by source")
    for s, n in cx["by_source_tokens"].items():
        o.sample("codex_tokens_by_source", n, source=s)
    o.family("codex_project_sessions", "Sessions per project")
    for p, n in cx["proj_sessions"].items():
        o.sample("codex_project_sessions", n, project=p)
    o.family("codex_project_tokens", "Tokens per project")
    for p, n in cx["proj_tokens"].items():
        o.sample("codex_project_tokens", n, project=p)
    o.family("codex_daily_sessions", "Daily Codex sessions")
    for d in sorted(cx["daily_sessions"]):
        o.sample("codex_daily_sessions", cx["daily_sessions"][d], date=d)
    o.family("codex_daily_tokens", "Daily Codex tokens")
    for d in sorted(cx["daily_tokens"]):
        o.sample("codex_daily_tokens", cx["daily_tokens"][d], date=d)
    o.family("codex_hour_sessions", "Sessions by hour of day")
    for h in sorted(cx["hour_sessions"]):
        o.sample("codex_hour_sessions", cx["hour_sessions"][h], hour=h)
    o.family("codex_total_messages", "Total messages (JSONL scan)")
    o.sample("codex_total_messages", cx["messages"])
    o.family("codex_total_tool_calls", "Total tool calls (JSONL scan)")
    o.sample("codex_total_tool_calls", cx["tool_calls"])
    o.family("codex_tool_usage_total", "Tool invocations by name")
    for t, n in cx["tool_usage"].most_common():
        o.sample("codex_tool_usage_total", n, tool=t)
    o.family("codex_daily_messages", "Daily messages (JSONL)")
    for d in sorted(cx["daily_messages"]):
        o.sample("codex_daily_messages", cx["daily_messages"][d], date=d)
    o.family("codex_daily_tool_calls", "Daily tool calls (JSONL)")
    for d in sorted(cx["daily_tool_calls"]):
        o.sample("codex_daily_tool_calls", cx["daily_tool_calls"][d], date=d)

    # --- cowork
    o.family("cowork_total_sessions", "Total Cowork sessions")
    o.sample("cowork_total_sessions", cw["sessions"])
    o.family("cowork_total_projects", "Distinct projects (cwd) used in Cowork")
    o.sample("cowork_total_projects", cw["projects"])
    o.family("cowork_total_active_seconds", "Summed session active time (createdAt..lastActivityAt)")
    o.sample("cowork_total_active_seconds", int(cw["active"]))
    o.family("cowork_sessions_by_model", "Cowork sessions by model")
    for m, n in cw["by_model"].most_common():
        o.sample("cowork_sessions_by_model", n, model=m)
    o.family("cowork_project_sessions", "Cowork sessions per project")
    for p, n in cw["proj_sessions"].most_common():
        o.sample("cowork_project_sessions", n, project=p)
    o.family("cowork_daily_sessions", "Cowork sessions per day")
    for d in sorted(cw["daily"]):
        o.sample("cowork_daily_sessions", cw["daily"][d], date=d)
    o.family("cowork_hour_sessions", "Cowork sessions by hour of day")
    for h in sorted(cw["hourly"]):
        o.sample("cowork_hour_sessions", cw["hourly"][h], hour=h)

    # --- cross-tool aggregates
    all_projects = set(P) | set(cx["proj_sessions"]) | cw["project_names"]
    o.family("ai_total_sessions", "Total sessions across all AI tools")
    o.sample("ai_total_sessions", cl["sessions"] + cx["sessions"] + cw["sessions"])
    o.family("ai_total_messages", "Total messages across all AI tools")
    o.sample("ai_total_messages", cl["messages"] + cx["messages"])
    o.family("ai_total_tool_calls", "Total tool calls across all AI tools")
    o.sample("ai_total_tool_calls", cl["tool_calls"] + cx["tool_calls"])
    o.family("ai_total_tokens", "Total tokens across all AI tools")
    o.sample("ai_total_tokens", cl["tokens"] + cx["tokens"])
    o.family("ai_total_projects", "Total projects across all AI tools")
    o.sample("ai_total_projects", len(all_projects))
    o.family("ai_sessions_by_tool", "Sessions by AI tool")
    o.sample("ai_sessions_by_tool", cl["sessions"], tool="claude")
    o.sample("ai_sessions_by_tool", cx["sessions"], tool="codex")
    o.sample("ai_sessions_by_tool", cw["sessions"], tool="cowork")
    o.family("ai_messages_by_tool", "Messages by AI tool")
    o.sample("ai_messages_by_tool", cl["messages"], tool="claude")
    o.sample("ai_messages_by_tool", cx["messages"], tool="codex")
    o.family("ai_tool_calls_by_tool", "Tool calls by AI tool")
    o.sample("ai_tool_calls_by_tool", cl["tool_calls"], tool="claude")
    o.sample("ai_tool_calls_by_tool", cx["tool_calls"], tool="codex")
    o.family("ai_tokens_by_tool", "Tokens by AI tool")
    o.sample("ai_tokens_by_tool", cl["tokens"], tool="claude")
    o.sample("ai_tokens_by_tool", cx["tokens"], tool="codex")
    o.family("ai_total_cost_usd", "Estimated total cost in USD across all AI tools")
    o.sample("ai_total_cost_usd", round(cl["cost"] + cx["cost"], 4))
    o.family("ai_cost_by_tool_usd", "Cost in USD by AI tool")
    o.sample("ai_cost_by_tool_usd", round(cl["cost"], 4), tool="claude")
    o.sample("ai_cost_by_tool_usd", round(cx["cost"], 4), tool="codex")

    # --- exporter health
    o.family("claude_stats_exporter_scrape_duration_seconds", "Time to rescan all sources")
    o.sample("claude_stats_exporter_scrape_duration_seconds", round(cl.get("_elapsed", 0.0), 3))
    o.family("claude_stats_exporter_last_success_timestamp_seconds", "Unix time of the last successful rescan")
    o.sample("claude_stats_exporter_last_success_timestamp_seconds", int(cl.get("_ts", 0)))
    return o.text()


def collect():
    t0 = time.time()
    cl = scan_claude()
    cache = load_stats_cache()
    cx = scan_codex()
    cw = scan_cowork()
    cl["_elapsed"] = time.time() - t0
    cl["_ts"] = time.time()
    return render(cl, cache, cx, cw)


# ----------------------------------------------------------------------------
# server
# ----------------------------------------------------------------------------
class State:
    text = "# exporter warming up\n"
    lock = threading.Lock()


def refresher(interval):
    while True:
        try:
            body = collect()
            with State.lock:
                State.text = body
        except Exception as e:  # keep serving the last good scrape
            sys.stderr.write("[claude-stats-exporter] collect failed: %r\n" % (e,))
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        with State.lock:
            body = State.text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt_, *args):
        return


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--interval", type=int, default=300, help="rescan interval in seconds")
    ap.add_argument("--once", action="store_true", help="print metrics to stdout and exit")
    args = ap.parse_args()
    if args.once:
        sys.stdout.write(collect())
        return
    threading.Thread(target=refresher, args=(args.interval,), daemon=True).start()
    srv = HTTPServer((args.bind, args.port), Handler)
    sys.stderr.write("[claude-stats-exporter] listening on %s:%d\n" % (args.bind, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
