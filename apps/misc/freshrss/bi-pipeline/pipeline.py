#!/usr/bin/env python3
"""Customer intelligence pipeline for FreshRSS + OpenAI-compatible models."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import yaml

UTC = dt.timezone.utc
DEFAULT_EVENT_TYPES = [
    "pricing_change",
    "outage_incident",
    "security_incident",
    "layoffs_reorg",
    "funding_mna",
    "product_launch",
    "cloud_cost_signal",
    "regulatory_change",
    "partner_change",
    "customer_reference",
    "other",
]
IGNORE_QUERY_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "to",
    "of",
    "in",
    "on",
    "with",
    "from",
    "by",
    "at",
    "is",
    "are",
    "was",
    "were",
    "this",
    "that",
    "these",
    "those",
    "after",
    "before",
    "about",
    "into",
    "across",
}

POSITIVE_FEEDBACK_LABELS = {"relevant", "action_taken", "actioned", "useful", "true_positive"}
NEGATIVE_FEEDBACK_LABELS = {"not_relevant", "wrong_customer", "duplicate", "false_positive", "noise"}


class PipelineError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def to_epoch(ts: dt.datetime) -> int:
    return int(ts.timestamp())


def iso_from_epoch(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def clean_text(value: str, max_chars: int = 1400) -> str:
    no_script = re.sub(r"<script.*?</script>", " ", value or "", flags=re.IGNORECASE | re.DOTALL)
    no_style = re.sub(r"<style.*?</style>", " ", no_script, flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r"<[^>]+>", " ", no_style)
    text = html.unescape(no_tags)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except Exception:
        return url
    filtered = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in IGNORE_QUERY_PARAMS]
    clean_query = urlencode(filtered)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, clean_query, ""))


def extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except Exception:
        return ""
    return parsed.netloc.lower().removeprefix("www.")


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data = expand_env(data)
    if not isinstance(data, dict):
        raise PipelineError(f"Config root must be a mapping: {path}")
    return data


def expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
        return pattern.sub(lambda m: os.getenv(m.group(1), ""), value)
    return value


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            return default
    if not isinstance(data, dict):
        return default
    return data


def to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_iso_date(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = f"{text}T00:00:00+00:00"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def quantile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(float(v) for v in values)
    p = clamp(percentile, 0.0, 1.0)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = p * (len(sorted_values) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return float(sorted_values[lo] + ((sorted_values[hi] - sorted_values[lo]) * frac))


def normalize_tokens(value: str, max_tokens: int = 18) -> List[str]:
    parts = re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", (value or "").lower())
    out: List[str] = []
    for token in parts:
        if token in STOPWORDS:
            continue
        out.append(token)
        if len(out) >= max_tokens:
            break
    return out


def stable_story_signature(article: Dict[str, Any], event: Dict[str, Any]) -> str:
    title_tokens = normalize_tokens(str(article.get("title") or ""), max_tokens=12)
    summary_tokens = normalize_tokens(str(event.get("summary") or article.get("summary") or ""), max_tokens=10)
    event_type = str(event.get("event_type") or "other").strip().lower()
    core = " ".join([*title_tokens[:10], *summary_tokens[:6]]).strip()
    if not core:
        core = str(article.get("url") or article.get("id") or "story")
    signature = f"{event_type}|{core}"
    return signature


def story_id_from_signature(signature: str) -> str:
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()  # noqa: S324
    return digest[:16]


def parse_feedback_label(label: str) -> str:
    normalized = str(label or "").strip().lower().replace(" ", "_")
    if normalized in POSITIVE_FEEDBACK_LABELS:
        return "positive"
    if normalized in NEGATIVE_FEEDBACK_LABELS:
        return "negative"
    return "neutral"


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    tmp_path.replace(path)


def merge_seen_ids(existing_ids: List[str], new_ids: List[str], keep_seen: int) -> List[str]:
    keep = max(1, keep_seen)
    merged: List[str] = []
    seen: set[str] = set()
    for value in [*existing_ids, *new_ids]:
        item = str(value).strip()
        if not item or item in seen:
            continue
        merged.append(item)
        seen.add(item)
    if len(merged) > keep:
        return merged[-keep:]
    return merged


def parse_run_id_to_epoch(run_id: str) -> int:
    try:
        return int(dt.datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp())
    except ValueError:
        return 0


def prune_output_runs(output_dir: Path, retention_days: int, max_run_directories: int) -> Dict[str, int]:
    stats = {"removed_by_age": 0, "removed_by_count": 0}
    if not output_dir.exists():
        return stats

    now_epoch = to_epoch(utc_now())
    retention_seconds = max(0, retention_days) * 86400
    run_dirs = sorted((p for p in output_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
    removed: set[Path] = set()

    if retention_seconds > 0:
        cutoff = now_epoch - retention_seconds
        for run_dir in run_dirs:
            run_epoch = parse_run_id_to_epoch(run_dir.name)
            if run_epoch and run_epoch >= cutoff:
                continue
            if run_epoch == 0:
                mtime = int(run_dir.stat().st_mtime)
                if mtime >= cutoff:
                    continue
            try:
                shutil.rmtree(run_dir)
                removed.add(run_dir)
                stats["removed_by_age"] += 1
            except OSError as exc:
                print(f"[warn] Failed to prune output run dir={run_dir}: {exc}", file=sys.stderr)

    if max_run_directories > 0:
        remaining_dirs = sorted((p for p in output_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        if len(remaining_dirs) > max_run_directories:
            extra = len(remaining_dirs) - max_run_directories
            for run_dir in remaining_dirs[:extra]:
                if run_dir in removed:
                    continue
                try:
                    shutil.rmtree(run_dir)
                    removed.add(run_dir)
                    stats["removed_by_count"] += 1
                except OSError as exc:
                    print(f"[warn] Failed to prune output run dir={run_dir}: {exc}", file=sys.stderr)

    return stats


def extract_readable_text_from_html(raw_html: str, max_chars: int) -> str:
    if not raw_html:
        return ""
    sections: List[str] = []
    for pattern in [
        r"<article[^>]*>(.*?)</article>",
        r"<main[^>]*>(.*?)</main>",
        r"<body[^>]*>(.*?)</body>",
    ]:
        matches = re.findall(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL)
        for block in matches:
            cleaned = clean_text(block, max_chars=max_chars)
            if cleaned and len(cleaned) > 120:
                sections.append(cleaned)
    if not sections:
        fallback = clean_text(raw_html, max_chars=max_chars)
        return fallback
    sections.sort(key=len, reverse=True)
    return sections[0][:max_chars]


class ArticleEnricher:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", False))
        self.timeout = max(2, int(cfg.get("timeout_seconds") or 12))
        self.verify_tls = bool(cfg.get("verify_tls", True))
        self.max_chars = max(1200, int(cfg.get("max_chars") or 7000))
        self.min_summary_chars = max(50, int(cfg.get("min_summary_chars") or 260))
        self.user_agent = str(cfg.get("user_agent") or "FreshRSS-BI-Enricher/1.0")
        self.skip_domains = {str(x).strip().lower() for x in (cfg.get("skip_domains") or []) if str(x).strip()}
        self.session = requests.Session()
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.skipped = 0

    def get_stats(self) -> Dict[str, int]:
        return {
            "attempted": int(self.attempted),
            "succeeded": int(self.succeeded),
            "failed": int(self.failed),
            "skipped": int(self.skipped),
        }

    def enrich_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            self.skipped += 1
            return article
        url = str(article.get("url") or "").strip()
        if not url:
            self.skipped += 1
            return article
        source_domain = str(article.get("source_domain") or extract_domain(url) or "").lower()
        if source_domain in self.skip_domains:
            self.skipped += 1
            return article
        summary = str(article.get("summary") or "")
        if len(summary) >= self.min_summary_chars and len(str(article.get("text") or "")) >= self.min_summary_chars:
            self.skipped += 1
            return article

        self.attempted += 1
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
                allow_redirects=True,
            )
            if response.status_code >= 400:
                self.failed += 1
                return article
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type and "xml" not in content_type:
                self.failed += 1
                return article
            extracted = extract_readable_text_from_html(response.text, max_chars=self.max_chars)
            if len(extracted) < self.min_summary_chars:
                self.failed += 1
                return article
            updated = dict(article)
            updated["full_text"] = extracted
            updated["full_text_chars"] = len(extracted)
            updated["text"] = clean_text(
                f"{article.get('title', '')} {article.get('summary', '')} {extracted}",
                max_chars=9000,
            )
            self.succeeded += 1
            return updated
        except requests.RequestException:
            self.failed += 1
            return article


def load_feedback_updates(state: Dict[str, Any], feedback_cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    result = {
        "processed": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "outcomes": {},
    }
    if not bool(feedback_cfg.get("enabled", False)):
        return state, result

    feedback_state = dict(state.get("feedback") or {})
    stats = feedback_state.get("stats") or {}
    customer_stats = stats.get("by_customer") or {}
    source_stats = stats.get("by_source") or {}
    event_type_stats = stats.get("by_event_type") or {}
    customer_source_stats = stats.get("by_customer_source") or {}
    outcomes_stats = stats.get("outcomes") or {}
    by_customer_outcome_stats = stats.get("by_customer_outcome") or {}
    run_stats = stats.get("run") or {"positive": 0, "negative": 0, "neutral": 0}

    def consume_item(item: Dict[str, Any]) -> None:
        label_bucket = parse_feedback_label(str(item.get("label") or ""))
        customer = str(item.get("customer") or "unknown").strip() or "unknown"
        source = str(item.get("source_domain") or item.get("source") or "unknown").strip().lower() or "unknown"
        event_type = str(item.get("event_type") or "other").strip().lower() or "other"

        def bump(bucket: Dict[str, Dict[str, int]], key: str, label: str) -> None:
            target = bucket.get(key) or {"positive": 0, "negative": 0, "neutral": 0}
            target[label] = int(target.get(label) or 0) + 1
            bucket[key] = target

        bump(customer_stats, customer, label_bucket)
        bump(source_stats, source, label_bucket)
        bump(event_type_stats, event_type, label_bucket)
        bump(customer_source_stats, f"{customer}|{source}", label_bucket)

        run_stats[label_bucket] = int(run_stats.get(label_bucket) or 0) + 1
        result[label_bucket] += 1
        result["processed"] += 1

        outcome = str(item.get("outcome") or "").strip().lower().replace(" ", "_")
        if outcome:
            outcomes_stats[outcome] = int(outcomes_stats.get(outcome) or 0) + 1
            customer_outcome_key = f"{customer}|{outcome}"
            by_customer_outcome_stats[customer_outcome_key] = int(by_customer_outcome_stats.get(customer_outcome_key) or 0) + 1

    api_cfg = feedback_cfg.get("api") if isinstance(feedback_cfg.get("api"), dict) else {}
    api_cursor = max(0, int(feedback_state.get("api_cursor") or 0))
    if bool(api_cfg.get("enabled", False)):
        api_url = str(api_cfg.get("url") or "").strip()
        if api_url:
            timeout_seconds = max(2, to_int(api_cfg.get("timeout_seconds"), 8))
            limit = max(1, min(2000, to_int(api_cfg.get("limit"), 500)))
            token = str(api_cfg.get("token") or "").strip()
            headers: Dict[str, str] = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                response = requests.get(
                    api_url,
                    params={"cursor": api_cursor, "limit": limit},
                    headers=headers,
                    timeout=timeout_seconds,
                )
                if response.status_code < 400:
                    payload = response.json() if response.text.strip() else {}
                    labels = payload.get("labels") if isinstance(payload, dict) else []
                    if isinstance(labels, list):
                        for raw_item in labels:
                            if isinstance(raw_item, dict):
                                consume_item(raw_item)
                    api_cursor = max(api_cursor, to_int((payload or {}).get("next_cursor"), api_cursor))
                else:
                    print(f"[warn] feedback api fetch failed ({response.status_code})", file=sys.stderr)
            except Exception as exc:
                print(f"[warn] feedback api request failed: {exc}", file=sys.stderr)

    feedback_file = Path(str(feedback_cfg.get("file") or "./feedback/labels.jsonl"))
    file_cursor = max(0, int(feedback_state.get("cursor") or 0))
    if feedback_file.exists():
        lines = feedback_file.read_text(encoding="utf-8").splitlines()
        if file_cursor < len(lines):
            for raw_line in lines[file_cursor:]:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                consume_item(item)
            file_cursor = len(lines)

    feedback_state["cursor"] = file_cursor
    feedback_state["api_cursor"] = api_cursor
    feedback_state["stats"] = {
        "by_customer": customer_stats,
        "by_source": source_stats,
        "by_event_type": event_type_stats,
        "by_customer_source": customer_source_stats,
        "outcomes": outcomes_stats,
        "by_customer_outcome": by_customer_outcome_stats,
        "run": run_stats,
    }
    result["outcomes"] = outcomes_stats

    updated_state = dict(state)
    updated_state["feedback"] = feedback_state
    return updated_state, result


def compute_quality_multiplier(
    counts: Dict[str, Any],
    min_samples: int,
    max_adjustment: float,
) -> float:
    positive = int(counts.get("positive") or 0)
    negative = int(counts.get("negative") or 0)
    total = positive + negative
    if total < max(1, min_samples):
        return 1.0
    precision = positive / max(1, total)
    centered = precision - 0.5
    return clamp(1.0 + (centered * max_adjustment * 2.0), 0.6, 1.4)


def feedback_quality_adjustment(
    customer_name: str,
    source_domain: str,
    event_type: str,
    feedback_state: Dict[str, Any],
    feedback_cfg: Dict[str, Any],
) -> Tuple[float, Dict[str, float]]:
    if not bool(feedback_cfg.get("enabled", False)):
        return 1.0, {"customer_source": 1.0, "source": 1.0, "event_type": 1.0}

    stats = ((feedback_state.get("feedback") or {}).get("stats") or {})
    customer_source_stats = stats.get("by_customer_source") or {}
    source_stats = stats.get("by_source") or {}
    event_type_stats = stats.get("by_event_type") or {}

    min_samples = max(1, int(feedback_cfg.get("min_samples_for_adjustment") or 5))
    max_adjustment = clamp(float(feedback_cfg.get("max_adjustment") or 0.25), 0.05, 0.45)

    m_customer_source = compute_quality_multiplier(
        customer_source_stats.get(f"{customer_name}|{source_domain}") or {},
        min_samples=min_samples,
        max_adjustment=max_adjustment,
    )
    m_source = compute_quality_multiplier(
        source_stats.get(source_domain) or {},
        min_samples=min_samples,
        max_adjustment=max_adjustment,
    )
    m_event_type = compute_quality_multiplier(
        event_type_stats.get(event_type) or {},
        min_samples=min_samples,
        max_adjustment=max_adjustment,
    )
    combined = clamp((m_customer_source * 0.45) + (m_source * 0.35) + (m_event_type * 0.20), 0.6, 1.4)
    return combined, {
        "customer_source": float(m_customer_source),
        "source": float(m_source),
        "event_type": float(m_event_type),
    }


def dynamic_threshold_for_customer(
    customer_name: str,
    base_threshold: float,
    state: Dict[str, Any],
    cfg: Dict[str, Any],
) -> float:
    if not bool(cfg.get("enabled", False)):
        return base_threshold
    history_state = (state.get("dynamic_thresholds") or {}).get("history") or {}
    history = history_state.get(customer_name) or []
    history_floats = [to_float(v, 0.0) for v in history if isinstance(v, (int, float, str))]
    min_history = max(5, int(cfg.get("min_history") or 30))
    if len(history_floats) < min_history:
        return base_threshold
    percentile = clamp(float(cfg.get("percentile") or 0.82), 0.5, 0.98)
    blend = clamp(float(cfg.get("blend_weight") or 0.65), 0.0, 1.0)
    q = quantile(history_floats, percentile)
    dynamic_target = clamp(q, float(cfg.get("clamp_min") or 25.0), float(cfg.get("clamp_max") or 90.0))
    return clamp((base_threshold * (1.0 - blend)) + (dynamic_target * blend), 0.0, 100.0)


def cleanup_alert_history(alert_history: Dict[str, Any], now_epoch: int, max_age_seconds: int) -> Dict[str, int]:
    cleaned: Dict[str, int] = {}
    for key, value in (alert_history or {}).items():
        ts = to_int(value, 0)
        if ts <= 0:
            continue
        if now_epoch - ts > max_age_seconds:
            continue
        cleaned[str(key)] = ts
    return cleaned


def trim_story_state(story_state: Dict[str, Any], max_items: int) -> Dict[str, Any]:
    if max_items <= 0:
        return {}
    sortable: List[Tuple[str, int]] = []
    for signature, raw in (story_state or {}).items():
        if isinstance(raw, dict):
            last_seen = to_int(raw.get("last_seen"), 0)
        else:
            last_seen = 0
        sortable.append((str(signature), last_seen))
    sortable.sort(key=lambda x: x[1], reverse=True)
    kept = sortable[:max_items]
    return {sig: story_state[sig] for sig, _ in kept if sig in story_state}


def summarize_feedback_breakdown(stats: Dict[str, Any], limit_per_dimension: int = 20) -> Dict[str, Dict[str, Dict[str, int]]]:
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for dimension, raw_map in [
        ("customer", (stats.get("by_customer") or {})),
        ("source", (stats.get("by_source") or {})),
        ("event_type", (stats.get("by_event_type") or {})),
    ]:
        if not isinstance(raw_map, dict):
            continue
        sortable: List[Tuple[str, int]] = []
        for key, counts in raw_map.items():
            if not isinstance(counts, dict):
                continue
            total = int(counts.get("positive") or 0) + int(counts.get("negative") or 0) + int(counts.get("neutral") or 0)
            sortable.append((str(key), total))
        sortable.sort(key=lambda x: x[1], reverse=True)
        trimmed: Dict[str, Dict[str, int]] = {}
        for key, _ in sortable[:limit_per_dimension]:
            counts = raw_map.get(key) or {}
            trimmed[key] = {
                "positive": int(counts.get("positive") or 0),
                "negative": int(counts.get("negative") or 0),
                "neutral": int(counts.get("neutral") or 0),
            }
        out[dimension] = trimmed
    return out


def parse_iso_to_epoch(value: str) -> int:
    if not value:
        return to_epoch(utc_now())
    text = str(value).strip()
    if not text:
        return to_epoch(utc_now())
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return to_epoch(utc_now())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def normalize_list_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def classify_theme(event_type: str) -> str:
    mapping = {
        "security_incident": "security",
        "outage_incident": "reliability",
        "cloud_cost_signal": "cost",
        "pricing_change": "cost",
        "layoffs_reorg": "org_change",
        "funding_mna": "market",
        "regulatory_change": "compliance",
        "partner_change": "ecosystem",
        "product_launch": "product",
        "customer_reference": "adoption",
    }
    return mapping.get(str(event_type or "").strip().lower(), "other")


def infer_customer_tier(customer: Dict[str, Any], customer_tiers_cfg: Dict[str, Any]) -> Dict[str, Any]:
    tiers = customer_tiers_cfg.get("tiers") if isinstance(customer_tiers_cfg.get("tiers"), list) else []
    business = customer.get("business_context") if isinstance(customer.get("business_context"), dict) else {}
    arr = to_float(business.get("arr"), to_float(customer.get("contract_value"), 0.0))
    chosen = {
        "name": str(customer_tiers_cfg.get("default_tier") or "standard"),
        "score_multiplier": 1.0,
        "threshold_adjustment": 0.0,
    }
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        min_arr = to_float(tier.get("min_arr"), -1.0)
        max_arr = to_float(tier.get("max_arr"), float("inf"))
        if arr < min_arr or arr > max_arr:
            continue
        chosen = {
            "name": str(tier.get("name") or chosen["name"]),
            "score_multiplier": clamp(to_float(tier.get("score_multiplier"), 1.0), 0.6, 1.5),
            "threshold_adjustment": clamp(to_float(tier.get("threshold_adjustment"), 0.0), -20.0, 20.0),
        }
        break
    chosen["arr"] = float(arr)
    return chosen


def resolve_playbooks(customer: Dict[str, Any], event_type: str, playbooks_cfg: Dict[str, Any]) -> List[str]:
    by_segment = playbooks_cfg.get("by_segment") if isinstance(playbooks_cfg.get("by_segment"), dict) else {}
    default = playbooks_cfg.get("default") if isinstance(playbooks_cfg.get("default"), dict) else {}
    business = customer.get("business_context") if isinstance(customer.get("business_context"), dict) else {}
    segment = str(business.get("segment") or "").strip().lower()
    event_key = str(event_type or "other").strip().lower()
    actions: List[str] = []
    if segment and isinstance(by_segment.get(segment), dict):
        actions.extend(normalize_list_strings((by_segment.get(segment) or {}).get(event_key)))
    actions.extend(normalize_list_strings(default.get(event_key)))
    deduped: List[str] = []
    seen: set[str] = set()
    for action in actions:
        key = action.lower()
        if key in seen:
            continue
        deduped.append(action)
        seen.add(key)
        if len(deduped) >= 4:
            break
    return deduped


def summarize_customer_outcomes(feedback_stats: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    by_customer_outcome = feedback_stats.get("by_customer_outcome") if isinstance(feedback_stats.get("by_customer_outcome"), dict) else {}
    out: Dict[str, Dict[str, int]] = {}
    for key, count in by_customer_outcome.items():
        if not isinstance(key, str) or "|" not in key:
            continue
        customer, outcome = key.split("|", 1)
        customer_key = customer.strip() or "unknown"
        outcome_key = outcome.strip() or "unknown"
        bucket = out.get(customer_key) or {}
        bucket[outcome_key] = int(bucket.get(outcome_key) or 0) + int(count or 0)
        out[customer_key] = bucket
    return out


def calc_account_heat_score(
    customer: Dict[str, Any],
    alerts: List[Dict[str, Any]],
    watchlist: List[Dict[str, Any]],
    competitor_pressure: float,
) -> Dict[str, Any]:
    business = customer.get("business_context") if isinstance(customer.get("business_context"), dict) else {}
    avg_alert_score = (sum(float(a.get("score") or 0.0) for a in alerts) / len(alerts)) if alerts else 0.0
    watchlist_factor = min(20.0, float(len(watchlist)) * 2.5)
    renewal_pressure = 0.0
    renewal_date = parse_iso_date(customer.get("renewal_date") or business.get("renewal_date"))
    if renewal_date is not None:
        days_until = int((renewal_date - utc_now()).total_seconds() / 86400)
        if days_until <= 0:
            renewal_pressure = 20.0
        elif days_until <= 30:
            renewal_pressure = 16.0
        elif days_until <= 90:
            renewal_pressure = 10.0
    open_risks = normalize_list_strings(business.get("open_risks") or customer.get("known_risks"))
    open_risk_pressure = min(12.0, float(len(open_risks)) * 2.0)
    competitor_pressure_component = clamp(float(competitor_pressure), 0.0, 20.0)
    score = clamp((avg_alert_score * 0.55) + watchlist_factor + renewal_pressure + open_risk_pressure + competitor_pressure_component, 0.0, 100.0)
    band = "low"
    if score >= 70:
        band = "high"
    elif score >= 40:
        band = "medium"
    return {
        "score": round(score, 2),
        "band": band,
        "avg_alert_score": round(avg_alert_score, 2),
        "watchlist_factor": round(watchlist_factor, 2),
        "renewal_pressure": round(renewal_pressure, 2),
        "open_risk_pressure": round(open_risk_pressure, 2),
        "competitor_pressure": round(competitor_pressure_component, 2),
    }


def positive_event_types() -> set[str]:
    return {"product_launch", "partner_change", "customer_reference", "funding_mna"}


def calc_opportunity_score(event: Dict[str, Any], score: float, details: Dict[str, Any]) -> float:
    event_type = str(event.get("event_type") or "other").strip().lower()
    if event_type not in positive_event_types():
        return 0.0
    confidence = clamp(to_float(event.get("confidence"), 0.0), 0.0, 1.0)
    urgency = clamp(float(to_int(event.get("urgency"), 1)), 1.0, 5.0)
    strategic_hits = to_float(details.get("strategic_priority_hits"), 0.0)
    stack_hits = to_float(details.get("stack_confirmed_hits"), 0.0)
    normalized_score = clamp(float(score) / 100.0, 0.0, 1.0)
    opportunity = (
        (normalized_score * 45.0)
        + (confidence * 25.0)
        + ((urgency / 5.0) * 10.0)
        + (min(2.0, strategic_hits) * 8.0)
        + (min(2.0, stack_hits) * 6.0)
    )
    return round(clamp(opportunity, 0.0, 100.0), 2)


def source_quality_multiplier(
    source_domain: str,
    source_reliability_state: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Tuple[float, Dict[str, float]]:
    domain = str(source_domain or "unknown").strip().lower() or "unknown"
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return 1.0, {"source_quality_multiplier": 1.0, "source_quality_score": 0.5}
    entries = source_reliability_state.get("sources") if isinstance(source_reliability_state.get("sources"), dict) else {}
    source_entry = entries.get(domain) if isinstance(entries.get(domain), dict) else {}
    alerts = float(to_int(source_entry.get("alerts"), 0))
    watchlist = float(to_int(source_entry.get("watchlist"), 0))
    needs_data = float(to_int(source_entry.get("needs_data"), 0))
    weighted_total = alerts + watchlist + needs_data
    if weighted_total <= 0:
        return 1.0, {"source_quality_multiplier": 1.0, "source_quality_score": 0.5}

    quality = (alerts + (0.4 * watchlist)) / max(1.0, weighted_total)
    max_adjustment = clamp(to_float(cfg.get("max_adjustment"), 0.15), 0.05, 0.3)
    min_samples = max(3, to_int(cfg.get("min_samples"), 8))
    if weighted_total < min_samples:
        return 1.0, {"source_quality_multiplier": 1.0, "source_quality_score": round(quality, 3)}
    centered = quality - 0.5
    multiplier = clamp(1.0 + (centered * 2.0 * max_adjustment), 0.8, 1.2)
    return round(multiplier, 4), {
        "source_quality_multiplier": round(multiplier, 4),
        "source_quality_score": round(quality, 3),
    }


def update_source_reliability_state(
    source_reliability_state: Dict[str, Any],
    customer_alerts: Dict[str, List[Dict[str, Any]]],
    customer_watchlist: Dict[str, List[Dict[str, Any]]],
    customer_needs_data: Dict[str, List[Dict[str, Any]]],
    max_sources: int,
) -> Dict[str, Any]:
    existing_sources = source_reliability_state.get("sources") if isinstance(source_reliability_state.get("sources"), dict) else {}
    updated_sources: Dict[str, Dict[str, int]] = {}
    for source, counts in existing_sources.items():
        if not isinstance(counts, dict):
            continue
        updated_sources[str(source)] = {
            "alerts": max(0, to_int(counts.get("alerts"), 0)),
            "watchlist": max(0, to_int(counts.get("watchlist"), 0)),
            "needs_data": max(0, to_int(counts.get("needs_data"), 0)),
        }

    def bump(items: List[Dict[str, Any]], field: str) -> None:
        for item in items:
            source = str(item.get("source_domain") or "unknown").strip().lower() or "unknown"
            entry = updated_sources.get(source) or {"alerts": 0, "watchlist": 0, "needs_data": 0}
            entry[field] = int(entry.get(field) or 0) + 1
            updated_sources[source] = entry

    for alerts in customer_alerts.values():
        bump(alerts or [], "alerts")
    for watch in customer_watchlist.values():
        bump(watch or [], "watchlist")
    for needs in customer_needs_data.values():
        bump(needs or [], "needs_data")

    sorted_sources = sorted(
        updated_sources.items(),
        key=lambda item: int((item[1] or {}).get("alerts") or 0) + int((item[1] or {}).get("watchlist") or 0),
        reverse=True,
    )
    trimmed = dict(sorted_sources[:max(50, max_sources)])
    return {"sources": trimmed, "updated_at": utc_now().isoformat()}


def build_customer_story_snapshot(
    customer_alerts: Dict[str, List[Dict[str, Any]]],
    customer_watchlist: Dict[str, List[Dict[str, Any]]],
    customer_needs_data: Dict[str, List[Dict[str, Any]]],
    max_items_per_customer: int,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    snapshot: Dict[str, Dict[str, Dict[str, Any]]] = {}
    all_customers = set(customer_alerts.keys()) | set(customer_watchlist.keys()) | set(customer_needs_data.keys())
    for customer in all_customers:
        merged = [
            *((customer_alerts.get(customer) or [])),
            *((customer_watchlist.get(customer) or [])),
            *((customer_needs_data.get(customer) or [])),
        ]
        merged = sorted(merged, key=lambda x: float(x.get("score") or 0.0), reverse=True)[:max_items_per_customer]
        customer_snapshot: Dict[str, Dict[str, Any]] = {}
        for item in merged:
            story_id = str(item.get("story_id") or "").strip()
            if not story_id:
                continue
            customer_snapshot[story_id] = {
                "score": round(float(item.get("score") or 0.0), 2),
                "event_type": str(item.get("event_type") or "other"),
                "title": str(item.get("article_title") or ""),
                "routing": str(item.get("routing_reason") or "alert"),
            }
        snapshot[customer] = customer_snapshot
    return snapshot


def compute_story_changes(
    customer_names: List[str],
    current_snapshot: Dict[str, Dict[str, Dict[str, Any]]],
    previous_snapshot: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for customer in customer_names:
        current_map = current_snapshot.get(customer) if isinstance(current_snapshot.get(customer), dict) else {}
        previous_map = previous_snapshot.get(customer) if isinstance(previous_snapshot.get(customer), dict) else {}
        added = []
        removed = []
        changed = []
        for story_id, current_item in current_map.items():
            prev_item = previous_map.get(story_id) if isinstance(previous_map.get(story_id), dict) else {}
            if not prev_item:
                added.append({"story_id": story_id, "title": current_item.get("title"), "score": current_item.get("score")})
                continue
            score_delta = round(float(current_item.get("score") or 0.0) - float(prev_item.get("score") or 0.0), 2)
            routing_changed = str(current_item.get("routing") or "") != str(prev_item.get("routing") or "")
            if abs(score_delta) >= 5.0 or routing_changed:
                changed.append(
                    {
                        "story_id": story_id,
                        "title": current_item.get("title"),
                        "score_delta": score_delta,
                        "routing_from": prev_item.get("routing"),
                        "routing_to": current_item.get("routing"),
                    }
                )
        for story_id, prev_item in previous_map.items():
            if story_id in current_map:
                continue
            removed.append({"story_id": story_id, "title": prev_item.get("title"), "score": prev_item.get("score")})
        out[customer] = {
            "added": added[:10],
            "changed": changed[:10],
            "removed": removed[:10],
            "added_count": len(added),
            "changed_count": len(changed),
            "removed_count": len(removed),
        }
    return out


def forecast_customer_heat(
    customer: Dict[str, Any],
    current_heat: Dict[str, Any],
    theme_history_entries: List[Dict[str, Any]],
    opportunity_index: float,
) -> Dict[str, float]:
    now_score = clamp(float(current_heat.get("score") or 0.0), 0.0, 100.0)
    recent = theme_history_entries[-4:] if theme_history_entries else []
    recent_totals: List[float] = []
    for entry in recent:
        if not isinstance(entry, dict):
            continue
        themes = entry.get("themes") if isinstance(entry.get("themes"), dict) else {}
        recent_totals.append(float(sum(to_int(v, 0) for v in themes.values())))
    trend = 0.0
    if len(recent_totals) >= 2:
        trend = (recent_totals[-1] - recent_totals[0]) / max(1.0, len(recent_totals) - 1.0)
    renewal_date = parse_iso_date(customer.get("renewal_date"))
    renewal_boost_30 = 0.0
    renewal_boost_90 = 0.0
    if renewal_date is not None:
        days_until = int((renewal_date - utc_now()).total_seconds() / 86400)
        if days_until <= 30:
            renewal_boost_30 = 7.0
            renewal_boost_90 = 9.0
        elif days_until <= 90:
            renewal_boost_30 = 4.0
            renewal_boost_90 = 6.0
    opportunity_relief = clamp(float(opportunity_index) / 100.0, 0.0, 1.0) * 4.0
    forecast_30d = clamp(now_score + (trend * 2.0) + renewal_boost_30 - opportunity_relief, 0.0, 100.0)
    forecast_90d = clamp(now_score + (trend * 4.0) + renewal_boost_90 - (opportunity_relief * 1.2), 0.0, 100.0)
    return {"forecast_30d": round(forecast_30d, 2), "forecast_90d": round(forecast_90d, 2)}


def process_article_for_event(
    article: Dict[str, Any],
    enrichment_cfg: Dict[str, Any],
    ai_cfg: Dict[str, Any],
    event_types: List[str],
) -> Dict[str, Any]:
    enricher = ArticleEnricher(enrichment_cfg)
    extractor = AIExtractor(ai_cfg, event_types)
    enriched_article = enricher.enrich_article(article)
    event = extractor.extract(enriched_article)
    return {
        "article": enriched_article,
        "event": event,
        "ai_stats": extractor.get_stats(),
        "enrichment_stats": enricher.get_stats(),
    }


def evaluate_cyera_customer_impact(
    item: Dict[str, Any],
    customer: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        base_score = clamp(to_float(item.get("cyera_relationship_risk_score"), 0.0), 0.0, 1.0)
        return {
            "risk_score": round(base_score, 3),
            "risk_label": bounded_risk_label(item.get("cyera_relationship_risk_label"), base_score),
            "negative_impact": False,
            "summary": str(item.get("cyera_impact_summary") or ""),
        }

    base_score = clamp(to_float(item.get("cyera_relationship_risk_score"), 0.35), 0.0, 1.0)
    event_type = str(item.get("event_type") or "other").strip().lower()
    signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
    business = customer.get("business_context") if isinstance(customer.get("business_context"), dict) else {}
    text_blob = " ".join(
        [
            str(item.get("article_title") or ""),
            str(item.get("summary") or ""),
            str(item.get("why_it_matters") or ""),
            str(item.get("cyera_impact_summary") or ""),
        ]
    ).lower()

    adjustment = 0.0
    if event_type in {"security_incident", "outage_incident", "regulatory_change", "pricing_change"}:
        adjustment += 0.08
    adjustment += float(signals.get("health_risk") or 0.0) * 0.12
    adjustment += float(signals.get("churn_risk") or 0.0) * 0.10
    adjustment += float(signals.get("renewal_risk") or 0.0) * 0.10

    ai_dspm_terms = normalize_list_strings(cfg.get("ai_dspm_terms")) or [
        "cyera",
        "ai security",
        "dspm",
        "data security posture",
        "governance",
        "compliance",
    ]
    term_hits = sum(1 for term in ai_dspm_terms if phrase_in_text(text_blob, term))
    if term_hits > 0:
        adjustment += min(0.12, term_hits * 0.03)

    active_use_cases = normalize_list_strings(business.get("active_use_cases"))
    if any("data" in use_case.lower() or "security" in use_case.lower() for use_case in active_use_cases):
        adjustment += 0.04

    score = clamp(base_score + adjustment, 0.0, 1.0)
    label = bounded_risk_label(item.get("cyera_relationship_risk_label"), score)
    negative_floor = clamp(to_float(cfg.get("negative_impact_floor"), 0.65), 0.3, 0.95)
    summary = str(item.get("cyera_impact_summary") or "").strip()
    if not summary:
        summary = (
            "Article may affect customer confidence in Cyera AI/DSPM positioning; validate urgency and mitigation posture."
        )
    return {
        "risk_score": round(score, 3),
        "risk_label": label,
        "negative_impact": bool(score >= negative_floor),
        "summary": summary,
    }


def memory_signal_adjustment(
    memory_entry: Dict[str, Any],
    article: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    text_blob = " ".join(
        [
            str(article.get("title") or ""),
            str(article.get("summary") or ""),
            str(article.get("full_text") or ""),
            str(event.get("summary") or ""),
            str(event.get("why_it_matters") or ""),
        ]
    ).lower()
    focus_topics = normalize_list_strings(memory_entry.get("focus_topics"))
    unresolved_actions = normalize_list_strings(memory_entry.get("unresolved_actions"))
    recent_event_types = {str(x).strip().lower() for x in normalize_list_strings(memory_entry.get("recent_event_types"))}
    event_type = str(event.get("event_type") or "other").strip().lower()

    topic_hits = 0
    for topic in focus_topics:
        if phrase_in_text(text_blob, topic):
            topic_hits += 1
    unresolved_hits = 0
    for action in unresolved_actions:
        if phrase_in_text(text_blob, action):
            unresolved_hits += 1

    multiplier = 1.0
    if event_type in recent_event_types:
        multiplier += 0.06
    if topic_hits > 0:
        multiplier += min(0.08, topic_hits * 0.03)
    if unresolved_hits > 0:
        multiplier += min(0.08, unresolved_hits * 0.04)

    return {
        "multiplier": float(clamp(multiplier, 0.85, 1.2)),
        "topic_hits": int(topic_hits),
        "unresolved_hits": int(unresolved_hits),
    }


def detect_needs_data_reason(
    item: Dict[str, Any],
    cfg: Dict[str, Any],
) -> str:
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return ""
    score_floor = clamp(float(cfg.get("score_floor") or 25.0), 0.0, 100.0)
    confidence_max = clamp(float(cfg.get("confidence_max") or 0.45), 0.0, 1.0)
    min_summary_chars = max(40, int(cfg.get("min_summary_chars") or 110))
    max_evidence_snippets = max(0, int(cfg.get("max_evidence_snippets") or 1))
    require_missing_link = bool(cfg.get("require_missing_link", False))

    if float(item.get("score") or 0.0) < score_floor:
        return ""
    confidence = clamp(float(item.get("confidence") or 0.0), 0.0, 1.0)
    summary = str(item.get("summary") or "")
    evidence = item.get("evidence_snippets") if isinstance(item.get("evidence_snippets"), list) else []
    no_link = not str(item.get("url") or "").strip()

    reasons: List[str] = []
    if confidence <= confidence_max:
        reasons.append("low_confidence")
    if len(summary.strip()) < min_summary_chars:
        reasons.append("limited_summary")
    if len(evidence) <= max_evidence_snippets:
        reasons.append("limited_evidence")
    if no_link:
        reasons.append("missing_link")

    if require_missing_link and "missing_link" not in reasons:
        return ""
    if len(reasons) >= 2:
        return ",".join(reasons[:3])
    return ""


def compute_customer_deltas(
    customer_names: List[str],
    current_metrics: Dict[str, Dict[str, float]],
    previous_snapshot: Dict[str, Any],
) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for customer in customer_names:
        current = current_metrics.get(customer) or {}
        previous = previous_snapshot.get(customer) if isinstance(previous_snapshot.get(customer), dict) else {}
        entry = {
            "alerts_delta": float(current.get("alerts", 0.0) - float(previous.get("alerts") or 0.0)),
            "watchlist_delta": float(current.get("watchlist", 0.0) - float(previous.get("watchlist") or 0.0)),
            "needs_data_delta": float(current.get("needs_data", 0.0) - float(previous.get("needs_data") or 0.0)),
            "heat_delta": round(float(current.get("heat", 0.0) - float(previous.get("heat") or 0.0)), 2),
            "opportunity_delta": round(
                float(current.get("opportunity", 0.0) - float(previous.get("opportunity") or 0.0)),
                2,
            ),
            "positive_signals_delta": float(
                current.get("positive_signals", 0.0) - float(previous.get("positive_signals") or 0.0)
            ),
        }
        out[customer] = entry
    return out


def influx_escape_tag(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def influx_escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def influx_field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return f"{value:.6f}"
    if value is None:
        return '""'
    return f'"{influx_escape_string(str(value))}"'


def influx_line(measurement: str, tags: Dict[str, str], fields: Dict[str, Any], timestamp_epoch: int) -> str:
    tag_str = ",".join(f"{influx_escape_tag(k)}={influx_escape_tag(v)}" for k, v in sorted(tags.items()) if v)
    field_parts = []
    for key, value in sorted(fields.items()):
        if value is None:
            continue
        field_parts.append(f"{influx_escape_tag(key)}={influx_field_value(value)}")
    if not field_parts:
        field_parts.append('value=""')
    fields_str = ",".join(field_parts)
    if tag_str:
        return f"{influx_escape_tag(measurement)},{tag_str} {fields_str} {int(timestamp_epoch)}"
    return f"{influx_escape_tag(measurement)} {fields_str} {int(timestamp_epoch)}"


def parse_client_login_token(raw: str) -> str:
    for line in raw.splitlines():
        if line.startswith("Auth="):
            return line.split("=", 1)[1].strip()
    raise PipelineError("FreshRSS greader auth token not found in ClientLogin response")


def extract_best_link(item: Dict[str, Any]) -> str:
    for group in ("canonical", "alternate"):
        entries = item.get(group) or []
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("href"):
                    return str(entry["href"])
    return ""


def normalize_article(item: Dict[str, Any]) -> Dict[str, Any]:
    summary_html = ""
    summary = item.get("summary")
    if isinstance(summary, dict):
        summary_html = str(summary.get("content") or "")
    content = item.get("content")
    if not summary_html and isinstance(content, dict):
        summary_html = str(content.get("content") or "")

    published = int(item.get("published") or 0)
    origin = item.get("origin") or {}
    stream_id = str(origin.get("streamId") or "")
    feed_url = stream_id[5:] if stream_id.startswith("feed/") else ""
    article = {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or "").strip(),
        "url": canonicalize_url(extract_best_link(item)),
        "feed_url": feed_url,
        "source": str(origin.get("title") or "unknown"),
        "published_epoch": published,
        "published_at": iso_from_epoch(published) if published else "",
        "author": str(item.get("author") or "").strip(),
        "summary": clean_text(summary_html),
        "categories": item.get("categories") or [],
    }
    article["source_domain"] = extract_domain(article["url"]) or "unknown"
    article["text"] = clean_text(f"{article['title']} {article['summary']}", max_chars=2600)
    return article


def article_fingerprint(article: Dict[str, Any]) -> str:
    if article.get("url"):
        parsed = urlsplit(article["url"])
        return f"{parsed.netloc}{parsed.path}".lower()
    core = re.sub(r"[^a-z0-9]+", " ", article.get("title", "").lower())
    return " ".join(core.split()[:14])


class FreshRSSClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.api_url = str(cfg.get("greader_api_url") or "").rstrip("/")
        self.username = str(cfg.get("username") or "")
        self.api_password = str(cfg.get("api_password") or "")
        self.timeout = int(cfg.get("timeout_seconds") or 20)
        self.verify_tls = bool(cfg.get("verify_tls", True))
        self.max_items = int(cfg.get("max_items") or 250)
        self.unread_only = bool(cfg.get("unread_only", True))
        self.lookback_hours = int(cfg.get("lookback_hours") or 72)
        self.max_article_age_hours = max(0, int(cfg.get("max_article_age_hours") or self.lookback_hours or 0))
        self.include_undated_articles = bool(cfg.get("include_undated_articles", False))
        self.page_size = max(1, int(cfg.get("page_size") or 100))
        self.retry_attempts = max(1, int(cfg.get("retry_attempts") or 3))
        self.retry_backoff_seconds = float(cfg.get("retry_backoff_seconds") or 2.0)
        self.include_groups = normalize_list_strings(cfg.get("include_groups"))
        self.session = requests.Session()

        if not self.api_url:
            raise PipelineError("fresh_rss.greader_api_url is required")
        if not self.username or not self.api_password:
            raise PipelineError("fresh_rss.username and fresh_rss.api_password are required")

    def _article_in_included_groups(self, article: Dict[str, Any]) -> bool:
        if not self.include_groups:
            return True
        categories = [str(x).strip().lower() for x in (article.get("categories") or []) if str(x).strip()]
        if not categories:
            return False
        normalized_targets = [x.lower() for x in self.include_groups]
        for category in categories:
            for target in normalized_targets:
                # FreshRSS/greader labels often look like:
                # user/-/label/NYM Customers
                if category == target:
                    return True
                if category.endswith(f"/label/{target}"):
                    return True
                if category.endswith(f"/{target}"):
                    return True
        return False

    def login(self) -> str:
        params = {"Email": self.username, "Passwd": self.api_password}
        url = f"{self.api_url}/accounts/ClientLogin"
        # Prefer POST so credentials are not written into upstream access logs.
        response = self.session.post(url, data=params, timeout=self.timeout, verify=self.verify_tls)
        if response.status_code in {404, 405}:
            response = self.session.get(url, params=params, timeout=self.timeout, verify=self.verify_tls)
        if response.status_code >= 400:
            hint = ""
            if response.status_code == 503 and "Service Unavailable" in response.text:
                hint = (
                    " Hint: FreshRSS greader API often returns 503 when the user's API password "
                    "is not configured. Set it in the FreshRSS user profile."
                )
            raise PipelineError(f"FreshRSS ClientLogin failed ({response.status_code}): {response.text[:300]}{hint}")
        return parse_client_login_token(response.text)

    def _fetch_articles_once(
        self,
        max_items_override: int | None = None,
        since_epoch_override: int | None = None,
    ) -> List[Dict[str, Any]]:
        auth = self.login()
        target_total = max(1, int(max_items_override if max_items_override is not None else self.max_items))
        stream = "user/-/state/com.google/reading-list"
        url = f"{self.api_url}/reader/api/0/stream/contents/{stream}"
        headers = {"Authorization": f"GoogleLogin auth={auth}"}
        continuation = ""
        normalized: List[Dict[str, Any]] = []

        while len(normalized) < target_total:
            page_limit = min(self.page_size, target_total - len(normalized))
            params: Dict[str, Any] = {
                "n": page_limit,
                "ck": str(int(time.time())),
                "output": "json",
            }
            if continuation:
                params["c"] = continuation
            if self.unread_only:
                params["xt"] = "user/-/state/com.google/read"
            lookback_epoch = 0
            if self.lookback_hours > 0:
                lookback_epoch = to_epoch(utc_now() - dt.timedelta(hours=self.lookback_hours))
            incremental_epoch = max(0, to_int(since_epoch_override, 0))
            effective_ot = 0
            if lookback_epoch and incremental_epoch:
                effective_ot = max(lookback_epoch, incremental_epoch)
            elif lookback_epoch:
                effective_ot = lookback_epoch
            elif incremental_epoch:
                effective_ot = incremental_epoch
            if effective_ot > 0:
                params["ot"] = effective_ot

            response = self.session.get(url, params=params, headers=headers, timeout=self.timeout, verify=self.verify_tls)
            if response.status_code >= 400:
                raise PipelineError(f"FreshRSS stream fetch failed ({response.status_code}): {response.text[:300]}")

            try:
                payload = response.json()
            except ValueError as exc:
                raise PipelineError(f"FreshRSS stream payload is not valid JSON: {exc}") from exc

            items = payload.get("items") or []
            if not isinstance(items, list):
                break

            batch = [normalize_article(item) for item in items if isinstance(item, dict)]
            normalized.extend(batch)

            continuation = str(payload.get("continuation") or "").strip()
            if not continuation or not batch:
                break

        deduped: Dict[str, Dict[str, Any]] = {}
        for article in normalized:
            key = article_fingerprint(article)
            existing = deduped.get(key)
            if not existing or article.get("published_epoch", 0) > existing.get("published_epoch", 0):
                deduped[key] = article
        filtered_items = [item for item in deduped.values() if self._article_in_included_groups(item)]
        if self.max_article_age_hours > 0:
            cutoff_epoch = to_epoch(utc_now() - dt.timedelta(hours=self.max_article_age_hours))
            age_filtered: List[Dict[str, Any]] = []
            for item in filtered_items:
                published_epoch = to_int(item.get("published_epoch"), 0)
                if published_epoch <= 0:
                    if self.include_undated_articles:
                        age_filtered.append(item)
                    continue
                if published_epoch >= cutoff_epoch:
                    age_filtered.append(item)
            filtered_items = age_filtered
        sorted_items = sorted(filtered_items, key=lambda x: x.get("published_epoch", 0), reverse=True)
        return sorted_items[:target_total]

    def fetch_articles(
        self,
        max_items_override: int | None = None,
        since_epoch_override: int | None = None,
    ) -> List[Dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return self._fetch_articles_once(
                    max_items_override=max_items_override,
                    since_epoch_override=since_epoch_override,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retry_attempts:
                    break
                wait_seconds = self.retry_backoff_seconds * (2 ** (attempt - 1))
                print(
                    f"[warn] FreshRSS request failed (attempt {attempt}/{self.retry_attempts}): {exc}. "
                    f"Retrying in {wait_seconds:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
            except PipelineError as exc:
                message = str(exc)
                retryable = "ClientLogin failed (5" in message or "FreshRSS stream fetch failed (5" in message
                if not retryable or attempt >= self.retry_attempts:
                    raise
                last_error = exc
                wait_seconds = self.retry_backoff_seconds * (2 ** (attempt - 1))
                print(
                    f"[warn] FreshRSS server-side error (attempt {attempt}/{self.retry_attempts}): {message}. "
                    f"Retrying in {wait_seconds:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)

        raise PipelineError(
            f"FreshRSS fetch failed after {self.retry_attempts} attempts: {last_error or 'unknown error'}"
        )


class AIExtractor:
    def __init__(self, cfg: Dict[str, Any], event_types: List[str]):
        self.enabled = bool(cfg.get("enabled", True))
        self.base_url = str(cfg.get("base_url") or "").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.model = str(cfg.get("model") or "")
        self.temperature = float(cfg.get("temperature") or 0.1)
        self.max_tokens = int(cfg.get("max_tokens") or 600)
        self.timeout = int(cfg.get("timeout_seconds") or 40)
        self.retry_attempts = max(1, int(cfg.get("retry_attempts") or 2))
        self.retry_backoff_seconds = float(cfg.get("retry_backoff_seconds") or 1.5)
        self.event_types = event_types
        self.calls_total = 0
        self.fallback_total = 0
        self.fallback_timeout_total = 0
        self.fallback_request_total = 0
        self.fallback_response_total = 0
        self.fallback_other_total = 0
        self.heuristic_only_total = 0
        self.session = requests.Session()

        if self.enabled and (not self.base_url or not self.model):
            raise PipelineError("ai.base_url and ai.model are required when ai.enabled=true")

    def get_stats(self) -> Dict[str, Any]:
        fallback_rate = float(self.fallback_total) / float(self.calls_total) if self.calls_total else 0.0
        return {
            "enabled": bool(self.enabled),
            "calls_total": int(self.calls_total),
            "fallback_total": int(self.fallback_total),
            "fallback_timeout_total": int(self.fallback_timeout_total),
            "fallback_request_total": int(self.fallback_request_total),
            "fallback_response_total": int(self.fallback_response_total),
            "fallback_other_total": int(self.fallback_other_total),
            "heuristic_only_total": int(self.heuristic_only_total),
            "fallback_rate": float(fallback_rate),
        }

    def extract(self, article: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            self.heuristic_only_total += 1
            return heuristic_extract(article, self.event_types)

        self.calls_total += 1
        event_types = ", ".join(self.event_types)
        schema_help = {
            "event_type": f"one of: {event_types}",
            "event_subtype": "short subtype label",
            "summary": "short business summary",
            "why_it_matters": "business impact statement for customer success",
            "customer_relevance_hypothesis": "short explanation of why this could matter to enterprise customers",
            "urgency": "integer 1-5",
            "confidence": "float 0-1",
            "time_horizon": "one of immediate|30d|90d|monitor",
            "signals": {
                "health_risk": "float 0-1",
                "cloud_spend_pressure": "float 0-1",
                "churn_risk": "float 0-1",
                "renewal_risk": "float 0-1",
            },
            "impact_vectors": {
                "operational": "float 0-1",
                "financial": "float 0-1",
                "regulatory": "float 0-1",
                "reputation": "float 0-1",
            },
            "entities": {
                "companies": ["list of company names"],
                "products": ["list of product names"],
                "vendors": ["list of vendor names"],
            },
            "evidence_snippets": ["1-3 short evidence snippets from the article"],
            "recommended_actions": ["max 3 actions"],
            "cyera_impact_summary": "1-2 sentence summary of possible impact on customer relationship/investment with Cyera AI/DSPM",
            "cyera_relationship_risk_score": "float 0-1 where 1=high risk of negative impact to Cyera relationship/investment",
            "cyera_relationship_risk_label": "one of low|medium|high",
        }

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a customer success business intelligence analyst. "
                    "Return strict JSON only. No markdown."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            "Extract the business event, key evidence, and customer-impact signals. "
                            "Keep outputs concise and factual."
                        ),
                        "schema": schema_help,
                        "article": {
                            "title": article.get("title"),
                            "source": article.get("source"),
                            "url": article.get("url"),
                            "published_at": article.get("published_at"),
                            "text": article.get("text"),
                        },
                    }
                ),
            },
        ]

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        reason = "other"
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = self.session.post(url, headers=headers, json=payload, timeout=self.timeout)
                if response.status_code >= 400 and "response_format" in response.text:
                    payload_without_response_format = dict(payload)
                    payload_without_response_format.pop("response_format", None)
                    response = self.session.post(
                        url, headers=headers, json=payload_without_response_format, timeout=self.timeout
                    )

                if response.status_code >= 400:
                    raise PipelineError(f"AI request failed ({response.status_code}): {response.text[:300]}")

                data = response.json()
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                parsed = parse_json_block(content)
                return validate_event(parsed, self.event_types, article)
            except requests.Timeout as exc:
                last_error = exc
                reason = "timeout"
                retryable = attempt < self.retry_attempts
            except requests.RequestException as exc:
                last_error = exc
                reason = "request"
                retryable = attempt < self.retry_attempts
            except PipelineError as exc:
                last_error = exc
                reason = "response"
                retryable = False
            except Exception as exc:
                last_error = exc
                reason = "other"
                retryable = False

            if retryable:
                wait_seconds = self.retry_backoff_seconds * (2 ** (attempt - 1))
                print(
                    f"[warn] AI extraction attempt {attempt}/{self.retry_attempts} failed for article={article.get('id')}: "
                    f"{last_error}. Retrying in {wait_seconds:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
                continue
            break

        self.fallback_total += 1
        if reason == "timeout":
            self.fallback_timeout_total += 1
            print(
                f"[warn] AI extraction timeout, falling back to heuristic for article={article.get('id')}: {last_error}",
                file=sys.stderr,
            )
        elif reason == "request":
            self.fallback_request_total += 1
            print(
                f"[warn] AI request failed, falling back to heuristic for article={article.get('id')}: {last_error}",
                file=sys.stderr,
            )
        elif reason == "response":
            self.fallback_response_total += 1
            print(
                f"[warn] AI response invalid, falling back to heuristic for article={article.get('id')}: {last_error}",
                file=sys.stderr,
            )
        else:
            self.fallback_other_total += 1
            print(
                f"[warn] AI extraction failed, falling back to heuristic for article={article.get('id')}: {last_error}",
                file=sys.stderr,
            )
        return heuristic_extract(article, self.event_types)


def parse_json_block(value: str) -> Dict[str, Any]:
    if not value:
        return {}
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def bounded_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def bounded_urgency(value: Any) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, min(5, v))


def bounded_risk_label(value: Any, score: float) -> str:
    label = str(value or "").strip().lower()
    if label in {"low", "medium", "high"}:
        return label
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def ensure_list_strings(value: Any, max_items: int = 12) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def validate_event(raw: Dict[str, Any], event_types: List[str], article: Dict[str, Any]) -> Dict[str, Any]:
    raw_signals = raw.get("signals") if isinstance(raw.get("signals"), dict) else {}
    raw_entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    raw_impacts = raw.get("impact_vectors") if isinstance(raw.get("impact_vectors"), dict) else {}

    event_type = str(raw.get("event_type") or "other").strip().lower()
    if event_type not in event_types:
        event_type = "other"
    event_subtype = str(raw.get("event_subtype") or "").strip().lower()

    summary = str(raw.get("summary") or "").strip()
    if not summary:
        summary = article.get("summary") or article.get("title")

    why = str(raw.get("why_it_matters") or "").strip()
    if not why:
        why = "Monitor for customer impact and confirm relevance with account context."
    relevance_hypothesis = str(raw.get("customer_relevance_hypothesis") or "").strip()
    if not relevance_hypothesis:
        relevance_hypothesis = "Possible relevance to one or more target accounts; validate against account context."

    time_horizon = str(raw.get("time_horizon") or "monitor").strip().lower()
    if time_horizon not in {"immediate", "30d", "90d", "monitor"}:
        time_horizon = "monitor"

    event = {
        "event_type": event_type,
        "event_subtype": event_subtype,
        "summary": summary,
        "why_it_matters": why,
        "customer_relevance_hypothesis": relevance_hypothesis,
        "time_horizon": time_horizon,
        "urgency": bounded_urgency(raw.get("urgency")),
        "confidence": bounded_float(raw.get("confidence"), default=0.45),
        "signals": {
            "health_risk": bounded_float(raw_signals.get("health_risk")),
            "cloud_spend_pressure": bounded_float(raw_signals.get("cloud_spend_pressure")),
            "churn_risk": bounded_float(raw_signals.get("churn_risk")),
            "renewal_risk": bounded_float(raw_signals.get("renewal_risk")),
        },
        "impact_vectors": {
            "operational": bounded_float(raw_impacts.get("operational")),
            "financial": bounded_float(raw_impacts.get("financial")),
            "regulatory": bounded_float(raw_impacts.get("regulatory")),
            "reputation": bounded_float(raw_impacts.get("reputation")),
        },
        "entities": {
            "companies": ensure_list_strings(raw_entities.get("companies")),
            "products": ensure_list_strings(raw_entities.get("products")),
            "vendors": ensure_list_strings(raw_entities.get("vendors")),
        },
        "evidence_snippets": ensure_list_strings(raw.get("evidence_snippets"), max_items=3),
        "recommended_actions": ensure_list_strings(raw.get("recommended_actions"), max_items=3),
        "cyera_impact_summary": clean_text(str(raw.get("cyera_impact_summary") or ""), max_chars=320),
        "cyera_relationship_risk_score": bounded_float(raw.get("cyera_relationship_risk_score"), default=0.35),
    }
    event["cyera_relationship_risk_label"] = bounded_risk_label(
        raw.get("cyera_relationship_risk_label"),
        float(event["cyera_relationship_risk_score"]),
    )
    if not event["evidence_snippets"]:
        snippet = clean_text(str(article.get("summary") or ""), max_chars=220)
        if snippet:
            event["evidence_snippets"] = [snippet]
    if not event["recommended_actions"]:
        event["recommended_actions"] = ["Share the event in the account plan and validate likely impact with the customer team."]
    if not event["cyera_impact_summary"]:
        event["cyera_impact_summary"] = (
            "Potential AI/DSPM relevance for Cyera should be validated against the account's active priorities and stack."
        )
    return event


def heuristic_extract(article: Dict[str, Any], event_types: List[str]) -> Dict[str, Any]:
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    mappings = [
        ("security_incident", ["breach", "vulnerability", "ransomware", "cve", "exploit"]),
        ("outage_incident", ["outage", "downtime", "incident", "degraded", "service disruption"]),
        ("pricing_change", ["price", "pricing", "cost increase", "rate card", "license fee"]),
        ("layoffs_reorg", ["layoff", "laid off", "restructuring", "reorg", "workforce reduction"]),
        ("funding_mna", ["acquire", "merger", "funding", "raises", "series a", "series b"]),
        ("cloud_cost_signal", ["aws", "azure", "gcp", "cloud bill", "gpu pricing", "egress"]),
        ("regulatory_change", ["regulation", "compliance", "sec", "gdpr", "privacy law"]),
        ("partner_change", ["partnership", "reseller", "channel", "integration partnership"]),
        ("product_launch", ["launch", "announced", "general availability", "ga", "roadmap"]),
    ]

    picked = "other"
    for event_type, words in mappings:
        if any(word in text for word in words):
            picked = event_type
            break
    if picked not in event_types:
        picked = "other"

    cloud_signal = 0.8 if any(k in text for k in ["price", "pricing", "aws", "azure", "gcp", "gpu", "cloud bill"]) else 0.2
    health_signal = 0.75 if any(k in text for k in ["outage", "incident", "degraded", "security", "breach"]) else 0.25
    churn_signal = 0.65 if any(k in text for k in ["layoff", "outage", "security", "price increase"]) else 0.2
    renewal_signal = 0.6 if any(k in text for k in ["price", "renewal", "contract", "budget"]) else 0.25

    urgency = 4 if picked in {"security_incident", "outage_incident"} else 3
    if picked == "other":
        urgency = 2

    companies = []
    for word in re.findall(r"\b[A-Z][a-zA-Z0-9&.-]{2,}\b", article.get("title", "")):
        if word.lower() not in {"the", "and", "for", "with"}:
            companies.append(word)
        if len(companies) >= 6:
            break

    return {
        "event_type": picked,
        "event_subtype": "",
        "summary": article.get("summary") or article.get("title"),
        "why_it_matters": "Potential impact on customer health and spend should be validated by account owners.",
        "customer_relevance_hypothesis": "Potential relevance depends on whether the vendor or event intersects customer priorities.",
        "time_horizon": "monitor",
        "urgency": urgency,
        "confidence": 0.42,
        "signals": {
            "health_risk": health_signal,
            "cloud_spend_pressure": cloud_signal,
            "churn_risk": churn_signal,
            "renewal_risk": renewal_signal,
        },
        "impact_vectors": {
            "operational": health_signal,
            "financial": cloud_signal,
            "regulatory": 0.25,
            "reputation": churn_signal,
        },
        "entities": {"companies": companies, "products": [], "vendors": companies[:4]},
        "evidence_snippets": [clean_text(str(article.get("summary") or article.get("title") or ""), max_chars=220)],
        "recommended_actions": [
            "Check whether the affected vendor is in the customer tech stack.",
            "If relevant, brief CSM/AM with a short impact note and next-step recommendation.",
        ],
        "cyera_impact_summary": "Potential customer-impact signal for security, compliance, or AI governance; verify Cyera DSPM relevance.",
        "cyera_relationship_risk_score": clamp(max(health_signal, churn_signal, renewal_signal), 0.2, 0.85),
        "cyera_relationship_risk_label": bounded_risk_label("", max(health_signal, churn_signal, renewal_signal)),
    }


def phrase_in_text(text_blob: str, phrase: str) -> bool:
    token = phrase.lower().strip()
    if not token:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
    return bool(re.search(pattern, text_blob))


def score_for_customer(
    article: Dict[str, Any],
    event: Dict[str, Any],
    customer: Dict[str, Any],
    defaults: Dict[str, Any],
    extra_context: Dict[str, Any] | None = None,
) -> Tuple[float, Dict[str, Any]]:
    extra = extra_context or {}
    weights = customer.get("weights") if isinstance(customer.get("weights"), dict) else {}
    business_context = customer.get("business_context") if isinstance(customer.get("business_context"), dict) else {}
    text_blob = " ".join(
        [
            article.get("title", ""),
            article.get("summary", ""),
            article.get("full_text", ""),
            " ".join(event.get("entities", {}).get("companies", [])),
            " ".join(event.get("entities", {}).get("products", [])),
            " ".join(event.get("entities", {}).get("vendors", [])),
        ]
    ).lower()

    def hit_count(values: List[str]) -> int:
        hits = 0
        for v in values:
            if phrase_in_text(text_blob, v):
                hits += 1
        return hits

    keywords = [str(x) for x in customer.get("keywords") or []]
    competitors = [str(x) for x in customer.get("competitors") or []]
    cloud = [str(x) for x in customer.get("cloud_keywords") or []]
    feed_urls = [str(x).rstrip("/") for x in customer.get("feed_urls") or []]
    strategic_priorities = normalize_list_strings(business_context.get("exec_priorities"))
    stack_confirmed = normalize_list_strings(business_context.get("stack_confirmed"))
    stack_possible = normalize_list_strings(business_context.get("stack_possible"))
    open_risks = normalize_list_strings(business_context.get("open_risks"))
    stage = str(business_context.get("stage") or "").strip().lower()
    context_terms = [
        *[str(x) for x in customer.get("context_terms") or []],
        *[str(x) for x in customer.get("tech_stack") or []],
        *[str(x) for x in customer.get("strategic_themes") or []],
        *[str(x) for x in customer.get("known_risks") or []],
    ]

    keyword_hits = hit_count(keywords)
    competitor_hits = hit_count(competitors)
    cloud_hits = hit_count(cloud)
    context_hits = hit_count(context_terms)
    strategic_priority_hits = hit_count(strategic_priorities)
    stack_confirmed_hits = hit_count(stack_confirmed)
    stack_possible_hits = hit_count(stack_possible)
    open_risk_hits = hit_count(open_risks)
    committee_priority_hits_weighted = 0.0
    for member in business_context.get("decision_committee") or []:
        if not isinstance(member, dict):
            continue
        priorities = normalize_list_strings(member.get("priorities"))
        if not priorities:
            continue
        influence = clamp(to_float(member.get("influence"), 0.5), 0.1, 1.0)
        committee_priority_hits_weighted += float(hit_count(priorities)) * influence

    article_feed_url = str(article.get("feed_url") or "").rstrip("/")
    feed_match = 1 if feed_urls and article_feed_url and article_feed_url in feed_urls else 0

    domain_match = 0
    customer_url = str(customer.get("url") or "").strip()
    if customer_url and article.get("url"):
        try:
            customer_domain = urlsplit(customer_url).netloc.lower().removeprefix("www.")
            article_domain = urlsplit(article.get("url")).netloc.lower().removeprefix("www.")
            if customer_domain and customer_domain == article_domain:
                domain_match = 1
        except Exception:
            pass

    event_type_weights = customer.get("event_type_weights") if isinstance(customer.get("event_type_weights"), dict) else {}
    event_type_boost = float(event_type_weights.get(event.get("event_type"), 0.0))
    novelty_score = clamp(float(extra.get("novelty") or 0.0), 0.0, 1.0)
    feedback_multiplier = clamp(float(extra.get("feedback_multiplier") or 1.0), 0.6, 1.4)
    account_multiplier = clamp(float(extra.get("account_multiplier") or 1.0), 0.6, 1.5)
    memory_multiplier = clamp(float(extra.get("memory_multiplier") or 1.0), 0.85, 1.2)
    memory_topic_hits = max(0, int(extra.get("memory_topic_hits") or 0))
    memory_unresolved_hits = max(0, int(extra.get("memory_unresolved_hits") or 0))

    stage_boost_map = defaults.get("stage_boosts") if isinstance(defaults.get("stage_boosts"), dict) else {}
    stage_boost = to_float(stage_boost_map.get(stage), 0.0) if stage else 0.0
    competitor_pressure_event_weight = {
        "pricing_change": 1.0,
        "product_launch": 0.8,
        "funding_mna": 0.6,
        "partner_change": 0.4,
    }
    competitor_pressure_signal = float(competitor_hits) * competitor_pressure_event_weight.get(
        str(event.get("event_type") or "other"), 0.2
    )

    renewal_pressure = 0.0
    renewal_date = parse_iso_date(customer.get("renewal_date"))
    if renewal_date is not None:
        days_until = int((renewal_date - utc_now()).total_seconds() / 86400)
        renewal_window_days = max(1, int(defaults.get("renewal_window_days", 90)))
        if 0 <= days_until <= renewal_window_days:
            risk_proxy = max(
                float((event.get("signals") or {}).get("renewal_risk") or 0.0),
                float((event.get("signals") or {}).get("churn_risk") or 0.0),
                float((event.get("impact_vectors") or {}).get("financial") or 0.0),
            )
            renewal_pressure = risk_proxy * float(weights.get("renewal_window_boost", defaults.get("renewal_window_boost", 10.0)))

    component = {
        "keyword_match": keyword_hits * float(weights.get("keyword_match", defaults.get("keyword_match", 5.5))),
        "competitor_match": competitor_hits * float(weights.get("competitor_match", defaults.get("competitor_match", 8.0))),
        "cloud_match": cloud_hits * float(weights.get("cloud_match", defaults.get("cloud_match", 7.0))),
        "context_match": context_hits * float(weights.get("context_match", defaults.get("context_match", 4.0))),
        "feed_match": feed_match * float(weights.get("feed_match", defaults.get("feed_match", 30.0))),
        "domain_match": domain_match * float(weights.get("domain_match", defaults.get("domain_match", 12.0))),
        "event_type": event_type_boost,
        "strategic_priority_match": strategic_priority_hits
        * float(weights.get("strategic_priority_match", defaults.get("strategic_priority_match", 6.0))),
        "stack_confirmed_match": stack_confirmed_hits
        * float(weights.get("stack_confirmed_match", defaults.get("stack_confirmed_match", 7.0))),
        "stack_possible_match": stack_possible_hits
        * float(weights.get("stack_possible_match", defaults.get("stack_possible_match", 3.5))),
        "open_risk_match": open_risk_hits * float(weights.get("open_risk_match", defaults.get("open_risk_match", 5.0))),
        "committee_priority_match": committee_priority_hits_weighted
        * float(weights.get("committee_priority_match", defaults.get("committee_priority_match", 4.5))),
        "account_stage_boost": stage_boost,
        "urgency": float(event.get("urgency", 2)) * float(weights.get("urgency", defaults.get("urgency", 4.0))),
        "health_risk": float(event.get("signals", {}).get("health_risk", 0.0))
        * float(weights.get("health_risk", defaults.get("health_risk", 20.0))),
        "cloud_spend_pressure": float(event.get("signals", {}).get("cloud_spend_pressure", 0.0))
        * float(weights.get("cloud_spend_pressure", defaults.get("cloud_spend_pressure", 20.0))),
        "churn_risk": float(event.get("signals", {}).get("churn_risk", 0.0))
        * float(weights.get("churn_risk", defaults.get("churn_risk", 16.0))),
        "renewal_risk": float(event.get("signals", {}).get("renewal_risk", 0.0))
        * float(weights.get("renewal_risk", defaults.get("renewal_risk", 16.0))),
        "novelty": novelty_score * float(weights.get("novelty", defaults.get("novelty", 10.0))),
        "memory_topic_match": float(memory_topic_hits)
        * float(weights.get("memory_topic_match", defaults.get("memory_topic_match", 3.0))),
        "memory_unresolved_match": float(memory_unresolved_hits)
        * float(weights.get("memory_unresolved_match", defaults.get("memory_unresolved_match", 4.0))),
        "renewal_window_pressure": renewal_pressure,
    }
    raw = sum(component.values())
    confidence = float(event.get("confidence", 0.4))
    confidence_factor = 0.6 + (0.4 * max(0.0, min(1.0, confidence)))
    score = min(100.0, raw * confidence_factor * feedback_multiplier * account_multiplier * memory_multiplier)

    details = {
        "keyword_hits": keyword_hits,
        "competitor_hits": competitor_hits,
        "cloud_hits": cloud_hits,
        "context_hits": context_hits,
        "strategic_priority_hits": strategic_priority_hits,
        "stack_confirmed_hits": stack_confirmed_hits,
        "stack_possible_hits": stack_possible_hits,
        "open_risk_hits": open_risk_hits,
        "committee_priority_hits_weighted": round(committee_priority_hits_weighted, 2),
        "feed_match": feed_match,
        "domain_match": domain_match,
        "component": component,
        "confidence_factor": confidence_factor,
        "feedback_multiplier": feedback_multiplier,
        "account_multiplier": account_multiplier,
        "memory_multiplier": memory_multiplier,
        "memory_topic_hits": memory_topic_hits,
        "memory_unresolved_hits": memory_unresolved_hits,
        "feedback_components": extra.get("feedback_components") or {},
        "competitor_pressure_signal": round(competitor_pressure_signal, 2),
        "novelty": novelty_score,
    }
    return round(score, 2), details


def write_outputs(
    run_dir: Path,
    run_payload: Dict[str, Any],
    top_n: int,
    customer_names: List[str] | None = None,
) -> Dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_path = run_dir / "events.json"
    alerts_path = run_dir / "alerts.json"
    digest_path = run_dir / "digest.md"
    briefs_path = run_dir / "customer_briefs.md"
    timeline_path = run_dir / "timeline.json"
    triage_path = run_dir / "triage_queue.json"

    save_json(raw_path, run_payload)

    alerts_only = {
        "run_at": run_payload.get("run_at"),
        "total_alerts": run_payload.get("total_alerts"),
        "customer_alerts": run_payload.get("customer_alerts"),
    }
    save_json(alerts_path, alerts_only)

    lines = [
        f"# Customer Intelligence Digest ({run_payload.get('run_at')})",
        "",
        f"- Total fetched articles: {run_payload.get('fetched_articles', 0)}",
        f"- New analyzed articles: {run_payload.get('new_articles', 0)}",
        f"- Total alerts above threshold: {run_payload.get('total_alerts', 0)}",
        f"- Watchlist items (needs review): {run_payload.get('watchlist_total', 0)}",
        f"- Needs-data items (insufficient evidence): {run_payload.get('needs_data_total', 0)}",
        f"- Suppressed by cooldown: {run_payload.get('suppressed_cooldown_alerts', 0)}",
        f"- Low-confidence blocked alerts: {run_payload.get('low_confidence_blocked_alerts', 0)}",
        "",
    ]
    customer_alerts = run_payload.get("customer_alerts") or {}
    customer_watchlist = run_payload.get("customer_watchlist") or {}
    customer_needs_data = run_payload.get("customer_needs_data") or {}
    display_customers = customer_names or sorted(customer_alerts.keys())
    account_heat = run_payload.get("account_heat") if isinstance(run_payload.get("account_heat"), dict) else {}
    competitor_pressure = run_payload.get("competitor_pressure") if isinstance(run_payload.get("competitor_pressure"), dict) else {}
    opportunity_index = (
        run_payload.get("opportunity_index_by_customer")
        if isinstance(run_payload.get("opportunity_index_by_customer"), dict)
        else {}
    )
    heat_forecast = (
        run_payload.get("heat_forecast_by_customer")
        if isinstance(run_payload.get("heat_forecast_by_customer"), dict)
        else {}
    )
    story_changes = (
        run_payload.get("customer_story_changes")
        if isinstance(run_payload.get("customer_story_changes"), dict)
        else {}
    )
    positive_signals = (
        run_payload.get("positive_signals_by_customer")
        if isinstance(run_payload.get("positive_signals_by_customer"), dict)
        else {}
    )
    cyera_negative_impact = (
        run_payload.get("cyera_negative_impact_by_customer")
        if isinstance(run_payload.get("cyera_negative_impact_by_customer"), dict)
        else {}
    )
    cyera_avg_risk = (
        run_payload.get("cyera_avg_risk_by_customer")
        if isinstance(run_payload.get("cyera_avg_risk_by_customer"), dict)
        else {}
    )
    theme_trends = run_payload.get("theme_trends") if isinstance(run_payload.get("theme_trends"), dict) else {}
    outcome_by_customer = (
        run_payload.get("outcome_counts_by_customer") if isinstance(run_payload.get("outcome_counts_by_customer"), dict) else {}
    )
    customer_deltas = run_payload.get("customer_deltas") if isinstance(run_payload.get("customer_deltas"), dict) else {}
    for customer in display_customers:
        alerts = customer_alerts.get(customer) or []
        watch_items = customer_watchlist.get(customer) or []
        lines.append(f"## {customer}")
        heat = account_heat.get(customer) if isinstance(account_heat.get(customer), dict) else {}
        if heat:
            lines.append(
                "Account heat: {score} ({band}) | competitor pressure: {pressure}".format(
                    score=heat.get("score", 0),
                    band=heat.get("band", "low"),
                    pressure=competitor_pressure.get(customer, 0.0),
                )
            )
            lines.append(
                "Opportunity index: {index} | positive signals: {count}".format(
                    index=opportunity_index.get(customer, 0.0),
                    count=positive_signals.get(customer, 0),
                )
            )
            lines.append(
                "Cyera risk: avg={avg} | negative-impact items={neg}".format(
                    avg=cyera_avg_risk.get(customer, 0.0),
                    neg=cyera_negative_impact.get(customer, 0),
                )
            )
        forecast_entry = heat_forecast.get(customer) if isinstance(heat_forecast.get(customer), dict) else {}
        if forecast_entry:
            lines.append(
                "Forecast heat: 30d={f30} | 90d={f90}".format(
                    f30=forecast_entry.get("forecast_30d", 0.0),
                    f90=forecast_entry.get("forecast_90d", 0.0),
                )
            )
        story_delta = story_changes.get(customer) if isinstance(story_changes.get(customer), dict) else {}
        if story_delta:
            lines.append(
                "Story changes: +{a} / ~{c} / -{r}".format(
                    a=to_int(story_delta.get("added_count"), 0),
                    c=to_int(story_delta.get("changed_count"), 0),
                    r=to_int(story_delta.get("removed_count"), 0),
                )
            )
        trend = theme_trends.get(customer) if isinstance(theme_trends.get(customer), dict) else {}
        if trend:
            lines.append(f"Theme trend highlights: {', '.join(f'{k}={v}' for k, v in trend.items())}")
        outcomes = outcome_by_customer.get(customer) if isinstance(outcome_by_customer.get(customer), dict) else {}
        if outcomes:
            lines.append(f"Recorded outcomes: {', '.join(f'{k}:{v}' for k, v in sorted(outcomes.items()))}")
        delta = customer_deltas.get(customer) if isinstance(customer_deltas.get(customer), dict) else {}
        if delta:
            lines.append(
                "Delta vs previous run: alerts={a:+.0f}, watchlist={w:+.0f}, needs_data={n:+.0f}, "
                "heat={h:+.2f}, opportunity={o:+.2f}, positive_signals={p:+.0f}".format(
                    a=float(delta.get("alerts_delta") or 0.0),
                    w=float(delta.get("watchlist_delta") or 0.0),
                    n=float(delta.get("needs_data_delta") or 0.0),
                    h=float(delta.get("heat_delta") or 0.0),
                    o=float(delta.get("opportunity_delta") or 0.0),
                    p=float(delta.get("positive_signals_delta") or 0.0),
                )
            )
        picked = sorted(alerts, key=lambda x: x.get("score", 0), reverse=True)[:top_n]
        if not picked:
            lines.append("No alerts above threshold.")
        else:
            for idx, item in enumerate(picked, start=1):
                source = str(item.get("source", "unknown"))
                source_domain = str(item.get("source_domain") or "").strip()
                if source_domain and source_domain != "unknown":
                    source = f"{source} ({source_domain})"
                lines.append(f"{idx}. **{item.get('article_title', 'Untitled')}** ({item.get('score', 0)})")
                lines.append(
                    f"   - Source: {source} | Type: {item.get('event_type', 'other')} | Urgency: {item.get('urgency', 2)}"
                )
                lines.append(f"   - Why it matters: {item.get('why_it_matters', '')}")
                lines.append(f"   - Suggested action: {', '.join(item.get('recommended_actions', []))}")
                if float(item.get("opportunity_score") or 0.0) > 0:
                    lines.append(f"   - Opportunity score: {item.get('opportunity_score')}")
                lines.append(
                    "   - Cyera AI/DSPM impact: {label} ({score}) | {summary}".format(
                        label=item.get("cyera_relationship_risk_label", "low"),
                        score=item.get("cyera_relationship_risk_score", 0.0),
                        summary=item.get("cyera_impact_summary", ""),
                    )
                )
                playbooks = item.get("playbooks") if isinstance(item.get("playbooks"), list) else []
                if playbooks:
                    lines.append(f"   - Playbook: {', '.join(playbooks)}")
                if item.get("url"):
                    lines.append(f"   - Link: {item.get('url')}")

        if watch_items:
            lines.append("")
            lines.append("### Needs Review")
            for idx, item in enumerate(sorted(watch_items, key=lambda x: x.get("score", 0), reverse=True)[:top_n], start=1):
                lines.append(
                    f"{idx}. {item.get('article_title', 'Untitled')} "
                    f"(score={item.get('score', 0)}, confidence={item.get('confidence', 0)})"
                )
        needs_items = customer_needs_data.get(customer) or []
        if needs_items:
            lines.append("")
            lines.append("### Needs Data")
            for idx, item in enumerate(sorted(needs_items, key=lambda x: x.get("score", 0), reverse=True)[:top_n], start=1):
                lines.append(
                    f"{idx}. {item.get('article_title', 'Untitled')} "
                    f"(score={item.get('score', 0)}, confidence={item.get('confidence', 0)}, reason={item.get('needs_data_reason', 'unknown')})"
                )
        lines.append("")

    coverage_gaps = run_payload.get("coverage_gaps") or []
    if coverage_gaps:
        lines.append("## Coverage Gaps")
        lines.append("Customers with no recent high-confidence alerts:")
        for item in coverage_gaps:
            lines.append(f"- {item.get('customer')}: {item.get('days_since_last_alert')} days since last alert")
            for suggestion in item.get("suggestions") or []:
                lines.append(f"  - suggestion: {suggestion}")
        lines.append("")

    digest_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    brief_lines = [f"# Customer Executive Briefs ({run_payload.get('run_at')})", ""]
    for customer in display_customers:
        alerts = sorted(customer_alerts.get(customer) or [], key=lambda x: x.get("score", 0), reverse=True)
        watch_items = sorted(customer_watchlist.get(customer) or [], key=lambda x: x.get("score", 0), reverse=True)
        needs_items = sorted(customer_needs_data.get(customer) or [], key=lambda x: x.get("score", 0), reverse=True)
        heat = account_heat.get(customer) if isinstance(account_heat.get(customer), dict) else {}
        brief_lines.append(f"## {customer}")
        if heat:
            brief_lines.append(f"- Heat score: {heat.get('score', 0)} ({heat.get('band', 'low')})")
        brief_lines.append(f"- Competitor pressure index: {competitor_pressure.get(customer, 0.0)}")
        brief_lines.append(f"- Opportunity index: {opportunity_index.get(customer, 0.0)}")
        brief_lines.append("- Top Risks:")
        risk_items = [a for a in alerts if str(a.get("event_type")) in {"security_incident", "outage_incident", "pricing_change"}]
        for item in risk_items[:3]:
            brief_lines.append(f"  - {item.get('article_title')} ({item.get('event_type')}, score={item.get('score')})")
        if not risk_items:
            brief_lines.append("  - No immediate high-priority risks detected.")
        brief_lines.append("- Opportunities:")
        opp_items = [a for a in alerts if str(a.get("event_type")) in {"product_launch", "partner_change", "customer_reference"}]
        for item in opp_items[:3]:
            brief_lines.append(f"  - {item.get('article_title')} ({item.get('event_type')}, score={item.get('score')})")
        if not opp_items:
            brief_lines.append("  - No strong expansion signals this run.")
        brief_lines.append("- Executive Talking Points:")
        top_item = alerts[0] if alerts else (watch_items[0] if watch_items else (needs_items[0] if needs_items else {}))
        if top_item:
            brief_lines.append(f"  - {top_item.get('why_it_matters')}")
            hypothesis = str(top_item.get("customer_relevance_hypothesis") or "").strip()
            if hypothesis:
                brief_lines.append(f"  - {hypothesis}")
            playbooks = top_item.get("playbooks") if isinstance(top_item.get("playbooks"), list) else []
            if playbooks:
                brief_lines.append(f"  - Next action: {playbooks[0]}")
        else:
            brief_lines.append("  - Maintain monitoring; no material changes this run.")
        brief_lines.append("")

    briefs_path.write_text("\n".join(brief_lines).strip() + "\n", encoding="utf-8")
    save_json(
        timeline_path,
        {
            "run_at": run_payload.get("run_at"),
            "story_changes": story_changes,
            "customer_deltas": run_payload.get("customer_deltas") or {},
        },
    )
    triage_rows: List[Dict[str, Any]] = []
    for customer in display_customers:
        for item in (customer_watchlist.get(customer) or []):
            triage_rows.append(
                {
                    "customer": customer,
                    "queue": "watchlist",
                    "routing_reason": item.get("routing_reason", "below_threshold"),
                    "story_id": item.get("story_id"),
                    "title": item.get("article_title"),
                    "score": item.get("score"),
                    "confidence": item.get("confidence"),
                    "url": item.get("url"),
                }
            )
        for item in (customer_needs_data.get(customer) or []):
            triage_rows.append(
                {
                    "customer": customer,
                    "queue": "needs_data",
                    "routing_reason": item.get("needs_data_reason", "needs_data"),
                    "story_id": item.get("story_id"),
                    "title": item.get("article_title"),
                    "score": item.get("score"),
                    "confidence": item.get("confidence"),
                    "url": item.get("url"),
                }
            )
    save_json(
        triage_path,
        {
            "run_at": run_payload.get("run_at"),
            "items": triage_rows,
            "total": len(triage_rows),
        },
    )
    return {
        "events": raw_path,
        "alerts": alerts_path,
        "digest": digest_path,
        "briefs": briefs_path,
        "timeline": timeline_path,
        "triage_queue": triage_path,
    }


def post_webhook(
    cfg: Dict[str, Any],
    payload: Dict[str, Any],
    previous_gap_customers: Optional[Set[str]] = None,
) -> None:
    url = str(cfg.get("webhook_url") or "").strip()
    if not url:
        return

    total_alerts = int(payload.get("total_alerts") or 0)
    watchlist_total = int(payload.get("watchlist_total") or 0)
    coverage_gaps = payload.get("coverage_gaps") or []
    current_gap_customers = {str(g.get("customer") or "") for g in coverage_gaps if g.get("customer")}

    # Suppress steady-state runs that have nothing actionable to report. The
    # daily digest (separate cronjob) still summarizes the standing coverage
    # gap list, so visibility isn't lost — this just stops the every-30-min
    # "0 alerts · 0 watchlist · N coverage gaps" spam in Matrix.
    # First run (previous_gap_customers is None) still posts so the user sees
    # initial state.
    if total_alerts == 0 and watchlist_total == 0:
        if previous_gap_customers is not None and current_gap_customers == previous_gap_customers:
            print(f"[ok] webhook suppressed: 0 alerts, 0 watchlist, gap set unchanged ({len(current_gap_customers)} customers)", file=sys.stderr)
            return

    timeout = int(cfg.get("webhook_timeout_seconds") or 10)
    body = {
        "run_at": payload.get("run_at"),
        "total_alerts": total_alerts,
        "watchlist_total": watchlist_total,
        "coverage_gaps": coverage_gaps,
        "top": {},
    }
    for customer, alerts in (payload.get("customer_alerts") or {}).items():
        sorted_alerts = sorted(alerts, key=lambda x: x.get("score", 0), reverse=True)
        body["top"][customer] = sorted_alerts[:3]
    try:
        response = requests.post(url, json=body, timeout=timeout)
        if response.status_code >= 400:
            print(f"[warn] Webhook failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
    except Exception as exc:
        print(f"[warn] Webhook request failed: {exc}", file=sys.stderr)


def run_action_hooks(cfg: Dict[str, Any], payload: Dict[str, Any]) -> None:
    if not bool(cfg.get("enabled", False)):
        return
    hooks = cfg.get("hooks") if isinstance(cfg.get("hooks"), list) else []
    min_score = clamp(to_float(cfg.get("min_score"), 70.0), 0.0, 100.0)
    max_alerts_per_customer = max(1, to_int(cfg.get("max_alerts_per_customer"), 3))
    timeout_seconds = max(2, to_int(cfg.get("timeout_seconds"), 8))
    if not hooks:
        return

    customer_alerts = payload.get("customer_alerts") if isinstance(payload.get("customer_alerts"), dict) else {}
    selected_alerts: Dict[str, List[Dict[str, Any]]] = {}
    for customer, alerts in customer_alerts.items():
        if not isinstance(alerts, list):
            continue
        picked = [a for a in sorted(alerts, key=lambda x: x.get("score", 0), reverse=True) if float(a.get("score") or 0.0) >= min_score]
        if picked:
            selected_alerts[str(customer)] = picked[:max_alerts_per_customer]
    if not selected_alerts:
        return

    body = {
        "run_at": payload.get("run_at"),
        "type": "freshrss_bi_action_hook",
        "alerts": selected_alerts,
        "counts": {k: len(v) for k, v in selected_alerts.items()},
    }
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        url = str(hook.get("url") or "").strip()
        if not url:
            continue
        token = str(hook.get("token") or "").strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout_seconds)
            if response.status_code >= 400:
                print(f"[warn] action hook failed ({response.status_code}) url={url}", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] action hook request failed url={url}: {exc}", file=sys.stderr)


class InfluxDBWriter:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", False))
        self.url = str(cfg.get("url") or "").rstrip("/")
        self.org = str(cfg.get("org") or "")
        self.bucket = str(cfg.get("bucket") or "")
        self.token = str(cfg.get("token") or "")
        self.timeout = int(cfg.get("timeout_seconds") or 10)
        self.measurement_prefix = str(cfg.get("measurement_prefix") or "freshrss_bi")

        if self.enabled and (not self.url or not self.org or not self.bucket or not self.token):
            raise PipelineError("influxdb requires url, org, bucket, and token when enabled=true")

    def write(self, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            return

        run_at = str(payload.get("run_at") or "")
        ts = parse_iso_to_epoch(run_at)
        customer_alerts = payload.get("customer_alerts") or {}
        ai_stats = payload.get("ai_stats") or {}
        ai_calls = int(ai_stats.get("calls_total") or 0)
        ai_fallbacks = int(ai_stats.get("fallback_total") or 0)
        ai_fallback_rate = float(ai_stats.get("fallback_rate") or 0.0)
        enrichment_stats = payload.get("enrichment_stats") or {}
        feedback_stats = payload.get("feedback_stats") or {}
        coverage_gaps = payload.get("coverage_gaps") or []

        lines: List[str] = []
        lines.append(
            influx_line(
                measurement=f"{self.measurement_prefix}_run",
                tags={"pipeline": "freshrss_bi"},
                fields={
                    "fetched_articles": int(payload.get("fetched_articles") or 0),
                    "new_articles": int(payload.get("new_articles") or 0),
                    "total_alerts": int(payload.get("total_alerts") or 0),
                    "customer_count": int(len(customer_alerts)),
                    "configured_customer_count": int(payload.get("configured_customer_count") or len(customer_alerts)),
                    "alerted_customer_count": int(payload.get("alerted_customer_count") or len(customer_alerts)),
                    "run_duration_seconds": float(payload.get("run_duration_seconds") or 0.0),
                    "ai_calls": int(ai_calls),
                    "ai_fallbacks": int(ai_fallbacks),
                    "ai_fallback_rate": float(ai_fallback_rate),
                    "ai_fallback_timeout": int(ai_stats.get("fallback_timeout_total") or 0),
                    "ai_fallback_request": int(ai_stats.get("fallback_request_total") or 0),
                    "ai_fallback_response": int(ai_stats.get("fallback_response_total") or 0),
                    "ai_fallback_other": int(ai_stats.get("fallback_other_total") or 0),
                    "suppressed_cooldown_alerts": int(payload.get("suppressed_cooldown_alerts") or 0),
                    "low_confidence_blocked_alerts": int(payload.get("low_confidence_blocked_alerts") or 0),
                    "watchlist_total": int(payload.get("watchlist_total") or 0),
                    "needs_data_total": int(payload.get("needs_data_total") or 0),
                    "story_clusters": int(payload.get("story_clusters") or 0),
                    "enrichment_attempted": int(enrichment_stats.get("attempted") or 0),
                    "enrichment_succeeded": int(enrichment_stats.get("succeeded") or 0),
                    "enrichment_failed": int(enrichment_stats.get("failed") or 0),
                    "feedback_processed": int(feedback_stats.get("processed") or 0),
                    "feedback_positive": int(feedback_stats.get("positive") or 0),
                    "feedback_negative": int(feedback_stats.get("negative") or 0),
                    "feedback_relevant_rate": float(feedback_stats.get("relevant_rate") or 0.0),
                    "coverage_gap_customers": int(len(coverage_gaps)),
                    "output_runs_pruned_age": int(((payload.get("output_retention") or {}).get("removed_by_age")) or 0),
                    "output_runs_pruned_count": int(
                        ((payload.get("output_retention") or {}).get("removed_by_count")) or 0
                    ),
                },
                timestamp_epoch=ts,
            )
        )

        for event_type, count in sorted((payload.get("event_type_counts") or {}).items()):
            count_int = int(count or 0)
            if count_int <= 0:
                continue
            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_event_type_summary",
                    tags={"event_type": str(event_type)},
                    fields={"alerts_count": count_int},
                    timestamp_epoch=ts,
                )
            )

        lines.append(
            influx_line(
                measurement=f"{self.measurement_prefix}_model_health",
                tags={"pipeline": "freshrss_bi"},
                fields={
                    "ai_fallback_timeout": int(ai_stats.get("fallback_timeout_total") or 0),
                    "ai_fallback_request": int(ai_stats.get("fallback_request_total") or 0),
                    "ai_fallback_response": int(ai_stats.get("fallback_response_total") or 0),
                    "ai_fallback_other": int(ai_stats.get("fallback_other_total") or 0),
                    "ai_calls": int(ai_calls),
                    "ai_fallbacks": int(ai_fallbacks),
                    "enrichment_attempted": int(enrichment_stats.get("attempted") or 0),
                    "enrichment_succeeded": int(enrichment_stats.get("succeeded") or 0),
                    "enrichment_failed": int(enrichment_stats.get("failed") or 0),
                },
                timestamp_epoch=ts,
            )
        )

        noise_summary = payload.get("noise_summary") or {}
        for reason, count in sorted(noise_summary.items()):
            count_int = int(count or 0)
            if count_int <= 0:
                continue
            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_noise_summary",
                    tags={"reason": str(reason)},
                    fields={"count": count_int},
                    timestamp_epoch=ts,
                )
            )

        for idx, (source, count) in enumerate((payload.get("source_counts") or {}).items()):
            if idx >= 30:
                break
            count_int = int(count or 0)
            if count_int <= 0:
                continue
            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_source_summary",
                    tags={"source": str(source)},
                    fields={"alerts_count": count_int},
                    timestamp_epoch=ts,
                )
            )

        account_heat = payload.get("account_heat") if isinstance(payload.get("account_heat"), dict) else {}
        competitor_pressure = payload.get("competitor_pressure") if isinstance(payload.get("competitor_pressure"), dict) else {}
        opportunity_index = (
            payload.get("opportunity_index_by_customer")
            if isinstance(payload.get("opportunity_index_by_customer"), dict)
            else {}
        )
        heat_forecast = (
            payload.get("heat_forecast_by_customer")
            if isinstance(payload.get("heat_forecast_by_customer"), dict)
            else {}
        )
        positive_signals = (
            payload.get("positive_signals_by_customer")
            if isinstance(payload.get("positive_signals_by_customer"), dict)
            else {}
        )
        cyera_negative_impact = (
            payload.get("cyera_negative_impact_by_customer")
            if isinstance(payload.get("cyera_negative_impact_by_customer"), dict)
            else {}
        )
        cyera_avg_risk = (
            payload.get("cyera_avg_risk_by_customer")
            if isinstance(payload.get("cyera_avg_risk_by_customer"), dict)
            else {}
        )
        theme_trends = payload.get("theme_trends") if isinstance(payload.get("theme_trends"), dict) else {}
        theme_counts_current = (
            payload.get("theme_counts_current")
            if isinstance(payload.get("theme_counts_current"), dict)
            else {}
        )
        outcomes_by_customer = (
            payload.get("outcome_counts_by_customer")
            if isinstance(payload.get("outcome_counts_by_customer"), dict)
            else {}
        )
        customer_deltas = payload.get("customer_deltas") if isinstance(payload.get("customer_deltas"), dict) else {}
        customer_needs_data = (
            payload.get("customer_needs_data")
            if isinstance(payload.get("customer_needs_data"), dict)
            else {}
        )
        for customer, alerts in customer_alerts.items():
            if not isinstance(alerts, list):
                continue
            alert_count = len(alerts)
            avg_score = (sum(float(a.get("score") or 0.0) for a in alerts) / alert_count) if alert_count > 0 else 0.0
            max_score = max(float(a.get("score") or 0.0) for a in alerts) if alert_count > 0 else 0.0
            avg_health_risk = (
                sum(float((a.get("signals") or {}).get("health_risk") or 0.0) for a in alerts) / alert_count
                if alert_count > 0
                else 0.0
            )
            avg_cloud_spend = (
                sum(float((a.get("signals") or {}).get("cloud_spend_pressure") or 0.0) for a in alerts) / alert_count
                if alert_count > 0
                else 0.0
            )
            avg_churn = (
                sum(float((a.get("signals") or {}).get("churn_risk") or 0.0) for a in alerts) / alert_count
                if alert_count > 0
                else 0.0
            )
            avg_renewal = (
                sum(float((a.get("signals") or {}).get("renewal_risk") or 0.0) for a in alerts) / alert_count
                if alert_count > 0
                else 0.0
            )
            heat = account_heat.get(customer) if isinstance(account_heat.get(customer), dict) else {}
            forecast = heat_forecast.get(customer) if isinstance(heat_forecast.get(customer), dict) else {}

            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_customer_summary",
                    tags={"customer": str(customer)},
                    fields={
                        "alerts_count": int(alert_count),
                        "watchlist_count": int(len((payload.get("customer_watchlist") or {}).get(customer) or [])),
                        "needs_data_count": int(len(customer_needs_data.get(customer) or [])),
                        "avg_score": float(avg_score),
                        "max_score": float(max_score),
                        "avg_health_risk": float(avg_health_risk),
                        "avg_cloud_spend_pressure": float(avg_cloud_spend),
                        "avg_churn_risk": float(avg_churn),
                        "avg_renewal_risk": float(avg_renewal),
                        "account_heat_score": float(heat.get("score") or 0.0),
                        "competitor_pressure": float(competitor_pressure.get(customer) or 0.0),
                        "cyera_negative_impact_count": int(cyera_negative_impact.get(customer) or 0),
                        "cyera_avg_risk": float(cyera_avg_risk.get(customer) or 0.0),
                        "forecast_heat_30d": float(forecast.get("forecast_30d") or 0.0),
                        "forecast_heat_90d": float(forecast.get("forecast_90d") or 0.0),
                    },
                    timestamp_epoch=ts,
                )
            )
            delta_entry = customer_deltas.get(customer) if isinstance(customer_deltas.get(customer), dict) else {}
            if delta_entry:
                lines.append(
                    influx_line(
                        measurement=f"{self.measurement_prefix}_customer_delta",
                        tags={"customer": str(customer)},
                        fields={
                            "alerts_delta": float(delta_entry.get("alerts_delta") or 0.0),
                            "watchlist_delta": float(delta_entry.get("watchlist_delta") or 0.0),
                            "needs_data_delta": float(delta_entry.get("needs_data_delta") or 0.0),
                            "heat_delta": float(delta_entry.get("heat_delta") or 0.0),
                            "opportunity_delta": float(delta_entry.get("opportunity_delta") or 0.0),
                            "positive_signals_delta": float(delta_entry.get("positive_signals_delta") or 0.0),
                        },
                        timestamp_epoch=ts,
                    )
                )
            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_customer_bi_snapshot",
                    tags={"customer": str(customer)},
                    fields={
                        "account_heat_score": float(heat.get("score") or 0.0),
                        "opportunity_index": float(opportunity_index.get(customer) or 0.0),
                        "positive_signals": int(positive_signals.get(customer) or 0),
                        "competitor_pressure": float(competitor_pressure.get(customer) or 0.0),
                        "alerts_count": int(alert_count),
                        "cyera_negative_impact_count": int(cyera_negative_impact.get(customer) or 0),
                        "cyera_avg_risk": float(cyera_avg_risk.get(customer) or 0.0),
                        "forecast_heat_30d": float(forecast.get("forecast_30d") or 0.0),
                        "forecast_heat_90d": float(forecast.get("forecast_90d") or 0.0),
                    },
                    timestamp_epoch=ts,
                )
            )

            if alert_count == 0:
                continue
            for alert in alerts:
                signals = alert.get("signals") or {}
                lines.append(
                    influx_line(
                        measurement=f"{self.measurement_prefix}_alert",
                        tags={
                            "customer": str(customer),
                            "event_type": str(alert.get("event_type") or "other"),
                            "source": str(alert.get("source_domain") or "unknown"),
                        },
                        fields={
                            "score": float(alert.get("score") or 0.0),
                            "urgency": int(alert.get("urgency") or 0),
                            "confidence": float(alert.get("confidence") or 0.0),
                            "novelty": float(alert.get("novelty") or 0.0),
                            "health_risk": float(signals.get("health_risk") or 0.0),
                            "cloud_spend_pressure": float(signals.get("cloud_spend_pressure") or 0.0),
                            "churn_risk": float(signals.get("churn_risk") or 0.0),
                            "renewal_risk": float(signals.get("renewal_risk") or 0.0),
                            "opportunity_score": float(alert.get("opportunity_score") or 0.0),
                            "is_positive_signal": bool(alert.get("is_positive_signal")),
                            "cyera_relationship_risk_score": float(alert.get("cyera_relationship_risk_score") or 0.0),
                            "cyera_negative_impact": bool(alert.get("cyera_negative_impact")),
                            "article_title": str(alert.get("article_title") or ""),
                            "url": str(alert.get("url") or ""),
                            "source_name": str(alert.get("source") or ""),
                            "story_id": str(alert.get("story_id") or ""),
                        },
                        timestamp_epoch=ts,
                    )
                )

        for gap in coverage_gaps:
            customer = str((gap or {}).get("customer") or "").strip()
            if not customer:
                continue
            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_coverage_gap",
                    tags={"customer": customer},
                    fields={
                        "days_since_last_alert": int((gap or {}).get("days_since_last_alert") or 0),
                    },
                    timestamp_epoch=ts,
                )
            )

        trend_to_int = {"down": -1, "flat": 0, "up": 1}
        for customer, trend_map in theme_trends.items():
            if not isinstance(trend_map, dict):
                continue
            counts = theme_counts_current.get(customer) if isinstance(theme_counts_current.get(customer), dict) else {}
            for theme, direction in trend_map.items():
                direction_value = trend_to_int.get(str(direction), 0)
                lines.append(
                    influx_line(
                        measurement=f"{self.measurement_prefix}_theme_trend",
                        tags={"customer": str(customer), "theme": str(theme)},
                        fields={
                            "direction": int(direction_value),
                            "current_count": int((counts.get(theme) if isinstance(counts, dict) else 0) or 0),
                        },
                        timestamp_epoch=ts,
                    )
                )

        feedback_breakdown = payload.get("feedback_breakdown") or {}
        for dimension, entries in feedback_breakdown.items():
            if not isinstance(entries, dict):
                continue
            for key, counts in entries.items():
                if not isinstance(counts, dict):
                    continue
                lines.append(
                    influx_line(
                        measurement=f"{self.measurement_prefix}_feedback_summary",
                        tags={"dimension": str(dimension), "key": str(key)},
                        fields={
                            "positive": int(counts.get("positive") or 0),
                            "negative": int(counts.get("negative") or 0),
                            "neutral": int(counts.get("neutral") or 0),
                        },
                        timestamp_epoch=ts,
                    )
                )

        outcome_counts = payload.get("outcome_counts") or {}
        for outcome, count in sorted(outcome_counts.items()):
            lines.append(
                influx_line(
                    measurement=f"{self.measurement_prefix}_outcome_summary",
                    tags={"outcome": str(outcome)},
                    fields={"count": int(count or 0)},
                    timestamp_epoch=ts,
                )
            )
        for customer, outcomes in outcomes_by_customer.items():
            if not isinstance(outcomes, dict):
                continue
            for outcome, count in sorted(outcomes.items()):
                lines.append(
                    influx_line(
                        measurement=f"{self.measurement_prefix}_customer_outcome_summary",
                        tags={"customer": str(customer), "outcome": str(outcome)},
                        fields={"count": int(count or 0)},
                        timestamp_epoch=ts,
                    )
                )

        if not lines:
            return

        write_url = f"{self.url}/api/v2/write"
        params = {"org": self.org, "bucket": self.bucket, "precision": "s"}
        headers = {
            "Authorization": f"Token {self.token}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        body = "\n".join(lines) + "\n"
        response = requests.post(write_url, params=params, headers=headers, data=body.encode("utf-8"), timeout=self.timeout)
        if response.status_code >= 400:
            raise PipelineError(f"InfluxDB write failed ({response.status_code}): {response.text[:300]}")


def run_pipeline(config: Dict[str, Any], max_items_override: int | None, dry_run: bool) -> Dict[str, Any]:
    run_started_monotonic = time.perf_counter()
    event_types = [str(x).strip() for x in (config.get("taxonomy", {}).get("event_types") or DEFAULT_EVENT_TYPES) if str(x).strip()]
    if "other" not in event_types:
        event_types.append("other")

    fresh_client = FreshRSSClient(config.get("fresh_rss") or {})
    ai_extractor = AIExtractor(config.get("ai") or {}, event_types)
    enricher = ArticleEnricher(config.get("enrichment") or {})
    influx_writer = InfluxDBWriter(config.get("influxdb") or {})
    processing_cfg = config.get("processing") if isinstance(config.get("processing"), dict) else {}
    source_quality_cfg = config.get("source_quality") if isinstance(config.get("source_quality"), dict) else {}
    action_hooks_cfg = config.get("action_hooks") if isinstance(config.get("action_hooks"), dict) else {}
    cyera_eval_cfg = config.get("cyera_eval") if isinstance(config.get("cyera_eval"), dict) else {}
    feedback_cfg = config.get("feedback") or {}
    dynamic_threshold_cfg = config.get("dynamic_thresholds") or {}
    routing_cfg = config.get("alert_routing") or {}
    output_cfg = config.get("output") or {}
    customer_tiers_cfg = config.get("customer_tiers") if isinstance(config.get("customer_tiers"), dict) else {}
    playbooks_cfg = config.get("playbooks") if isinstance(config.get("playbooks"), dict) else {}
    no_signal_days = max(1, int(routing_cfg.get("no_signal_days") or 7))
    min_confidence_for_alert = clamp(float(routing_cfg.get("min_confidence_for_alert") or 0.5), 0.0, 1.0)
    watchlist_score_floor = clamp(float(routing_cfg.get("watchlist_score_floor") or 30.0), 0.0, 100.0)
    positive_signal_opportunity_floor = clamp(float(routing_cfg.get("positive_signal_opportunity_floor") or 35.0), 0.0, 100.0)
    needs_data_cfg = routing_cfg.get("needs_data") if isinstance(routing_cfg.get("needs_data"), dict) else {}
    cooldown_hours = max(1, int(routing_cfg.get("cooldown_hours") or 48))
    cooldown_seconds = cooldown_hours * 3600

    state_cfg = config.get("state") or {}
    state_path = Path(str(state_cfg.get("file") or "./state/bi_pipeline_state.json"))
    keep_seen = max(1, to_int(state_cfg.get("keep_seen_ids"), 15000))
    state = load_json(
        state_path,
        {
            "seen_ids": [],
            "last_run": "",
            "alert_history": {},
            "customer_last_alert_at": {},
            "story_state": {},
            "dynamic_thresholds": {"history": {}},
            "feedback": {"cursor": 0, "stats": {}},
            "theme_history": {},
            "account_memory": {},
            "customer_snapshot": {},
            "customer_story_snapshot": {},
            "source_reliability": {"sources": {}, "updated_at": ""},
            "last_fetch_epoch": 0,
            "coverage_gap_customers": [],
        },
    )
    previous_gap_customers: Set[str] = {
        str(x) for x in (state.get("coverage_gap_customers") or []) if str(x)
    }
    state, feedback_update = load_feedback_updates(state, feedback_cfg)
    existing_seen_ids = [str(x).strip() for x in (state.get("seen_ids") or []) if str(x).strip()]
    seen_ids = set(existing_seen_ids)
    now_epoch = to_epoch(utc_now())
    alert_history = cleanup_alert_history(
        alert_history=state.get("alert_history") or {},
        now_epoch=now_epoch,
        max_age_seconds=max(30 * 86400, cooldown_seconds * 6),
    )
    story_state = state.get("story_state") if isinstance(state.get("story_state"), dict) else {}
    dynamic_state = state.get("dynamic_thresholds") if isinstance(state.get("dynamic_thresholds"), dict) else {}
    dynamic_history_state = dynamic_state.get("history") if isinstance(dynamic_state.get("history"), dict) else {}
    customer_last_alert_at = state.get("customer_last_alert_at") if isinstance(state.get("customer_last_alert_at"), dict) else {}
    theme_history_state = state.get("theme_history") if isinstance(state.get("theme_history"), dict) else {}
    account_memory_state = state.get("account_memory") if isinstance(state.get("account_memory"), dict) else {}
    customer_snapshot_state = state.get("customer_snapshot") if isinstance(state.get("customer_snapshot"), dict) else {}
    customer_story_snapshot_state = (
        state.get("customer_story_snapshot")
        if isinstance(state.get("customer_story_snapshot"), dict)
        else {}
    )
    source_reliability_state = (
        state.get("source_reliability")
        if isinstance(state.get("source_reliability"), dict)
        else {"sources": {}, "updated_at": ""}
    )
    last_fetch_epoch = max(0, to_int(state.get("last_fetch_epoch"), 0))
    incremental_cfg = (config.get("fresh_rss") or {}).get("incremental_fetch")
    incremental_cfg = incremental_cfg if isinstance(incremental_cfg, dict) else {}
    incremental_enabled = bool(incremental_cfg.get("enabled", True))
    incremental_grace_seconds = max(0, to_int(incremental_cfg.get("grace_seconds"), 900))
    since_epoch_override = max(0, last_fetch_epoch - incremental_grace_seconds) if incremental_enabled and last_fetch_epoch > 0 else None

    fetched = fresh_client.fetch_articles(
        max_items_override=max_items_override,
        since_epoch_override=since_epoch_override,
    )
    new_articles = [a for a in fetched if a.get("id") and a.get("id") not in seen_ids]

    customers = config.get("customers") or []
    if not isinstance(customers, list) or not customers:
        raise PipelineError("config.customers must contain at least one customer profile")

    scoring_defaults = (config.get("scoring") or {}).get("default_weights") or {}
    configured_global_min = to_float((config.get("scoring") or {}).get("min_alert_score"), 45.0)
    min_score_global = max(0.0, min(100.0, configured_global_min))
    if min_score_global != configured_global_min:
        print(
            f"[warn] scoring.min_alert_score={configured_global_min} out of bounds; clamped to {min_score_global}",
            file=sys.stderr,
        )

    prepared_customers: List[Dict[str, Any]] = []
    customer_names: List[str] = []
    for customer in customers:
        if not isinstance(customer, dict):
            continue
        customer_name = str(customer.get("name") or customer.get("id") or "customer").strip()
        if not customer_name:
            customer_name = "customer"
        configured_min = to_float(customer.get("min_alert_score"), min_score_global)
        customer_min = max(0.0, min(100.0, configured_min))
        if customer_min != configured_min:
            print(
                f"[warn] customer={customer_name} min_alert_score={configured_min} out of bounds; clamped to {customer_min}",
                file=sys.stderr,
            )
        tier = infer_customer_tier(customer, customer_tiers_cfg)
        customer_min = clamp(customer_min + float(tier.get("threshold_adjustment") or 0.0), 0.0, 100.0)
        effective_threshold = dynamic_threshold_for_customer(
            customer_name=customer_name,
            base_threshold=customer_min,
            state=state,
            cfg=dynamic_threshold_cfg,
        )
        normalized_customer = dict(customer)
        normalized_customer["_base_min_alert_score"] = customer_min
        normalized_customer["_effective_min_alert_score"] = effective_threshold
        normalized_customer["_display_name"] = customer_name
        normalized_customer["_tier"] = tier
        prepared_customers.append(normalized_customer)
        if customer_name not in customer_names:
            customer_names.append(customer_name)

    if not prepared_customers:
        raise PipelineError("config.customers must contain at least one valid customer mapping")

    customer_alerts: Dict[str, List[Dict[str, Any]]] = {name: [] for name in customer_names}
    customer_watchlist: Dict[str, List[Dict[str, Any]]] = {name: [] for name in customer_names}
    customer_needs_data: Dict[str, List[Dict[str, Any]]] = {name: [] for name in customer_names}
    customer_profiles: Dict[str, Dict[str, Any]] = {str(c.get("_display_name")): c for c in prepared_customers}
    analyzed: List[Dict[str, Any]] = []
    noise_summary: Counter[str] = Counter()
    score_history_updates: Dict[str, List[float]] = defaultdict(list)
    theme_counts_by_customer: Dict[str, Counter[str]] = defaultdict(Counter)
    competitor_pressure_by_customer: Dict[str, float] = defaultdict(float)
    positive_signals_by_customer: Counter[str] = Counter()
    opportunity_index_by_customer: Dict[str, float] = defaultdict(float)
    story_clusters: set[str] = set()
    suppressed_cooldown_alerts = 0
    low_confidence_blocked_alerts = 0
    cyera_negative_impact_by_customer: Counter[str] = Counter()
    cyera_avg_risk_by_customer: Dict[str, List[float]] = defaultdict(list)
    parallel_workers = max(1, to_int(processing_cfg.get("parallel_workers"), 1))
    article_event_results: List[Dict[str, Any]] = []
    parallel_ai_stats: Counter[str] = Counter()
    parallel_enrichment_stats: Counter[str] = Counter()
    if parallel_workers > 1 and len(new_articles) > 1:
        worker_count = min(parallel_workers, len(new_articles))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    process_article_for_event,
                    article=article,
                    enrichment_cfg=config.get("enrichment") or {},
                    ai_cfg=config.get("ai") or {},
                    event_types=event_types,
                )
                for article in new_articles
            ]
            for future in futures:
                result = future.result()
                article_event_results.append(result)
                for key, value in (result.get("ai_stats") or {}).items():
                    if isinstance(value, bool):
                        continue
                    parallel_ai_stats[key] += to_int(value, 0)
                for key, value in (result.get("enrichment_stats") or {}).items():
                    parallel_enrichment_stats[key] += to_int(value, 0)
    else:
        for article in new_articles:
            enriched_article = enricher.enrich_article(article)
            event = ai_extractor.extract(enriched_article)
            article_event_results.append(
                {
                    "article": enriched_article,
                    "event": event,
                }
            )

    for processed in article_event_results:
        enriched_article = processed.get("article") if isinstance(processed.get("article"), dict) else {}
        event = processed.get("event") if isinstance(processed.get("event"), dict) else {}
        story_signature = stable_story_signature(enriched_article, event)
        story_id = story_id_from_signature(story_signature)
        story_meta = story_state.get(story_signature) if isinstance(story_state.get(story_signature), dict) else {}
        story_seen_count = to_int(story_meta.get("count"), 0)
        novelty = clamp(1.0 / (1.0 + max(0, story_seen_count)), 0.0, 1.0)
        story_state[story_signature] = {
            "count": story_seen_count + 1,
            "last_seen": now_epoch,
            "story_id": story_id,
        }
        story_clusters.add(story_id)

        article_record = {
            "article": enriched_article,
            "event": event,
            "story_id": story_id,
            "story_signature": story_signature,
            "novelty": novelty,
            "customer_scores": [],
        }

        for customer in prepared_customers:
            customer_name = str(customer.get("_display_name") or "customer").strip()
            customer_min = float(customer.get("_effective_min_alert_score") or min_score_global)
            memory_entry = account_memory_state.get(customer_name) if isinstance(account_memory_state.get(customer_name), dict) else {}
            memory_signal = memory_signal_adjustment(memory_entry, enriched_article, event)
            source_domain = str(enriched_article.get("source_domain") or "unknown")
            feedback_multiplier, feedback_components = feedback_quality_adjustment(
                customer_name=customer_name,
                source_domain=source_domain,
                event_type=str(event.get("event_type") or "other"),
                feedback_state=state,
                feedback_cfg=feedback_cfg,
            )
            source_multiplier, source_quality_components = source_quality_multiplier(
                source_domain=source_domain,
                source_reliability_state=source_reliability_state,
                cfg=source_quality_cfg,
            )
            score, details = score_for_customer(
                enriched_article,
                event,
                customer,
                scoring_defaults,
                extra_context={
                    "novelty": novelty,
                    "feedback_multiplier": feedback_multiplier * source_multiplier,
                    "feedback_components": {**feedback_components, **source_quality_components},
                    "account_multiplier": (customer.get("_tier") or {}).get("score_multiplier", 1.0),
                    "memory_multiplier": memory_signal.get("multiplier", 1.0),
                    "memory_topic_hits": memory_signal.get("topic_hits", 0),
                    "memory_unresolved_hits": memory_signal.get("unresolved_hits", 0),
                },
            )
            score_history_updates[customer_name].append(score)

            scored = {
                "customer": customer_name,
                "score": score,
                "details": details,
            }
            article_record["customer_scores"].append(scored)

            item = {
                "customer": customer_name,
                "article_id": enriched_article.get("id"),
                "story_id": story_id,
                "story_signature": story_signature,
                "article_title": enriched_article.get("title"),
                "url": enriched_article.get("url"),
                "source": enriched_article.get("source"),
                "source_domain": enriched_article.get("source_domain"),
                "published_at": enriched_article.get("published_at"),
                "event_type": event.get("event_type"),
                "event_subtype": event.get("event_subtype"),
                "time_horizon": event.get("time_horizon"),
                "summary": event.get("summary"),
                "why_it_matters": event.get("why_it_matters"),
                "customer_relevance_hypothesis": event.get("customer_relevance_hypothesis"),
                "evidence_snippets": event.get("evidence_snippets"),
                "urgency": event.get("urgency"),
                "confidence": event.get("confidence"),
                "signals": event.get("signals"),
                "impact_vectors": event.get("impact_vectors"),
                "recommended_actions": event.get("recommended_actions"),
                "entities": event.get("entities"),
                "cyera_impact_summary": event.get("cyera_impact_summary"),
                "cyera_relationship_risk_score": event.get("cyera_relationship_risk_score"),
                "cyera_relationship_risk_label": event.get("cyera_relationship_risk_label"),
                "score": score,
                "score_details": details,
                "novelty": novelty,
                "customer_tier": (customer.get("_tier") or {}).get("name", "standard"),
            }
            cyera_eval = evaluate_cyera_customer_impact(item, customer, cyera_eval_cfg)
            item["cyera_relationship_risk_score"] = cyera_eval.get("risk_score")
            item["cyera_relationship_risk_label"] = cyera_eval.get("risk_label")
            item["cyera_negative_impact"] = cyera_eval.get("negative_impact")
            item["cyera_impact_summary"] = cyera_eval.get("summary")
            cyera_avg_risk_by_customer[customer_name].append(float(item.get("cyera_relationship_risk_score") or 0.0))
            if bool(item.get("cyera_negative_impact")):
                cyera_negative_impact_by_customer[customer_name] += 1
            item["opportunity_score"] = calc_opportunity_score(event, score, details)
            item["is_positive_signal"] = bool(item["opportunity_score"] >= positive_signal_opportunity_floor)
            item["playbooks"] = resolve_playbooks(customer, str(event.get("event_type") or "other"), playbooks_cfg)
            if item["playbooks"]:
                combined_actions = [*normalize_list_strings(item.get("recommended_actions")), *item["playbooks"]]
                item["recommended_actions"] = combined_actions[:4]
            competitor_pressure_by_customer[customer_name] += float(details.get("competitor_pressure_signal") or 0.0)
            theme_counts_by_customer[customer_name][classify_theme(str(event.get("event_type") or "other"))] += 1
            if item["is_positive_signal"]:
                positive_signals_by_customer[customer_name] += 1
                opportunity_index_by_customer[customer_name] += float(item["opportunity_score"] or 0.0)

            needs_data_reason = detect_needs_data_reason(item, needs_data_cfg)
            if needs_data_reason:
                needs_item = dict(item)
                needs_item["routing_reason"] = "needs_data"
                needs_item["needs_data_reason"] = needs_data_reason
                customer_needs_data[customer_name].append(needs_item)
                noise_summary["needs_data"] += 1
                continue

            if score < customer_min:
                if score >= watchlist_score_floor:
                    watch_item = dict(item)
                    watch_item["routing_reason"] = "below_threshold"
                    customer_watchlist[customer_name].append(watch_item)
                    noise_summary["below_threshold_watchlist"] += 1
                continue

            confidence = clamp(float(event.get("confidence") or 0.0), 0.0, 1.0)
            if confidence < min_confidence_for_alert:
                low_confidence_blocked_alerts += 1
                noise_summary["low_confidence_blocked"] += 1
                if score >= watchlist_score_floor:
                    watch_item = dict(item)
                    watch_item["routing_reason"] = "low_confidence"
                    customer_watchlist[customer_name].append(watch_item)
                continue

            cooldown_key = f"{customer_name}|{story_id}|{event.get('event_type') or 'other'}"
            last_alert_epoch = to_int(alert_history.get(cooldown_key), 0)
            if last_alert_epoch > 0 and (now_epoch - last_alert_epoch) < cooldown_seconds:
                suppressed_cooldown_alerts += 1
                noise_summary["cooldown_suppressed"] += 1
                if score >= watchlist_score_floor:
                    watch_item = dict(item)
                    watch_item["routing_reason"] = "cooldown_suppressed"
                    customer_watchlist[customer_name].append(watch_item)
                continue

            alert_history[cooldown_key] = now_epoch
            customer_alerts[customer_name].append(item)

        analyzed.append(article_record)

    run_at = utc_now().isoformat()
    run_ts = parse_iso_date(run_at) or utc_now()
    for customer_name, alerts in customer_alerts.items():
        if alerts:
            customer_last_alert_at[customer_name] = run_ts.isoformat()
    total_alerts = sum(len(v) for v in customer_alerts.values())
    watchlist_total = sum(len(v) for v in customer_watchlist.values())
    needs_data_total = sum(len(v) for v in customer_needs_data.values())
    event_type_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for alerts in customer_alerts.values():
        for alert in alerts:
            event_type_counts[str(alert.get("event_type") or "other")] += 1
            source_counts[str(alert.get("source_domain") or "unknown")] += 1

    coverage_gaps: List[Dict[str, Any]] = []
    for customer_name in customer_names:
        profile = customer_profiles.get(customer_name) or {}
        last_alert_dt = parse_iso_date(customer_last_alert_at.get(customer_name))
        if last_alert_dt is None:
            days_since_last = 9999
        else:
            days_since_last = max(0, int((run_ts - last_alert_dt).total_seconds() / 86400))
        if days_since_last >= no_signal_days:
            suggestions: List[str] = []
            if len(normalize_list_strings(profile.get("feed_urls"))) < 2:
                suggestions.append("Add more account-specific feeds (press, status page, investor/newsroom).")
            if len(normalize_list_strings(profile.get("keywords"))) < 5:
                suggestions.append("Expand customer keywords with product names, initiatives, and business units.")
            if len(normalize_list_strings(profile.get("context_terms"))) < 3:
                suggestions.append("Add context_terms for current quarter priorities and success plan themes.")
            business = profile.get("business_context") if isinstance(profile.get("business_context"), dict) else {}
            if len(normalize_list_strings(business.get("stack_confirmed"))) < 3:
                suggestions.append("Populate business_context.stack_confirmed with verified vendors in production.")
            coverage_gaps.append(
                {
                    "customer": customer_name,
                    "days_since_last_alert": days_since_last,
                    "suggestions": suggestions[:4],
                }
            )

    run_duration_seconds = max(0.0, time.perf_counter() - run_started_monotonic)
    if parallel_workers > 1 and len(new_articles) > 1:
        ai_calls = int(parallel_ai_stats.get("calls_total") or 0)
        ai_fallback = int(parallel_ai_stats.get("fallback_total") or 0)
        ai_stats = {
            "enabled": bool((config.get("ai") or {}).get("enabled", True)),
            "calls_total": ai_calls,
            "fallback_total": ai_fallback,
            "fallback_timeout_total": int(parallel_ai_stats.get("fallback_timeout_total") or 0),
            "fallback_request_total": int(parallel_ai_stats.get("fallback_request_total") or 0),
            "fallback_response_total": int(parallel_ai_stats.get("fallback_response_total") or 0),
            "fallback_other_total": int(parallel_ai_stats.get("fallback_other_total") or 0),
            "heuristic_only_total": int(parallel_ai_stats.get("heuristic_only_total") or 0),
            "fallback_rate": (float(ai_fallback) / float(ai_calls)) if ai_calls > 0 else 0.0,
        }
        enrichment_stats = {
            "attempted": int(parallel_enrichment_stats.get("attempted") or 0),
            "succeeded": int(parallel_enrichment_stats.get("succeeded") or 0),
            "failed": int(parallel_enrichment_stats.get("failed") or 0),
            "skipped": int(parallel_enrichment_stats.get("skipped") or 0),
        }
    else:
        ai_stats = ai_extractor.get_stats()
        enrichment_stats = enricher.get_stats()
    alerted_customer_count = sum(1 for alerts in customer_alerts.values() if alerts)
    feedback_stats_state = ((state.get("feedback") or {}).get("stats") or {}).get("run") or {}
    feedback_positive = int(feedback_stats_state.get("positive") or 0)
    feedback_negative = int(feedback_stats_state.get("negative") or 0)
    feedback_processed = int(feedback_update.get("processed") or 0)
    feedback_relevant_rate = (
        float(feedback_positive) / float(feedback_positive + feedback_negative)
        if (feedback_positive + feedback_negative) > 0
        else 0.0
    )
    feedback_stats = {
        "processed": feedback_processed,
        "positive": feedback_positive,
        "negative": feedback_negative,
        "neutral": int(feedback_stats_state.get("neutral") or 0),
        "relevant_rate": float(feedback_relevant_rate),
    }
    feedback_breakdown = summarize_feedback_breakdown((state.get("feedback") or {}).get("stats") or {})
    outcome_counts = ((state.get("feedback") or {}).get("stats") or {}).get("outcomes") or {}
    outcome_counts_by_customer = summarize_customer_outcomes((state.get("feedback") or {}).get("stats") or {})
    account_heat: Dict[str, Dict[str, Any]] = {}
    theme_trends: Dict[str, Dict[str, str]] = {}
    theme_counts_current: Dict[str, Dict[str, int]] = {}
    for customer_name in customer_names:
        profile = customer_profiles.get(customer_name) or {}
        competitor_pressure = float(competitor_pressure_by_customer.get(customer_name) or 0.0)
        account_heat[customer_name] = calc_account_heat_score(
            customer=profile,
            alerts=customer_alerts.get(customer_name) or [],
            watchlist=customer_watchlist.get(customer_name) or [],
            competitor_pressure=competitor_pressure,
        )
        current_theme_counts = dict(theme_counts_by_customer.get(customer_name) or {})
        theme_counts_current[customer_name] = {k: int(v) for k, v in current_theme_counts.items()}
        history_entries = theme_history_state.get(customer_name) if isinstance(theme_history_state.get(customer_name), list) else []
        prev_window = history_entries[-6:] if history_entries else []
        theme_keys = set(current_theme_counts.keys())
        for entry in prev_window:
            if isinstance(entry, dict):
                for key in (entry.get("themes") or {}).keys():
                    theme_keys.add(str(key))
        customer_trends: Dict[str, str] = {}
        for theme in sorted(theme_keys):
            current_value = float(current_theme_counts.get(theme) or 0.0)
            prev_values = []
            for entry in prev_window:
                if not isinstance(entry, dict):
                    continue
                prev_values.append(float(((entry.get("themes") or {}).get(theme)) or 0.0))
            prev_avg = (sum(prev_values) / len(prev_values)) if prev_values else 0.0
            if current_value >= prev_avg + 1.0:
                customer_trends[theme] = "up"
            elif current_value <= max(0.0, prev_avg - 1.0):
                customer_trends[theme] = "down"
            else:
                customer_trends[theme] = "flat"
        theme_trends[customer_name] = customer_trends

    for customer_name in customer_names:
        positives = int(positive_signals_by_customer.get(customer_name) or 0)
        total = (
            len(customer_alerts.get(customer_name) or [])
            + len(customer_watchlist.get(customer_name) or [])
            + len(customer_needs_data.get(customer_name) or [])
        )
        avg_opp = (
            float(opportunity_index_by_customer.get(customer_name) or 0.0) / float(max(1, positives))
            if positives > 0
            else 0.0
        )
        mix_bonus = (float(positives) / float(max(1, total))) * 25.0 if total > 0 else 0.0
        opportunity_index_by_customer[customer_name] = round(clamp(avg_opp + mix_bonus, 0.0, 100.0), 2)

    current_customer_metrics: Dict[str, Dict[str, float]] = {}
    for customer_name in customer_names:
        current_customer_metrics[customer_name] = {
            "alerts": float(len(customer_alerts.get(customer_name) or [])),
            "watchlist": float(len(customer_watchlist.get(customer_name) or [])),
            "needs_data": float(len(customer_needs_data.get(customer_name) or [])),
            "heat": float((account_heat.get(customer_name) or {}).get("score") or 0.0),
            "opportunity": float(opportunity_index_by_customer.get(customer_name) or 0.0),
            "positive_signals": float(positive_signals_by_customer.get(customer_name) or 0.0),
        }
    customer_deltas = compute_customer_deltas(
        customer_names=customer_names,
        current_metrics=current_customer_metrics,
        previous_snapshot=customer_snapshot_state,
    )
    customer_story_snapshot = build_customer_story_snapshot(
        customer_alerts=customer_alerts,
        customer_watchlist=customer_watchlist,
        customer_needs_data=customer_needs_data,
        max_items_per_customer=max(20, to_int(state_cfg.get("story_snapshot_max_items"), 80)),
    )
    customer_story_changes = compute_story_changes(
        customer_names=customer_names,
        current_snapshot=customer_story_snapshot,
        previous_snapshot=customer_story_snapshot_state,
    )
    heat_forecast_by_customer: Dict[str, Dict[str, float]] = {}
    for customer_name in customer_names:
        profile = customer_profiles.get(customer_name) or {}
        heat_forecast_by_customer[customer_name] = forecast_customer_heat(
            customer=profile,
            current_heat=account_heat.get(customer_name) or {},
            theme_history_entries=theme_history_state.get(customer_name) if isinstance(theme_history_state.get(customer_name), list) else [],
            opportunity_index=float(opportunity_index_by_customer.get(customer_name) or 0.0),
        )

    payload = {
        "run_at": run_at,
        "run_duration_seconds": float(run_duration_seconds),
        "fetched_articles": len(fetched),
        "new_articles": len(new_articles),
        "fetch_since_epoch": int(since_epoch_override or 0),
        "parallel_workers_used": int(parallel_workers),
        "total_alerts": total_alerts,
        "watchlist_total": watchlist_total,
        "needs_data_total": needs_data_total,
        "suppressed_cooldown_alerts": int(suppressed_cooldown_alerts),
        "low_confidence_blocked_alerts": int(low_confidence_blocked_alerts),
        "story_clusters": int(len(story_clusters)),
        "configured_customer_count": int(len(prepared_customers)),
        "alerted_customer_count": int(alerted_customer_count),
        "ai_stats": ai_stats,
        "enrichment_stats": enrichment_stats,
        "feedback_stats": feedback_stats,
        "feedback_breakdown": feedback_breakdown,
        "outcome_counts": outcome_counts,
        "outcome_counts_by_customer": outcome_counts_by_customer,
        "noise_summary": dict(noise_summary),
        "coverage_gaps": coverage_gaps,
        "account_heat": account_heat,
        "competitor_pressure": {k: round(float(v), 2) for k, v in competitor_pressure_by_customer.items()},
        "cyera_negative_impact_by_customer": {k: int(v) for k, v in cyera_negative_impact_by_customer.items()},
        "cyera_avg_risk_by_customer": {
            k: round(sum(values) / len(values), 3) for k, values in cyera_avg_risk_by_customer.items() if values
        },
        "positive_signals_by_customer": {k: int(v) for k, v in positive_signals_by_customer.items()},
        "opportunity_index_by_customer": {k: round(float(v), 2) for k, v in opportunity_index_by_customer.items()},
        "theme_trends": theme_trends,
        "theme_counts_current": theme_counts_current,
        "customer_deltas": customer_deltas,
        "customer_story_changes": customer_story_changes,
        "heat_forecast_by_customer": heat_forecast_by_customer,
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "source_counts": dict(sorted(source_counts.items(), key=lambda item: item[1], reverse=True)),
        "customer_alerts": customer_alerts,
        "customer_watchlist": customer_watchlist,
        "customer_needs_data": customer_needs_data,
        "analyzed": analyzed,
    }

    output_dir = Path(str(output_cfg.get("directory") or "./output"))
    run_id = dt.datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_dir / run_id
    top_n = max(1, to_int(output_cfg.get("top_n_per_customer"), 5))
    written = write_outputs(run_dir, payload, top_n=top_n, customer_names=customer_names)

    if not dry_run:
        post_webhook(output_cfg, payload, previous_gap_customers=previous_gap_customers)
        run_action_hooks(action_hooks_cfg, payload)
        retention_days = max(0, to_int(output_cfg.get("retention_days"), 0))
        max_run_directories = max(0, to_int(output_cfg.get("max_run_directories"), 0))
        prune_stats = prune_output_runs(
            output_dir=output_dir,
            retention_days=retention_days,
            max_run_directories=max_run_directories,
        )
        if prune_stats["removed_by_age"] or prune_stats["removed_by_count"]:
            print(
                f"[ok] output retention pruned removed_by_age={prune_stats['removed_by_age']} "
                f"removed_by_count={prune_stats['removed_by_count']}",
                file=sys.stderr,
            )
        payload["output_retention"] = prune_stats

        try:
            influx_writer.write(payload)
        except PipelineError as exc:
            print(f"[warn] InfluxDB write failed, continuing without metrics: {exc}", file=sys.stderr)

        new_ids = [a.get("id") for a in new_articles if a.get("id")]
        all_seen = merge_seen_ids(existing_seen_ids, new_ids, keep_seen=keep_seen)
        dynamic_history_size = max(50, int(dynamic_threshold_cfg.get("history_size") or 400))
        for customer_name, new_scores in score_history_updates.items():
            history = [to_float(v, 0.0) for v in (dynamic_history_state.get(customer_name) or [])]
            history.extend(float(v) for v in new_scores)
            if len(history) > dynamic_history_size:
                history = history[-dynamic_history_size:]
            dynamic_history_state[customer_name] = history

        story_state_max_items = max(500, int(state_cfg.get("story_state_max_items") or 10000))
        trimmed_story_state = trim_story_state(story_state, max_items=story_state_max_items)
        memory_max_items = max(3, int(state_cfg.get("account_memory_max_items") or 10))
        updated_account_memory: Dict[str, Dict[str, Any]] = {}
        for customer_name in customer_names:
            alerts_sorted = sorted(customer_alerts.get(customer_name) or [], key=lambda x: x.get("score", 0), reverse=True)
            watch_sorted = sorted(customer_watchlist.get(customer_name) or [], key=lambda x: x.get("score", 0), reverse=True)
            needs_sorted = sorted(customer_needs_data.get(customer_name) or [], key=lambda x: x.get("score", 0), reverse=True)
            memory_source = [*alerts_sorted, *watch_sorted, *needs_sorted][:memory_max_items]
            focus_topics: List[str] = []
            recent_event_types: List[str] = []
            unresolved_actions: List[str] = []
            recent_items: List[Dict[str, Any]] = []
            for item in memory_source:
                event_type = str(item.get("event_type") or "other")
                recent_event_types.append(event_type)
                recent_items.append(
                    {
                        "story_id": str(item.get("story_id") or ""),
                        "event_type": event_type,
                        "score": float(item.get("score") or 0.0),
                        "routing": str(item.get("routing_reason") or "alert"),
                        "why": str(item.get("why_it_matters") or "")[:240],
                    }
                )
                focus_topics.extend(normalize_list_strings((item.get("entities") or {}).get("companies")))
                focus_topics.extend(normalize_list_strings((item.get("entities") or {}).get("products")))
                unresolved_actions.extend(normalize_list_strings(item.get("recommended_actions")))
            dedup_topics = []
            seen_topics: set[str] = set()
            for topic in focus_topics:
                key = topic.lower()
                if key in seen_topics:
                    continue
                dedup_topics.append(topic)
                seen_topics.add(key)
                if len(dedup_topics) >= 10:
                    break
            dedup_actions = []
            seen_actions: set[str] = set()
            for action in unresolved_actions:
                key = action.lower()
                if key in seen_actions:
                    continue
                dedup_actions.append(action)
                seen_actions.add(key)
                if len(dedup_actions) >= 8:
                    break
            updated_account_memory[customer_name] = {
                "last_updated": run_at,
                "focus_topics": dedup_topics,
                "unresolved_actions": dedup_actions,
                "recent_event_types": recent_event_types[:memory_max_items],
                "recent_items": recent_items,
            }

        theme_history_size = max(7, int(state_cfg.get("theme_history_size") or 60))
        for customer_name in customer_names:
            entries = theme_history_state.get(customer_name) if isinstance(theme_history_state.get(customer_name), list) else []
            entries.append(
                {
                    "run_at": run_at,
                    "themes": dict(theme_counts_by_customer.get(customer_name) or {}),
                }
            )
            if len(entries) > theme_history_size:
                entries = entries[-theme_history_size:]
            theme_history_state[customer_name] = entries
        source_reliability_max_sources = max(50, to_int(source_quality_cfg.get("max_sources"), 400))
        updated_source_reliability = update_source_reliability_state(
            source_reliability_state=source_reliability_state,
            customer_alerts=customer_alerts,
            customer_watchlist=customer_watchlist,
            customer_needs_data=customer_needs_data,
            max_sources=source_reliability_max_sources,
        )
        latest_published_epoch = max((to_int(a.get("published_epoch"), 0) for a in fetched), default=0)
        persisted_fetch_epoch = max(latest_published_epoch, now_epoch if fetched else last_fetch_epoch)
        save_json(
            state_path,
            {
                "seen_ids": all_seen,
                "last_run": run_at,
                "last_fetch_epoch": int(persisted_fetch_epoch),
                "alert_history": cleanup_alert_history(
                    alert_history=alert_history,
                    now_epoch=now_epoch,
                    max_age_seconds=max(30 * 86400, cooldown_seconds * 6),
                ),
                "customer_last_alert_at": customer_last_alert_at,
                "story_state": trimmed_story_state,
                "dynamic_thresholds": {"history": dynamic_history_state},
                "feedback": state.get("feedback") or {"cursor": 0, "stats": {}},
                "theme_history": theme_history_state,
                "account_memory": updated_account_memory,
                "customer_snapshot": current_customer_metrics,
                "customer_story_snapshot": customer_story_snapshot,
                "source_reliability": updated_source_reliability,
                "coverage_gap_customers": sorted(
                    {
                        str(g.get("customer") or "")
                        for g in (payload.get("coverage_gaps") or [])
                        if g.get("customer")
                    }
                ),
            },
        )

    payload["outputs"] = {k: str(v) for k, v in written.items()}
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FreshRSS customer intelligence pipeline")
    parser.add_argument("--config", default="./config.yaml", help="Path to pipeline config YAML")
    parser.add_argument("--max-items", type=int, default=None, help="Override max item fetch count")
    parser.add_argument("--dry-run", action="store_true", help="Do not write state or send webhook")
    parser.add_argument("--print-json", action="store_true", help="Print summary JSON to stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[error] Config not found: {config_path}", file=sys.stderr)
        return 2

    try:
        config = load_yaml(config_path)
        result = run_pipeline(config, max_items_override=args.max_items, dry_run=args.dry_run)
    except PipelineError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"[error] Network/API failure: {exc}", file=sys.stderr)
        return 1

    print(
        "[ok] run_at={run_at} fetched={fetched} new={new} alerts={alerts}".format(
            run_at=result.get("run_at"),
            fetched=result.get("fetched_articles", 0),
            new=result.get("new_articles", 0),
            alerts=result.get("total_alerts", 0),
        )
    )
    for name, path in (result.get("outputs") or {}).items():
        print(f"[ok] {name}: {path}")

    if args.print_json:
        print(json.dumps({
            "run_at": result.get("run_at"),
            "total_alerts": result.get("total_alerts"),
            "outputs": result.get("outputs"),
        }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
