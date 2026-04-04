#!/usr/bin/env python3
"""FreshRSS Daily Digest — pulls articles by category, summarizes each via Ollama."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} references in strings."""
    if isinstance(value, str):
        for key, val in os.environ.items():
            value = value.replace(f"${{{key}}}", val)
        return value
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_config(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return expand_env(raw or {})


# ---------------------------------------------------------------------------
# FreshRSS Greader client
# ---------------------------------------------------------------------------

class FreshRSSClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.api_url = str(cfg["greader_api_url"]).rstrip("/")
        self.username = str(cfg["username"])
        self.api_password = str(cfg["api_password"])
        self.timeout = int(cfg.get("timeout_seconds", 20))
        self.page_size = int(cfg.get("page_size", 200))
        self.max_items = int(cfg.get("max_items", 500))
        self.lookback_hours = int(cfg.get("lookback_hours", 24))
        self.unread_only = bool(cfg.get("unread_only", False))
        self.retry_attempts = int(cfg.get("retry_attempts", 3))
        self.retry_backoff = float(cfg.get("retry_backoff_seconds", 2.0))
        self.verify_tls = bool(cfg.get("verify_tls", True))
        self.session = requests.Session()

    def login(self) -> str:
        url = f"{self.api_url}/accounts/ClientLogin"
        creds = {"Email": self.username, "Passwd": self.api_password}
        resp = self.session.post(url, data=creds, timeout=self.timeout, verify=self.verify_tls)
        if resp.status_code in {404, 405}:
            resp = self.session.get(url, params=creds, timeout=self.timeout, verify=self.verify_tls)
        if resp.status_code >= 400:
            raise RuntimeError(f"FreshRSS login failed ({resp.status_code}): {resp.text[:300]}")
        for line in resp.text.splitlines():
            if line.startswith("Auth="):
                return line.split("=", 1)[1].strip()
        raise RuntimeError("Auth token not found in ClientLogin response")

    def fetch_articles(self) -> List[Dict[str, Any]]:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return self._fetch_once()
            except requests.RequestException as exc:
                last_err = exc
                if attempt >= self.retry_attempts:
                    break
                wait = self.retry_backoff * (2 ** (attempt - 1))
                print(f"[warn] fetch attempt {attempt} failed: {exc}, retry in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
        raise RuntimeError(f"FreshRSS fetch failed after {self.retry_attempts} attempts: {last_err}")

    def _fetch_once(self) -> List[Dict[str, Any]]:
        auth = self.login()
        headers = {"Authorization": f"GoogleLogin auth={auth}"}
        stream = "user/-/state/com.google/reading-list"
        url = f"{self.api_url}/reader/api/0/stream/contents/{stream}"

        cutoff = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=self.lookback_hours)).timestamp())
        continuation = ""
        articles: List[Dict[str, Any]] = []

        while len(articles) < self.max_items:
            params: Dict[str, Any] = {
                "n": min(self.page_size, self.max_items - len(articles)),
                "ck": str(int(time.time())),
                "output": "json",
                "ot": cutoff,
            }
            if continuation:
                params["c"] = continuation
            if self.unread_only:
                params["xt"] = "user/-/state/com.google/read"

            resp = self.session.get(url, params=params, headers=headers,
                                    timeout=self.timeout, verify=self.verify_tls)
            if resp.status_code >= 400:
                raise RuntimeError(f"Stream fetch failed ({resp.status_code}): {resp.text[:300]}")

            payload = resp.json()
            items = payload.get("items") or []
            if not items:
                break

            for item in items:
                articles.append(self._normalize(item))

            continuation = str(payload.get("continuation") or "").strip()
            if not continuation:
                break

        # Deduplicate by URL
        seen: Dict[str, Dict[str, Any]] = {}
        for a in articles:
            key = a.get("url") or a.get("id", "")
            if key not in seen:
                seen[key] = a
        return sorted(seen.values(), key=lambda x: x.get("published", 0), reverse=True)

    @staticmethod
    def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
        content = ""
        if item.get("summary", {}).get("content"):
            content = item["summary"]["content"]
        elif item.get("content", {}).get("content"):
            content = item["content"]["content"]

        categories = []
        for cat in item.get("categories", []):
            if "/label/" in cat:
                categories.append(cat.rsplit("/label/", 1)[-1])
            elif "/state/" not in cat:
                categories.append(cat)

        return {
            "id": item.get("id", ""),
            "title": item.get("title", "(untitled)"),
            "url": (item.get("canonical") or [{}])[0].get("href")
                   or (item.get("alternate") or [{}])[0].get("href", ""),
            "published": item.get("published", 0),
            "source": (item.get("origin") or {}).get("title", ""),
            "categories": categories,
            "text": strip_html(content),
        }


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Group & filter articles
# ---------------------------------------------------------------------------

BI_PREFIXES = ("entity:", "event:", "signal:", "impact:", "horizon:", "sector:")


def group_by_category(articles: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for article in articles:
        cats = article.get("categories") or ["Uncategorized"]
        if not cats:
            cats = ["Uncategorized"]
        for cat in cats:
            # Skip BI pipeline auto-tags
            if cat.lower().startswith(BI_PREFIXES):
                continue
            groups.setdefault(cat, []).append(article)
    return groups


# ---------------------------------------------------------------------------
# Ollama summarizer
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a daily news briefing writer. You will receive a batch of articles "
    "from one RSS feed category. Write a SHORT, scannable summary — like a morning "
    "briefing someone reads on their phone.\n\n"
    "Rules:\n"
    "- Write 3-6 bullet points maximum for the whole section\n"
    "- Each bullet is 1-2 sentences covering one key story or theme\n"
    "- Merge related articles into one bullet\n"
    "- Lead each bullet with the most important fact\n"
    "- SKIP: court case metadata, document download notices, routine filings, "
    "procedural updates, press release boilerplate, opinion pieces, listicles\n"
    "- Only include stories with real substance — policy changes, major events, "
    "significant business moves, things a busy person needs to know\n"
    "- If most articles are noise, write fewer bullets. If ALL are noise, "
    "reply with exactly: (nothing noteworthy)\n"
    "- No headers, no intro, no closing — just the bullets\n"
    "- Use plain text, not markdown"
)

MAX_ARTICLES_PER_CATEGORY = 20


class OllamaSummarizer:
    def __init__(self, cfg: Dict[str, Any]):
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        self.model = str(cfg.get("model", ""))
        self.api_key = str(cfg.get("api_key", ""))
        self.temperature = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens", 4000))
        self.timeout = int(cfg.get("timeout_seconds", 120))
        self.max_article_chars = int(cfg.get("max_article_chars", 2000))
        self.system_prompt = str(cfg.get("system_prompt", SYSTEM_PROMPT))
        self.session = requests.Session()

        if not self.base_url or not self.model:
            raise RuntimeError("ai.base_url and ai.model are required")

    def summarize_category(self, category: str, articles: List[Dict[str, Any]]) -> str:
        # Cap articles so we don't overwhelm the model
        capped = articles[:MAX_ARTICLES_PER_CATEGORY]
        article_texts = []
        for i, a in enumerate(capped, 1):
            text = a.get("text", "")[:self.max_article_chars]
            title = a.get("title", "(untitled)")
            article_texts.append(f"{i}. {title}\n{text}")

        user_content = (
            f"Category: {category} ({len(articles)} articles"
            + (f", showing newest {len(capped)}" if len(capped) < len(articles) else "")
            + ")\n\n"
            + "\n\n".join(article_texts)
        )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                print(f"[warn] Ollama error for '{category}' ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                return f"({len(articles)} articles — AI summary unavailable)"
            data = resp.json()
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return content or f"({len(articles)} articles — empty AI response)"
        except (requests.RequestException, ValueError) as exc:
            print(f"[warn] Ollama failed for '{category}': {exc}", file=sys.stderr)
            return f"({len(articles)} articles — AI summary unavailable)"


# ---------------------------------------------------------------------------
# Digest builder — single clean message
# ---------------------------------------------------------------------------

def order_categories(
    groups: Dict[str, List[Dict[str, Any]]],
    category_order: Optional[List[str]] = None,
) -> List[str]:
    ordered = []
    if category_order:
        for cat in category_order:
            if cat in groups:
                ordered.append(cat)
    for cat in sorted(groups.keys()):
        if cat not in ordered:
            ordered.append(cat)
    return ordered


def summarize_all(
    groups: Dict[str, List[Dict[str, Any]]],
    summarizer: OllamaSummarizer,
    ordered: List[str],
) -> Dict[str, str]:
    summaries: Dict[str, str] = {}
    for cat in ordered:
        articles = groups[cat]
        print(f"[info] Summarizing '{cat}' ({len(articles)} articles)...", file=sys.stderr)
        summaries[cat] = summarizer.summarize_category(cat, articles)
    return summaries


def build_digest(
    groups: Dict[str, List[Dict[str, Any]]],
    summaries: Dict[str, str],
    ordered: List[str],
    date_str: str,
) -> str:
    """Build a single clean digest string."""
    total = sum(len(groups[c]) for c in ordered)
    lines: List[str] = []
    lines.append(f"Daily Digest — {date_str} ({total} articles)\n")

    for cat in ordered:
        n = len(groups[cat])
        lines.append(f"{cat} ({n})")
        lines.append(summaries.get(cat, ""))
        lines.append("")

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Markdown → HTML for Matrix
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"\*(.+?)\*")
_BULLET_RE = re.compile(r"^[-•]\s+", re.MULTILINE)


def digest_to_html(digest: str) -> str:
    """Convert the digest to clean HTML for Matrix."""
    lines = digest.strip().split("\n")
    html_parts: List[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if in_list and not (stripped.startswith("- ") or stripped.startswith("• ")):
            html_parts.append("</ul>")
            in_list = False

        if not stripped:
            continue

        # First line = title
        if not html_parts:
            html_parts.append(f"<b>{_esc(stripped)}</b>")
            continue

        # Category header: line that ends with (N) pattern
        if re.match(r"^.+\(\d+\)$", stripped):
            html_parts.append(f"<br><b>{_esc(stripped)}</b>")
            continue

        # Bullet points
        if stripped.startswith("- ") or stripped.startswith("• "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_esc(stripped[2:])}</li>")
            continue

        # Regular text
        html_parts.append(f"{_esc(stripped)}")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _esc(text: str) -> str:
    return html.escape(text)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_file(digest: str, output_dir: Path, date_str: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"digest-{date_str}.md"
    path.write_text(digest, encoding="utf-8")
    return path


def post_to_matrix(webhook_url: str, digest: str, timeout: int = 30) -> None:
    """Post the entire digest as one plain text message."""
    payload = {"text": digest}
    resp = requests.post(webhook_url, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Hookshot webhook failed ({resp.status_code}): {resp.text[:300]}")


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

def prune_old_digests(output_dir: Path, keep_days: int) -> None:
    if keep_days <= 0 or not output_dir.exists():
        return
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    for f in output_dir.glob("digest-*.md"):
        try:
            date_part = f.stem.replace("digest-", "")
            file_date = dt.datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            if file_date < cutoff:
                f.unlink()
                print(f"[info] Pruned old digest: {f.name}", file=sys.stderr)
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FreshRSS Daily Digest via Ollama")
    parser.add_argument("--config", default="/config/config.yaml", help="Path to config YAML")
    parser.add_argument("--dry-run", action="store_true", help="Print digest to stdout only")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[error] Config not found: {config_path}", file=sys.stderr)
        return 2

    cfg = load_config(config_path)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    # --- Fetch ---
    print("[info] Fetching articles from FreshRSS...", file=sys.stderr)
    client = FreshRSSClient(cfg["freshrss"])
    articles = client.fetch_articles()
    print(f"[ok] Fetched {len(articles)} articles", file=sys.stderr)

    if not articles:
        print("[ok] No articles in lookback window", file=sys.stderr)
        return 0

    # --- Group & filter ---
    groups = group_by_category(articles)

    digest_cfg = cfg.get("digest") or {}
    include = digest_cfg.get("include_categories")
    exclude = digest_cfg.get("exclude_categories", [])
    if include:
        include_lower = [c.lower() for c in include]
        groups = {k: v for k, v in groups.items() if k.lower() in include_lower}
    if exclude:
        exclude_lower = [c.lower() for c in exclude]
        groups = {k: v for k, v in groups.items() if k.lower() not in exclude_lower}

    if not groups:
        print("[ok] No categories after filtering", file=sys.stderr)
        return 0

    print(f"[ok] {len(groups)} categories: {', '.join(sorted(groups.keys()))}", file=sys.stderr)

    # --- Summarize ---
    summarizer = OllamaSummarizer(cfg["ai"])
    ordered = order_categories(groups, digest_cfg.get("category_order", []))
    summaries = summarize_all(groups, summarizer, ordered)
    digest = build_digest(groups, summaries, ordered, date_str)

    if args.dry_run:
        print(digest)
        return 0

    # --- Write file ---
    output_dir = Path(args.output_dir or cfg.get("output", {}).get("directory", "/data/digests"))
    path = write_file(digest, output_dir, date_str)
    print(f"[ok] Digest written: {path}", file=sys.stderr)

    # --- Post to Matrix ---
    matrix_cfg = cfg.get("matrix") or {}
    webhook_url = matrix_cfg.get("webhook_url", "")
    if webhook_url:
        try:
            timeout = int(matrix_cfg.get("timeout_seconds", 30))
            post_to_matrix(webhook_url, digest, timeout)
            print("[ok] Posted to Matrix", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] Matrix post failed: {exc}", file=sys.stderr)

    # --- Retention ---
    keep_days = int(cfg.get("output", {}).get("keep_days", 30))
    prune_old_digests(output_dir, keep_days)

    print(f"[ok] Done — {date_str}, {sum(len(v) for v in groups.values())} articles, {len(groups)} categories", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
