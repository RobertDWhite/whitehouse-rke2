#!/usr/bin/env python3
"""FreshRSS Daily Digest — pulls articles by category, summarizes each via Ollama."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import html

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
# HTML stripping (minimal, no extra deps)
# ---------------------------------------------------------------------------

import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Group articles by category
# ---------------------------------------------------------------------------

def group_by_category(articles: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for article in articles:
        cats = article.get("categories") or ["Uncategorized"]
        if not cats:
            cats = ["Uncategorized"]
        for cat in cats:
            groups.setdefault(cat, []).append(article)
    return groups


# ---------------------------------------------------------------------------
# Ollama summarizer
# ---------------------------------------------------------------------------

class OllamaSummarizer:
    def __init__(self, cfg: Dict[str, Any]):
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        self.model = str(cfg.get("model", ""))
        self.api_key = str(cfg.get("api_key", ""))
        self.temperature = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens", 2000))
        self.timeout = int(cfg.get("timeout_seconds", 120))
        self.max_article_chars = int(cfg.get("max_article_chars", 3000))
        self.system_prompt = str(cfg.get("system_prompt",
            "You are a concise news analyst. Summarize the following articles from an RSS feed category "
            "into a coherent daily briefing. Highlight key themes, important developments, and anything "
            "noteworthy. Use markdown formatting with bullet points. Be concise but thorough."
        ))
        self.session = requests.Session()

        if not self.base_url or not self.model:
            raise RuntimeError("ai.base_url and ai.model are required")

    def summarize_category(self, category: str, articles: List[Dict[str, Any]]) -> str:
        article_texts = []
        for i, a in enumerate(articles, 1):
            text = a.get("text", "")[:self.max_article_chars]
            source = a.get("source", "unknown")
            title = a.get("title", "(untitled)")
            article_texts.append(f"[{i}] {title} (via {source})\n{text}")

        user_content = (
            f"Category: {category}\n"
            f"Number of articles: {len(articles)}\n\n"
            + "\n\n---\n\n".join(article_texts)
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                print(f"[warn] Ollama error for '{category}' ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                return self._fallback_summary(category, articles)
            data = resp.json()
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if not content:
                return self._fallback_summary(category, articles)
            return content
        except (requests.RequestException, ValueError) as exc:
            print(f"[warn] Ollama failed for '{category}': {exc}", file=sys.stderr)
            return self._fallback_summary(category, articles)

    @staticmethod
    def _fallback_summary(category: str, articles: List[Dict[str, Any]]) -> str:
        lines = [f"*AI summary unavailable — {len(articles)} article(s):*\n"]
        for a in articles[:20]:
            url = a.get("url", "")
            title = a.get("title", "(untitled)")
            source = a.get("source", "")
            lines.append(f"- **{title}** ({source}) — {url}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Digest builder
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
    """Summarize each category, return {category: summary_text}."""
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
    sections: List[str] = []
    sections.append(f"# Daily RSS Digest — {date_str}\n")

    total_articles = sum(len(arts) for arts in groups.values())
    sections.append(f"**{total_articles} articles** across **{len(groups)} categories**\n")
    sections.append("---\n")

    for cat in ordered:
        articles = groups[cat]
        sections.append(f"## {cat} ({len(articles)} articles)\n")
        sections.append(summaries.get(cat, ""))
        sections.append("")

    sections.append("---\n")
    sections.append("### Sources\n")
    for cat in ordered:
        for a in groups[cat]:
            title = a.get("title", "(untitled)")
            url = a.get("url", "")
            source = a.get("source", "")
            if url:
                sections.append(f"- [{title}]({url}) — *{source}*")
            else:
                sections.append(f"- {title} — *{source}*")

    return "\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Markdown → HTML (lightweight, no extra deps)
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"\*(.+?)\*")


def md_to_html(md: str) -> str:
    """Convert simple markdown to HTML for Matrix formatted_body."""
    lines = md.split("\n")
    html_lines: List[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Close list if we left bullet context
        if in_list and not stripped.startswith("- "):
            html_lines.append("</ul>")
            in_list = False

        if not stripped or stripped == "---":
            if stripped == "---":
                html_lines.append("<hr>")
            continue

        # Headers
        if stripped.startswith("### "):
            html_lines.append(f"<h4>{_inline(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h3>{_inline(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h2>{_inline(stripped[2:])}</h2>")
        elif stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline(text: str) -> str:
    """Convert inline markdown (bold, italic, links) to HTML."""
    # Process markdown syntax before escaping so patterns still match
    # Links: [text](url) — extract before escaping
    text = _MD_LINK_RE.sub(r'%%LINK_START%%\1%%LINK_MID%%\2%%LINK_END%%', text)
    # Bold: **text**
    text = _MD_BOLD_RE.sub(r"%%BOLD_START%%\1%%BOLD_END%%", text)
    # Italic: *text*
    text = _MD_ITALIC_RE.sub(r"%%EM_START%%\1%%EM_END%%", text)
    # Now HTML-escape the plain text parts
    text = html.escape(text)
    # Restore the HTML tags from placeholders
    text = text.replace("%%LINK_START%%", '<a href="').replace("%%LINK_MID%%", '">').replace("%%LINK_END%%", "</a>")
    text = text.replace("%%BOLD_START%%", "<strong>").replace("%%BOLD_END%%", "</strong>")
    text = text.replace("%%EM_START%%", "<em>").replace("%%EM_END%%", "</em>")
    return text


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_file(digest: str, output_dir: Path, date_str: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"digest-{date_str}.md"
    path.write_text(digest, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Matrix (via Hookshot webhook)
# ---------------------------------------------------------------------------

def send_hookshot(webhook_url: str, markdown: str, timeout: int = 30) -> None:
    """Post a single message to a Matrix room via Hookshot generic webhook."""
    html_body = md_to_html(markdown)
    payload = {
        "msgtype": "m.notice",
        "body": markdown,
        "format": "org.matrix.custom.html",
        "formatted_body": html_body,
    }
    resp = requests.post(webhook_url, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Hookshot webhook failed ({resp.status_code}): {resp.text[:300]}")


def post_to_matrix(
    webhook_url: str,
    groups: Dict[str, List[Dict[str, Any]]],
    category_summaries: Dict[str, str],
    ordered_categories: List[str],
    date_str: str,
    timeout: int = 30,
) -> None:
    """Post digest to Matrix as a series of messages (header + per-category + sources)."""
    total = sum(len(arts) for arts in groups.values())

    # Header message
    header = (
        f"# Daily RSS Digest — {date_str}\n\n"
        f"**{total} articles** across **{len(groups)} categories**"
    )
    send_hookshot(webhook_url, header, timeout)

    # One message per category summary
    for cat in ordered_categories:
        articles = groups[cat]
        summary = category_summaries.get(cat, "")
        msg = f"## {cat} ({len(articles)} articles)\n\n{summary}"
        send_hookshot(webhook_url, msg, timeout)

    # Sources message
    source_lines = ["### Sources\n"]
    for cat in ordered_categories:
        for a in groups[cat]:
            title = a.get("title", "(untitled)")
            url = a.get("url", "")
            source = a.get("source", "")
            if url:
                source_lines.append(f"- [{title}]({url}) — *{source}*")
            else:
                source_lines.append(f"- {title} — *{source}*")
    send_hookshot(webhook_url, "\n".join(source_lines), timeout)


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
    parser.add_argument("--dry-run", action="store_true", help="Print digest to stdout, skip file write and webhook")
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

    # --- Fetch articles ---
    print("[info] Fetching articles from FreshRSS...", file=sys.stderr)
    client = FreshRSSClient(cfg["freshrss"])
    articles = client.fetch_articles()
    print(f"[ok] Fetched {len(articles)} articles", file=sys.stderr)

    if not articles:
        print("[ok] No articles in lookback window, nothing to summarize", file=sys.stderr)
        return 0

    # --- Group by category ---
    groups = group_by_category(articles)
    print(f"[ok] {len(groups)} categories: {', '.join(sorted(groups.keys()))}", file=sys.stderr)

    # --- Filter categories if configured ---
    digest_cfg = cfg.get("digest") or {}
    include = digest_cfg.get("include_categories")
    exclude = digest_cfg.get("exclude_categories", [])

    # Filter out BI pipeline auto-tags (entity:*, event:*, signal:*, etc.)
    bi_prefixes = ("entity:", "event:", "signal:", "impact:", "horizon:", "sector:")
    groups = {k: v for k, v in groups.items() if not k.lower().startswith(bi_prefixes)}
    if include:
        include_lower = [c.lower() for c in include]
        groups = {k: v for k, v in groups.items() if k.lower() in include_lower}
    if exclude:
        exclude_lower = [c.lower() for c in exclude]
        groups = {k: v for k, v in groups.items() if k.lower() not in exclude_lower}

    if not groups:
        print("[ok] No categories matched after filtering", file=sys.stderr)
        return 0

    # --- Summarize ---
    summarizer = OllamaSummarizer(cfg["ai"])
    category_order = digest_cfg.get("category_order", [])
    ordered = order_categories(groups, category_order)
    summaries = summarize_all(groups, summarizer, ordered)
    digest = build_digest(groups, summaries, ordered, date_str)

    # --- Output ---
    if args.dry_run:
        print(digest)
        return 0

    output_dir = Path(args.output_dir or cfg.get("output", {}).get("directory", "/data/digests"))
    path = write_file(digest, output_dir, date_str)
    print(f"[ok] Digest written: {path}", file=sys.stderr)

    # Matrix via Hookshot
    webhook_url = cfg.get("matrix", {}).get("webhook_url", "")
    if webhook_url:
        try:
            timeout = int(cfg.get("matrix", {}).get("timeout_seconds", 30))
            post_to_matrix(webhook_url, groups, summaries, ordered, date_str, timeout)
            print(f"[ok] Posted to Matrix via Hookshot", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] Matrix post failed: {exc}", file=sys.stderr)

    # Retention
    keep_days = int(cfg.get("output", {}).get("keep_days", 30))
    prune_old_digests(output_dir, keep_days)

    print(f"[ok] Daily digest complete — {date_str}, {len(articles)} articles, {len(groups)} categories", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
