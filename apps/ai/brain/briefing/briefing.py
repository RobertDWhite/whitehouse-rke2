#!/usr/bin/env python3
"""brain-briefing: write a daily briefing note into the Forgejo brain repo and push a short
notification about it.

It reads the whole repo from one archive download, pulls out what needs attention today
(reminders, birthdays, CRM tasks and deals, recording action items, agent sessions, entity
mentions), writes `$BRIEFING_DIR/<YYYY>/<YYYY-MM-DD>.md` through Forgejo's change-files API,
then posts a summary with a link to ntfy and, in more detail, to a Matrix room. A local OpenAI-compatible LLM can optionally add a
"top priorities" paragraph; if it is unset or fails the briefing is written without it.

Standard library only, so it runs on the stock python image.
"""

import base64
import html
import io
import json
import logging
import os
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "America/New_York"))
except Exception:  # no tzdata in the image: fall back to UTC
    LOCAL_TZ = timezone.utc

FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://forgejo.forgejo.svc.cluster.local").rstrip("/")
FORGEJO_TOKEN = os.environ["FORGEJO_TOKEN"]
REPO = os.environ.get("BRAIN_REPO", "WhiteHouse/brain")
BRANCH = os.environ.get("BRAIN_BRANCH", "main")
WEB_URL = os.environ.get("BRAIN_WEB_URL", "https://git.internal.white.fm").rstrip("/")
AUTHOR = {"name": "brain-sync", "email": "brain-sync@whitematter.tech"}

BRIEFING_DIR = os.environ.get("BRIEFING_DIR", "Briefings").strip("/")
MEERKAT_DIR = os.environ.get("MEERKAT_DIR", "Meerkat").strip("/")
RIFFADO_DIR = os.environ.get("RIFFADO_DIR", "Riffado").strip("/")
HUBSPOT_DIR = os.environ.get("HUBSPOT_DIR", "HubSpot").strip("/")
AGENTS_DIR = os.environ.get("AGENTS_DIR", "Agents").strip("/")
ENTITIES_DIR = os.environ.get("ENTITIES_DIR", "Entities").strip("/")

BIRTHDAY_DAYS = int(os.environ.get("BIRTHDAY_DAYS", "14"))
UPCOMING_DAYS = int(os.environ.get("UPCOMING_DAYS", "7"))  # reminders/tasks coming up
DEAL_CHANGED_HOURS = int(os.environ.get("DEAL_CHANGED_HOURS", "24"))
DEAL_CLOSING_DAYS = int(os.environ.get("DEAL_CLOSING_DAYS", "14"))
ACTION_ITEM_DAYS = int(os.environ.get("ACTION_ITEM_DAYS", "7"))
AGENT_HOURS = int(os.environ.get("AGENT_HOURS", "24"))
ENTITY_DAYS = int(os.environ.get("ENTITY_DAYS", "7"))
ENTITY_TOP = int(os.environ.get("ENTITY_TOP", "10"))
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "25"))  # per list
# CRM tasks and deal close dates overdue by more than this are counted, not listed.
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))

LLM_URL = os.environ.get("LLM_URL", "").rstrip("/")  # OpenAI-compatible base, e.g. http://host/v1
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT_SECONDS", "300"))

NTFY_URL = os.environ.get("NTFY_URL", "").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
# The push only carries counts unless this is set: ntfy topics may be readable by anyone
# who knows the topic name, while the note itself stays behind Forgejo auth.
NTFY_DETAILS = os.environ.get("NTFY_INCLUDE_PRIORITIES", "") == "1"

# Hookshot generic webhook of the (end-to-end encrypted) Brain room; posts carry the
# priorities and the items due today, not just counts.
MATRIX_WEBHOOK_URL = os.environ.get("MATRIX_WEBHOOK_URL", "")
MATRIX_ITEMS = int(os.environ.get("MATRIX_ITEMS", "5"))  # per section

DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brain-briefing")

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ------------------------------------------------------------------ helpers


def forgejo(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1{path}", data=data, method=method)
    req.add_header("Authorization", f"token {FORGEJO_TOKEN}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300].decode(errors='replace')}") from None


def repo_snapshot():
    """{path: text} for every Markdown file on BRANCH, from one archive download."""
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1/repos/{REPO}/archive/{BRANCH}.tar.gz")
    req.add_header("Authorization", f"token {FORGEJO_TOKEN}")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar:
            if m.isfile() and m.name.endswith(".md") and "/" in m.name:
                files[m.name.split("/", 1)[1]] = tar.extractfile(m).read().decode("utf-8", errors="replace")
    return files


def parse_frontmatter(text):
    """The flat `key: json-value` frontmatter brain-sync writes (tolerates plain YAML scalars)."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        k, sep, v = line.partition(":")
        if not sep or line.startswith((" ", "-")):
            continue
        v = v.strip()
        try:
            fm[k.strip()] = json.loads(v)
        except ValueError:
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip("\"'")
    return fm


def section_lines(text, heading):
    """Lines under `## heading` up to the next `## ` heading."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == heading.lower()
            continue
        if inside:
            out.append(line)
    return out


def parse_ts(value):
    if not value:
        return None
    v = str(value).strip().replace("Z", "+00:00")
    v = re.sub(r"(\.\d{6})\d+", r"\1", v)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        m = DATE_RE.match(v)
        if not m:
            return None
        dt = datetime.fromisoformat(m.group(0))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def parse_day(value):
    m = DATE_RE.search(str(value or ""))
    try:
        return date.fromisoformat(m.group(0)) if m else None
    except ValueError:
        return None


def title_of(path, fm):
    return str(fm.get("title") or fm.get("name") or path.rsplit("/", 1)[-1][:-3])


def wikilink(path, title):
    label = re.sub(r"[|\[\]]+", " ", str(title)).strip() or path.rsplit("/", 1)[-1][:-3]
    return f"[[{path[:-3] if path.endswith('.md') else path}|{label}]]"


def clip(text, n=160):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def rel_day(d, today):
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if delta < 0:
        return f"{-delta} days overdue"
    return f"in {delta} days ({d.strftime('%a %b %-d')})"


# ------------------------------------------------------------------ sections
# Each collector returns a list of items: {"line": markdown bullet, "brief": short text for the
# LLM, ...}. Sections render from these lists; the notification counts them.


def collect_reminders(files, today):
    due, upcoming = [], []
    for path, text in sorted(files.items()):
        if not path.startswith(MEERKAT_DIR + "/"):
            continue
        fm = parse_frontmatter(text)
        who = title_of(path, fm)
        for line in section_lines(text, "Reminders"):
            m = re.match(r"- \[ \] (\d{4}-\d{2}-\d{2}) — (.*)", line)
            if not m:
                continue
            d = parse_day(m.group(1))
            if not d or d > today + timedelta(days=UPCOMING_DAYS):
                continue
            item = {"day": d, "line": f"{wikilink(path, who)} — {clip(m.group(2))} · {rel_day(d, today)}",
                    "brief": f"Reminder ({rel_day(d, today)}) for {who}: {clip(m.group(2), 120)}"}
            (due if d <= today else upcoming).append(item)
    key = lambda i: i["day"]
    return sorted(due, key=key), sorted(upcoming, key=key)


def collect_birthdays(files, today):
    out = []
    for path, text in files.items():
        if not path.startswith(MEERKAT_DIR + "/"):
            continue
        fm = parse_frontmatter(text)
        if fm.get("archived"):
            continue
        m = re.fullmatch(r"(\d{4}|-)-(\d{2})-(\d{2})", str(fm.get("birthday") or "").strip())
        if not m:
            continue
        month, day = int(m.group(2)), int(m.group(3))
        nxt = None
        for year in (today.year, today.year + 1):
            try:
                cand = date(year, month, day)
            except ValueError:  # Feb 29 in a non-leap year
                cand = date(year, 3, 1)
            if cand >= today:
                nxt = cand
                break
        if not nxt or (nxt - today).days > BIRTHDAY_DAYS:
            continue
        who = title_of(path, fm)
        age = ""
        if m.group(1) != "-" and 1900 < int(m.group(1)) < nxt.year:
            age = f" (turns {nxt.year - int(m.group(1))})"
        out.append({"day": nxt, "line": f"{wikilink(path, who)}{age} · {rel_day(nxt, today)}",
                    "brief": f"Birthday {rel_day(nxt, today)}: {who}"})
    return sorted(out, key=lambda i: i["day"])


HS_KIND_ORDER = {"Deals": 0, "Contacts": 1, "Companies": 2}


def collect_hubspot(files, today, now):
    tasks, changed, closing = {}, [], []
    stale_tasks, stale_deals = set(), 0
    stale_cut = today - timedelta(days=STALE_DAYS)
    base = HUBSPOT_DIR + "/"
    for path, text in files.items():
        if not path.startswith(base):
            continue
        kind = path[len(base):].split("/", 1)[0]
        if kind not in HS_KIND_ORDER:
            continue
        fm = parse_frontmatter(text)
        name = title_of(path, fm)
        # Open tasks live in each record's timeline as "### <due date> — Task - <subject>"
        # (completed ones end with a check mark). A task linked to several records shows up
        # once per record; keep the deal, then contact, then company copy.
        for line in section_lines(text, "Timeline"):
            m = re.match(r"### (\d{4}-\d{2}-\d{2}|Undated) — Task(?: - |: )(.*)", line)
            if not m or "✓" in m.group(2):
                continue
            d = parse_day(m.group(1))
            if d and d > today + timedelta(days=UPCOMING_DAYS):
                continue
            subject = m.group(2).strip()
            key = (m.group(1), subject.casefold())
            if d and d < stale_cut:
                stale_tasks.add(key)
                continue
            rank = HS_KIND_ORDER[kind]
            if key in tasks and tasks[key]["rank"] <= rank:
                continue
            when = rel_day(d, today) if d else "no due date"
            tasks[key] = {"rank": rank, "day": d or today, "overdue": bool(d and d < today),
                          "line": f"{clip(subject, 120)} — {wikilink(path, name)} · {when}",
                          "brief": f"CRM task ({when}): {clip(subject, 100)} [{name}]"}
        if kind != "Deals":
            continue
        stage = str(fm.get("stage") or "")
        is_closed = "closed" in stage.lower()
        bits = " · ".join(x for x in (stage, fm.get("amount")) if x)
        upd = parse_ts(fm.get("updated_at"))
        if upd and now - upd <= timedelta(hours=DEAL_CHANGED_HOURS):
            changed.append({"ts": upd, "line": f"{wikilink(path, name)} — {bits or 'updated'}"
                                                f" · updated {upd.strftime('%a %-I:%M %p')}",
                            "brief": f"Deal updated: {name} ({bits})"})
        cd = parse_day(fm.get("close_date"))
        if cd and not is_closed and cd < stale_cut:
            stale_deals += 1
        elif cd and not is_closed and cd <= today + timedelta(days=DEAL_CLOSING_DAYS):
            closing.append({"day": cd, "line": f"{wikilink(path, name)} — {bits} · close date {rel_day(cd, today)}",
                            "brief": f"Deal close date {rel_day(cd, today)}: {name} ({bits})"})
    open_tasks = sorted(tasks.values(), key=lambda t: (t["day"], t["line"]))
    return (open_tasks, sorted(changed, key=lambda i: i["ts"], reverse=True),
            sorted(closing, key=lambda i: i["day"]), len(stale_tasks), stale_deals)


def collect_action_items(files, now):
    out = []
    cutoff = now - timedelta(days=ACTION_ITEM_DAYS)
    for path, text in files.items():
        if not path.startswith(RIFFADO_DIR + "/"):
            continue
        fm = parse_frontmatter(text)
        rec = parse_ts(fm.get("recorded_at")) or parse_ts(parse_day(path) and str(parse_day(path)))
        if not rec or rec < cutoff:
            continue
        title = title_of(path, fm)
        items = [clip(m.group(1), 200) for line in section_lines(text, "Action items")
                 if (m := re.match(r"\s*- \[ \] (.+)", line))]
        for it in items:
            out.append({"ts": rec, "path": path, "title": title, "text": it,
                        "brief": f"Action item from recording '{title}': {it}"})
    return sorted(out, key=lambda i: i["ts"], reverse=True)


def collect_agents(files, now):
    out = []
    cutoff = now - timedelta(hours=AGENT_HOURS)
    for path, text in files.items():
        if not path.startswith(AGENTS_DIR + "/"):
            continue
        fm = parse_frontmatter(text)
        ts = parse_ts(fm.get("ended")) or parse_ts(fm.get("started"))
        if not ts or ts < cutoff:
            continue
        agent = path.split("/")[1] if path.count("/") >= 2 else ""
        msgs = fm.get("user_messages")
        extra = " · ".join(x for x in (agent, f"{msgs} prompts" if msgs else "", ts.strftime("%a %-I:%M %p")) if x)
        out.append({"ts": ts, "line": f"{wikilink(path, title_of(path, fm))} — {extra}",
                    "brief": f"Agent session: {clip(title_of(path, fm), 100)}"})
    return sorted(out, key=lambda i: i["ts"], reverse=True)


def collect_entities(files, today):
    """Entities ranked by mentions whose note date falls in the last ENTITY_DAYS days.

    Entity pages list mentions as `- [[path|title]] · YYYY-MM-DD — snippet`. Mentions from
    earlier briefings are ignored so the briefing doesn't feed on itself.
    """
    out = []
    cutoff = today - timedelta(days=ENTITY_DAYS)
    for path, text in files.items():
        if not path.startswith(ENTITIES_DIR + "/") or path.endswith("/README.md"):
            continue
        n = 0
        for line in text.splitlines():
            m = re.match(r"- \[\[([^\]|]+)(?:\|[^\]]*)?\]\] · (\d{4}-\d{2}-\d{2})", line)
            if not m or m.group(1).startswith(BRIEFING_DIR + "/"):
                continue
            d = parse_day(m.group(2))
            if d and cutoff < d <= today:
                n += 1
        if n:
            fm = parse_frontmatter(text)
            name = title_of(path, fm)
            out.append({"n": n, "line": f"{wikilink(path, name)} — {n} new mention{'s' if n != 1 else ''}"
                                        f" ({fm.get('type') or 'entity'})",
                        "brief": f"{name} mentioned in {n} notes this week"})
    return sorted(out, key=lambda i: (-i["n"], i["line"]))[:ENTITY_TOP]


# HOOK: calendar and email. Not connected yet. When a source exists, return a list of items
# shaped like the collectors above ({"line": ..., "brief": ...}); the section renders them
# instead of the placeholder and they feed the priorities prompt and the notification.
def collect_calendar(today):
    return None


def collect_email(today):
    return None


# ------------------------------------------------------------------ LLM (optional)


def llm_priorities(briefs, today):
    if not (LLM_URL and LLM_MODEL and briefs):
        return None
    prompt = (
        f"Today is {today.strftime('%A %B %-d, %Y')}. Below is everything from my notes that needs "
        "attention today. Pick the three most important things to do today and give each as one "
        "short markdown bullet (imperative, at most 20 words, keep names as written). Prefer overdue "
        "and time-bound items. Reply with exactly three bullets and nothing else.\n\n"
        + "\n".join(f"- {b}" for b in briefs[:120])
    )
    body = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": 4000, "reasoning_effort": "none"}
    req = urllib.request.Request(f"{LLM_URL}/chat/completions", data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if LLM_API_KEY:
        req.add_header("Authorization", f"Bearer {LLM_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            msg = json.loads(resp.read())["choices"][0]["message"]
    except Exception as e:  # fail soft: the briefing is useful without it
        log.warning("llm: %s", e)
        return None
    text = re.sub(r"(?s)<think>.*?</think>", "", msg.get("content") or "").strip()
    bullets = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "- ", l).rstrip() for l in text.splitlines()
               if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+\S", l)]
    return bullets[:3] or None


# ------------------------------------------------------------------ render


def bullets(items, limit=MAX_ITEMS):
    lines = [f"- {i['line']}" for i in items[:limit]]
    if len(items) > limit:
        lines.append(f"- _…and {len(items) - limit} more._")
    return lines


def render(today, now, data, priorities):
    out = ["---", f"type: {json.dumps('briefing')}", f"date: {json.dumps(today.isoformat())}",
           f"generated_at: {json.dumps(now.isoformat(timespec='minutes'))}",
           f"tags: {json.dumps(['briefing'])}", "---",
           "<!-- Generated by brain-briefing (whitehouse-rke2 apps/ai/brain/briefing). "
           "Rewritten when the job runs again for this date. -->", "",
           f"# Briefing — {today.strftime('%A, %B %-d, %Y')}", ""]
    out.append(f"*{' · '.join(data['summary'])}*" if data["summary"] else "*Nothing flagged today.*")

    if priorities:
        out += ["", "## Top priorities", "", *priorities, "", "_Suggested by the local LLM; check the lists below._"]

    out += ["", "## Calendar", ""]
    out += bullets(data["calendar"]) if data["calendar"] is not None else ["_Not connected yet._"]
    out += ["", "## Email", ""]
    out += bullets(data["email"]) if data["email"] is not None else ["_Not connected yet._"]

    out += ["", "## Reminders", ""]
    if data["reminders_due"]:
        out += ["**Due today or overdue**", "", *bullets(data["reminders_due"])]
    if data["reminders_upcoming"]:
        out += ([""] if data["reminders_due"] else []) + [f"**Next {UPCOMING_DAYS} days**", "",
                                                          *bullets(data["reminders_upcoming"])]
    if not (data["reminders_due"] or data["reminders_upcoming"]):
        out.append("_None._")

    out += ["", "## Birthdays", ""]
    out += bullets(data["birthdays"]) or [f"_None in the next {BIRTHDAY_DAYS} days._"]

    out += ["", "## CRM", "", "### Open tasks", ""]
    out += bullets(data["tasks"]) or [f"_No open tasks due within {UPCOMING_DAYS} days._"]
    if data["stale_tasks"]:
        out += ["", f"_{data['stale_tasks']} open tasks more than {STALE_DAYS} days overdue are not listed._"]
    out += ["", f"### Deals changed (last {DEAL_CHANGED_HOURS}h)", ""]
    out += bullets(data["deals_changed"]) or ["_None._"]
    out += ["", f"### Open deals closing within {DEAL_CLOSING_DAYS} days or recently overdue", ""]
    out += bullets(data["deals_closing"]) or ["_None._"]
    if data["stale_deals"]:
        out += ["", f"_{data['stale_deals']} open deals with a close date more than {STALE_DAYS} days ago are not listed._"]

    out += ["", f"## Action items from recordings (last {ACTION_ITEM_DAYS} days)", ""]
    if data["actions"]:
        by_rec = {}
        for a in data["actions"]:
            by_rec.setdefault((a["ts"], a["path"], a["title"]), []).append(a["text"])
        for (ts, path, title), texts in sorted(by_rec.items(), reverse=True):
            out.append(f"- {wikilink(path, title)} · {ts.strftime('%a %b %-d')}")
            out += [f"    - [ ] {t}" for t in texts]
    else:
        out.append("_None._")

    out += ["", f"## Agent sessions (last {AGENT_HOURS}h)", ""]
    out += bullets(data["agents"]) or ["_None._"]

    out += ["", f"## Most mentioned (last {ENTITY_DAYS} days)", ""]
    out += bullets(data["entities"]) or ["_No new mentions._"]
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ deliver


def commit(path, content, message):
    existing = forgejo("GET", f"/repos/{REPO}/contents/{urllib.parse.quote(path)}?ref={BRANCH}")
    if existing and base64.b64decode(existing.get("content") or "").decode(errors="replace") == content:
        log.info("note unchanged: %s", path)
        return
    op = {"operation": "update" if existing else "create", "path": path,
          "content": base64.b64encode(content.encode()).decode()}
    if existing:
        op["sha"] = existing["sha"]
    forgejo("POST", f"/repos/{REPO}/contents", {"branch": BRANCH, "message": message, "author": AUTHOR,
                                                "committer": AUTHOR, "files": [op]})
    log.info("committed %s (%s)", path, op["operation"])


def notify(title, message, url):
    if not (NTFY_URL and NTFY_TOPIC):
        log.info("ntfy not configured; skipping notification")
        return
    req = urllib.request.Request(f"{NTFY_URL}/{urllib.parse.quote(NTFY_TOPIC)}", data=message.encode(),
                                 method="POST")
    # Header values must be latin-1; titles here are plain ASCII.
    req.add_header("Title", title)
    req.add_header("Click", url)
    req.add_header("Tags", "sunrise")
    req.add_header("Markdown", "yes")
    req.add_header("Actions", f"view, Open note, {url}")
    if NTFY_TOKEN:
        req.add_header("Authorization", f"Bearer {NTFY_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("ntfy: %s", resp.status)
    except Exception as e:
        log.warning("ntfy: %s", e)


def notify_matrix(title, summary, priorities, sections, url):
    if not MATRIX_WEBHOOK_URL:
        log.info("matrix not configured; skipping")
        return
    plain = [f"☀️ {title}", summary]
    rich = [f"<b>☀️ {html.escape(title)}</b> · <a href=\"{html.escape(url)}\">open note</a><br>{html.escape(summary)}<br>"]
    for heading, items in (("Top priorities", [re.sub(r"^- ", "", p) for p in priorities or []]), *sections):
        if not items:
            continue
        shown = items[:MATRIX_ITEMS] + ([f"… {len(items) - MATRIX_ITEMS} more"] if len(items) > MATRIX_ITEMS else [])
        plain += ["", f"{heading}:"] + [f"• {i}" for i in shown]
        rich.append(f"<b>{html.escape(heading)}</b><ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in shown)
                    + "</ul>")
    plain += ["", url]
    req = urllib.request.Request(MATRIX_WEBHOOK_URL, method="POST",
                                 data=json.dumps({"text": "\n".join(plain), "html": "".join(rich)}).encode())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("matrix: %s", resp.status)
    except Exception as e:
        log.warning("matrix: %s", e)


def main():
    now = datetime.now(LOCAL_TZ)
    today = date.fromisoformat(os.environ["BRIEFING_DATE"]) if os.environ.get("BRIEFING_DATE") else now.date()
    files = repo_snapshot()
    log.info("snapshot: %d notes", len(files))

    reminders_due, reminders_upcoming = collect_reminders(files, today)
    tasks, deals_changed, deals_closing, stale_tasks, stale_deals = collect_hubspot(files, today, now)
    data = {
        "calendar": collect_calendar(today),
        "email": collect_email(today),
        "reminders_due": reminders_due,
        "reminders_upcoming": reminders_upcoming,
        "birthdays": collect_birthdays(files, today),
        "tasks": tasks,
        "deals_changed": deals_changed,
        "deals_closing": deals_closing,
        "stale_tasks": stale_tasks,
        "stale_deals": stale_deals,
        "actions": collect_action_items(files, now),
        "agents": collect_agents(files, now),
        "entities": collect_entities(files, today),
    }
    overdue_tasks = sum(1 for t in tasks if t["overdue"])
    counts = [(len(reminders_due), "reminder due", "reminders due"),
              (len(data["birthdays"]), "birthday", "birthdays"),
              (len(tasks), "open CRM task", "open CRM tasks"),
              (len(deals_changed), "deal changed", "deals changed"),
              (len(data["actions"]), "action item", "action items"),
              (len(data["agents"]), "agent session", "agent sessions")]
    data["summary"] = [f"{n} {one if n == 1 else many}" + (f" ({overdue_tasks} overdue)" if many == "open CRM tasks"
                                                           and overdue_tasks else "")
                       for n, one, many in counts if n]
    briefs = [i["brief"] for k in ("calendar", "email", "reminders_due", "tasks", "deals_closing", "birthdays",
                                   "actions", "deals_changed", "reminders_upcoming")
              for i in (data[k] or [])]
    priorities = llm_priorities(briefs, today)

    path = f"{BRIEFING_DIR}/{today.year}/{today.isoformat()}.md"
    note = render(today, now, data, priorities)
    url = f"{WEB_URL}/{REPO}/src/branch/{BRANCH}/" + urllib.parse.quote(path)
    if DRY_RUN:
        print(note)
        return
    commit(path, note, f"briefing: {today.isoformat()}")

    msg = " · ".join(data["summary"]) or "Nothing flagged today."
    if priorities and NTFY_DETAILS:
        msg += "\n\n" + "\n".join(priorities)
    notify(f"Briefing {today.strftime('%a %b %-d')}", msg, url)
    notify_matrix(f"Briefing {today.strftime('%a %b %-d')}", " · ".join(data["summary"]) or "Nothing flagged today.",
                  priorities, [(h, [i["brief"] for i in data[k] or []]) for h, k in (
                      ("Calendar", "calendar"), ("Reminders due", "reminders_due"), ("Birthdays", "birthdays"),
                      ("Deals closing", "deals_closing"), ("Action items", "actions"))], url)


if __name__ == "__main__":
    main()
