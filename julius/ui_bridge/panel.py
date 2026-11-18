"""Side-panel events: `panel.*` messages on the UI event feed.

Everything that reaches the panel is untrusted (web page titles, claims, alt text, file names), so it is cleaned here, once, before it is
published: control and bidi characters are stripped, every string and list is capped, URLs must be http(s), image references must be an
opaque id served by the panel file server. Messages carry data only, never HTML. A bad payload is dropped and logged; publishing never raises
into a spoken turn. The schema table `SCHEMAS` is the single source for the validators, the conformance test and docs/EVENT_FEED.md."""

import collections
import json
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

HISTORY = 20
MAX_BYTES = 48_000
MAX_SOURCES = 12
MAX_FINDINGS = 12
MAX_IMAGES = 8

CAPS = {
    "question": 300,
    "summary": 1200,
    "claim": 600,
    "title": 200,
    "site": 100,
    "date": 40,
    "alt": 300,
    "url": 2000,
    "note": 300,
    "stopped": 40,
    "name": 200,
    "where": 300,
    "status": 20,
}
ITEM_PAGE = 25  # file rows on a card at first; "show more" adds this many
MAX_ITEMS = 200
FILE_STATUSES = ("pending", "completed", "cancelled", "undone", "partial", "expired", "listing")
IMAGE_REF = re.compile(r"^/p/[A-Za-z0-9_-]{8,64}\.(?:png|jpg|webp|gif)$")
_BIDI = set("‪‫‬‭‮⁦⁧⁨⁩‎‏؜")

# type -> (required fields, optional fields); values are the JSON type names the validator checks
COMMON = {
    "type": "str",
    "t": "num",
    "panel_id": "str",
    "turn": "int?",
    "seq": "int",
    "conv": "int?",
}
SCHEMAS: dict[str, dict[str, str]] = {
    "panel.research": {
        "question": "str",
        "summary": "str",
        "summary_cite": "list?",
        "findings": "list",
        "sources": "list",
        "images": "list",
        "dropped": "int",
        "stopped": "str?",
        "truncated": "bool?",
    },
    "panel.files": {
        "title": "str",
        "kind": "str",
        "action": "str?",
        "status": "str",
        "plan_id": "str?",
        "where": "str",
        "summary": "str",
        "items": "list",
        "shown": "int",
        "total": "int",
        "total_bytes": "int?",
        "skipped": "int",
        "more": "bool",
    },
    "panel.chart": {
        "title": "str",
        "kind": "str",
        "unit": "str",
        "image": "str",
        "alt": "str",
        "labels": "list",
        "series": "list",
        "note": "str?",
    },
    "panel.table": {
        "title": "str",
        "unit": "str",
        "columns": "list",
        "rows": "list",
        "note": "str?",
    },
    "panel.images": {"title": "str", "question": "str", "images": "list"},
    "panel.vision": {
        "title": "str",
        "source": "str",
        "question": "str",
        "answer": "str",
        "image": "str",
        "alt": "str",
        "model": "str",
        "cost_usd": "num",
        "est_cost_usd": "num",
    },
    "panel.art": {
        "title": "str",
        "kind": "str",
        "before": "str",
        "after": "str",
        "compare": "str",
        "export": "str",
        "small": "str",
        "alt": "str",
        "summary": "str",
        "settings": "list",
        "palette": "list",
        "editing": "bool?",
        "sprite_id": "str?",
        "main": "bool?",
        "reveal": "list?",
    },
    "panel.camera": {"title": "str", "image": "str", "note": "str"},
    "panel.code": {  # a coding task's changes: the files and the diff of its scratch copy (nothing is in the project until a merge)
        "title": "str",
        "project": "str",
        "summary": "str",
        "files": "list",
        "diff": "list",
        "tests": "str?",
    },
    "panel.camera_open": {"autostart": "bool", "auto_use_s": "int"},
    "panel.camera_status": {"enabled": "bool", "reason": "str", "cause": "str"},
    "panel.theory": {
        "title": "str",
        "kind": "str",
        "key": "str",
        "scale_notes": "list",
        "chords": "list",
    },
    "panel.update": {
        "target": "str",
        "images": "list?",
        "note": "str?",
        "items": "list?",
        "status": "str?",
        "shown": "int?",
        "more": "bool?",
        "summary": "str?",
        "after": "str?",
        "export": "str?",
        "small": "str?",
        "settings": "list?",
        "palette": "list?",
        "editing": "bool?",
        "main": "bool?",
    },
    "panel.clear": {"scope": "str", "target": "str?", "reason": "str"},
    # not cards: the conversation list (the sidebar) and the notes the brain was given this turn (the "Memory used" chip)
    "panel.conversations": {
        "current": "int?",
        "title": "str",
        "project": "str",
        "projects": "list",
        "conversations": "list",
        "trash": "int",
        "messages": "list?",
    },
    "panel.memory_used": {"items": "list"},
}
CONV_FIELDS = {
    "id": "int",
    "title": "str",
    "project": "str",
    "pinned": "bool",
    "updated": "num",
    "n": "int",
}
PROJECT_FIELDS = {"id": "int", "name": "str", "pinned": "bool"}
MEMORY_ITEM_FIELDS = {"id": "int", "scope": "str", "label": "str", "text": "str"}
# the card types (kept, shown per conversation); the rest are updates and status messages
CARD_TYPES = {
    "panel.research",
    "panel.files",
    "panel.chart",
    "panel.table",
    "panel.images",
    "panel.vision",
    "panel.theory",
    "panel.art",
    "panel.camera",
    "panel.code",
}
PER_CONVERSATION = 20
MAX_KEPT = 120
CLEAR_SCOPES = ("all", "card")
CLEAR_REASONS = ("new_topic", "dismissed", "expired", "done")
THEORY_KINDS = ("key", "scale", "progression", "chord")
THEORY_CHORD_FIELDS = {
    "symbol": "str",
    "roman": "str",
    "label": "str",
    "notes": "list",
    "midi": "list",
    "root": "int",
}
MAX_THEORY_CHORDS = 16
FINDING_FIELDS = {
    "n": "int",
    "claim": "str",
    "cite": "list",
    "verified": "bool?",
}  # verified: false when its quote was not found on the cited page
SOURCE_FIELDS = {"n": "int", "title": "str", "site": "str", "date": "str?", "url": "str"}
ITEM_FIELDS = {
    "n": "int",
    "name": "str",
    "dir": "str",
    "size": "int?",
    "kind": "str",
    "was": "str?",
}
SERIES_FIELDS = {"name": "str", "values": "list"}
IMAGE_FIELDS = {"n": "int", "image": "str", "alt": "str", "page_url": "str", "page_title": "str"}


def clean(value: Any, cap: int) -> str:
    """Printable text only: no control characters, no bidi overrides, collapsed whitespace, capped."""
    s = unicodedata.normalize("NFC", str(value if value is not None else ""))
    s = "".join(
        " " if ch.isspace() else ch
        for ch in s
        if ch not in _BIDI and (ch.isspace() or unicodedata.category(ch)[0] != "C")
    )
    s = " ".join(s.split())
    return s if len(s) <= cap else s[: cap - 1].rstrip() + "…"


def clean_text(value: Any, cap: int) -> str:
    """Like `clean`, but keeps line breaks (at most one blank line in a row) so the panel's markdown subset can show lists and paragraphs."""
    raw = (
        unicodedata.normalize("NFC", str(value if value is not None else ""))
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    lines: list[str] = []
    for line in raw.split("\n"):
        line = "".join(
            " " if ch.isspace() else ch
            for ch in line
            if ch not in _BIDI and (ch.isspace() or unicodedata.category(ch)[0] != "C")
        )
        line = " ".join(line.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    s = "\n".join(lines).strip("\n")
    return s if len(s) <= cap else s[: cap - 1].rstrip() + "…"


def clean_url(value: Any) -> str:
    """http(s) URLs only (no credentials, no spaces); anything else becomes an empty string."""
    s = str(value or "").strip()
    if len(s) > CAPS["url"] or any(c.isspace() or ord(c) < 32 for c in s):
        return ""
    try:
        parts = urlsplit(s)
    except ValueError:
        return ""
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        return ""
    return s


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "") if url else ""
    return host[4:] if host.startswith("www.") else host


def _is(kind: str, v: Any) -> bool:
    optional = kind.endswith("?")
    kind = kind.rstrip("?")
    if v is None:
        return optional
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == "num":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return {"str": isinstance(v, str), "list": isinstance(v, list)}.get(kind, False)


def _check(fields: dict[str, str], obj: Any, where: str, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        errors.append(f"{where}: not an object")
        return
    for k, kind in fields.items():
        if k not in obj:
            if not kind.endswith("?"):
                errors.append(f"{where}.{k}: missing")
        elif not _is(kind, obj[k]):
            errors.append(f"{where}.{k}: expected {kind}")


def _v_research(msg: dict, errors: list[str]) -> None:
    for i, f in enumerate(msg["findings"]):
        _check(FINDING_FIELDS, f, f"findings[{i}]", errors)
    for i, s in enumerate(msg["sources"]):
        _check(SOURCE_FIELDS, s, f"sources[{i}]", errors)
        if isinstance(s, dict) and not clean_url(s.get("url")):
            errors.append(f"sources[{i}].url: not http(s)")
    _v_images(msg["images"], errors)


def _v_images(images: list, errors: list[str]) -> None:
    for i, im in enumerate(images):
        _check(IMAGE_FIELDS, im, f"images[{i}]", errors)
        if isinstance(im, dict) and not IMAGE_REF.match(str(im.get("image"))):
            errors.append(f"images[{i}].image: not a panel file reference")


def _v_files(msg: dict, errors: list[str]) -> None:
    if msg["status"] not in FILE_STATUSES:
        errors.append("panel.files: bad status")
    for i, it in enumerate(msg["items"]):
        _check(ITEM_FIELDS, it, f"items[{i}]", errors)


CODE_FILE_FIELDS = {"path": "str", "status": "str"}
CODE_STATUSES = ("added", "modified", "deleted")
MAX_DIFF_LINES = 160


def _v_code(msg: dict, errors: list[str]) -> None:
    for i, f in enumerate(msg["files"]):
        _check(CODE_FILE_FIELDS, f, f"files[{i}]", errors)
        if isinstance(f, dict) and f.get("status") not in CODE_STATUSES:
            errors.append(f"files[{i}].status: bad status")
    if len(msg["diff"]) > MAX_DIFF_LINES or not all(isinstance(x, str) for x in msg["diff"]):
        errors.append("panel.code.diff: lines of text, at most 160")


def _v_camera(msg: dict, errors: list[str]) -> None:
    if not IMAGE_REF.match(str(msg["image"])):
        errors.append("panel.camera.image: not a panel file reference")


def _v_update(msg: dict, errors: list[str]) -> None:
    for k in ("after", "export", "small"):
        if k in msg and not IMAGE_REF.match(str(msg[k])):
            errors.append(f"panel.update.{k}: not a panel file reference")
    if "palette" in msg and not all(isinstance(c, str) and HEX6.match(c) for c in msg["palette"]):
        errors.append("panel.update.palette: colours as 6 hex digits")
    if "settings" in msg and not all(isinstance(x, str) for x in msg["settings"]):
        errors.append("panel.update.settings: not text")
    for i, it in enumerate(msg.get("items") or []):
        _check(ITEM_FIELDS, it, f"items[{i}]", errors)
    if msg.get("status") is not None and msg["status"] not in FILE_STATUSES:
        errors.append("panel.update: bad status")


def _v_chart(msg: dict, errors: list[str]) -> None:
    if not IMAGE_REF.match(str(msg["image"])):
        errors.append("panel.chart.image: not a panel file reference")
    if msg["kind"] not in ("bar", "hbar", "line", "pie"):
        errors.append("panel.chart: bad kind")
    for i, s in enumerate(msg["series"]):
        _check(SERIES_FIELDS, s, f"series[{i}]", errors)


VISION_SOURCES = ("camera", "window", "screen", "image")


def _v_vision(msg: dict, errors: list[str]) -> None:
    if not IMAGE_REF.match(str(msg["image"])):
        errors.append("panel.vision.image: not a panel file reference")
    if msg["source"] not in VISION_SOURCES:
        errors.append("panel.vision: bad source")


ART_KINDS = ("pixel", "compare", "filter", "sprite")
MAX_ART_PALETTE = 64
HEX6 = re.compile(r"^[0-9A-F]{6}$")


REVEAL_MAX = 64  # the sand reveal's grids are at most 64 x 64
REVEAL_PX = re.compile(r"^(?:[0-9A-F]{6}|-{6})*$")


def reveal_ok(r: Any) -> bool:
    """The sprite card's sand reveal: [w, h, photo, sprite], each grid w*h pixels as 6 hex digits ("------" is clear)."""
    if not (isinstance(r, list) and len(r) == 4 and all(isinstance(x, int) for x in r[:2])):
        return False
    w, h = r[0], r[1]
    return (
        0 < w <= REVEAL_MAX
        and 0 < h <= REVEAL_MAX
        and all(isinstance(g, str) and len(g) == w * h * 6 and REVEAL_PX.match(g) for g in r[2:])
    )


def _v_art(msg: dict, errors: list[str]) -> None:
    for k in ("before", "after", "export", "small"):
        if not IMAGE_REF.match(str(msg[k])):
            errors.append(f"panel.art.{k}: not a panel file reference")
    if msg["compare"] and not IMAGE_REF.match(str(msg["compare"])):
        errors.append(
            "panel.art.compare: not a panel file reference"
        )  # optional: a second result shown beside the first
    if msg["kind"] not in ART_KINDS:
        errors.append("panel.art: bad kind")
    if not all(isinstance(x, str) for x in msg["settings"]):
        errors.append("panel.art.settings: not text")
    if len(msg["palette"]) > MAX_ART_PALETTE or not all(
        isinstance(c, str) and HEX6.match(c) for c in msg["palette"]
    ):
        errors.append("panel.art.palette: colours as 6 hex digits")
    if msg.get("reveal") and not reveal_ok(msg["reveal"]):
        errors.append("panel.art.reveal: [w, h, photo, sprite] grids of 6 hex digits per pixel")


def _v_theory(msg: dict, errors: list[str]) -> None:
    if msg["kind"] not in THEORY_KINDS:
        errors.append("panel.theory: bad kind")
    if len(msg["chords"]) > MAX_THEORY_CHORDS or not msg["chords"]:
        errors.append(f"panel.theory.chords: 1 to {MAX_THEORY_CHORDS} entries")
    if not all(isinstance(n, str) for n in msg["scale_notes"]):
        errors.append("panel.theory.scale_notes: not text")
    for i, c in enumerate(msg["chords"]):
        _check(THEORY_CHORD_FIELDS, c, f"chords[{i}]", errors)
        if not isinstance(c, dict) or errors:
            continue
        if not all(isinstance(n, str) for n in c["notes"]) or not all(
            isinstance(m, int) and not isinstance(m, bool) and 21 <= m <= 108 for m in c["midi"]
        ):
            errors.append(f"chords[{i}]: notes must be text and midi numbers 21 to 108")
        if not 0 <= c["root"] <= 11:
            errors.append(f"chords[{i}].root: not a pitch class")


def _v_table(msg: dict, errors: list[str]) -> None:
    for i, r in enumerate(msg["rows"]):
        if not isinstance(r, list) or len(r) != len(msg["columns"]):
            errors.append(f"rows[{i}]: wrong width")


def _v_clear(msg: dict, errors: list[str]) -> None:
    if msg["scope"] not in CLEAR_SCOPES or msg["reason"] not in CLEAR_REASONS:
        errors.append("panel.clear: bad scope or reason")


_EXTRA_CHECKS: dict[str, Callable[[dict, list[str]], None]] = {
    "panel.research": _v_research,
    "panel.files": _v_files,
    "panel.code": _v_code,
    "panel.update": _v_update,
    "panel.chart": _v_chart,
    "panel.table": _v_table,
    "panel.vision": _v_vision,
    "panel.theory": _v_theory,
    "panel.art": _v_art,
    "panel.camera": _v_camera,
    "panel.camera_status": lambda m, e: None,
    "panel.camera_open": lambda m, e: None,
    "panel.images": lambda m, e: _v_images(m["images"], e),
    "panel.clear": _v_clear,
    "panel.conversations": lambda m, e: _v_conversations(m, e),
    "panel.memory_used": lambda m, e: (
        [_check(MEMORY_ITEM_FIELDS, it, f"items[{i}]", e) for i, it in enumerate(m["items"][:20])]
        and None
    ),
}


def _v_conversations(msg: dict, errors: list[str]) -> None:
    for i, c in enumerate(msg["conversations"]):
        _check(CONV_FIELDS, c, f"conversations[{i}]", errors)
    for i, p in enumerate(msg["projects"]):
        _check(PROJECT_FIELDS, p, f"projects[{i}]", errors)


def validate(msg: Any) -> list[str]:
    """Schema check of one published message; returns a list of problems (empty = conformant)."""
    errors: list[str] = []
    if not isinstance(msg, dict) or msg.get("type") not in SCHEMAS:
        return ["unknown panel type"]
    _check({**COMMON, **SCHEMAS[msg["type"]]}, msg, "msg", errors)
    if errors:
        return errors
    _EXTRA_CHECKS[msg["type"]](msg, errors)
    return errors


def _count(value: Any) -> int:
    """The research agent reports dropped findings as a count; older results and the synthetic feed use a list."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    return len(value) if isinstance(value, list | tuple) else 0


def research_card(result: dict, images: list[dict] | None = None) -> dict:
    """A panel.research body from the research agent's result dict. `images` are already-cached [{image, alt, page_url, page_title}]."""
    sources = []
    index: dict[str, int] = {}
    for s in (result.get("sources") or [])[:MAX_SOURCES]:
        url = clean_url(s.get("url"))
        if not url or url in index:
            continue
        n = len(sources) + 1
        index[url] = n
        sources.append(
            {
                "n": n,
                "title": clean(s.get("title") or site_of(url), CAPS["title"]) or site_of(url),
                "site": clean(site_of(url), CAPS["site"]),
                "date": clean(s["date"], CAPS["date"]) if s.get("date") else None,
                "url": url,
            }
        )
    findings = []
    for f in (result.get("findings") or [])[:MAX_FINDINGS]:
        claim = clean_text(f.get("claim"), CAPS["claim"])
        n = index.get(clean_url(f.get("url")))
        if claim:
            item = {"n": len(findings) + 1, "claim": claim, "cite": [n] if n else []}
            if (
                "verified" in f
            ):  # the research agent checked the claim's quote against the page it cites
                item["verified"] = bool(f["verified"])
            findings.append(item)
    imgs = []
    for im in (images or [])[:MAX_IMAGES]:
        if IMAGE_REF.match(str(im.get("image"))):
            imgs.append(
                {
                    "n": len(imgs) + 1,
                    "image": im["image"],
                    "alt": clean(im.get("alt"), CAPS["alt"]),
                    "page_url": clean_url(im.get("page_url")),
                    "page_title": clean(im.get("page_title"), CAPS["title"]),
                }
            )
    return {
        "question": clean(result.get("question"), CAPS["question"]),
        "summary": clean_text(result.get("summary"), CAPS["summary"]),
        # the spoken summary is built only from the kept findings, so it cites exactly the sources they cite (card numbering)
        "summary_cite": sorted({c for f in findings for c in f["cite"]}),
        "findings": findings,
        "sources": sources,
        "images": imgs,
        "dropped": _count(result.get("dropped_findings")),
        "stopped": clean(result["stopped"], CAPS["stopped"]) if result.get("stopped") else None,
    }


def _home(path: str) -> str:
    import os

    home = os.path.expanduser("~")
    p = str(path or "")
    return "~" + p[len(home) :] if p == home or p.startswith(home + os.sep) else p


def _item(n: int, path: str, size: Any, kind: str = "file", was: str | None = None) -> dict:
    import os

    name = re.sub(r"^\d+__", "", os.path.basename(str(path).rstrip("/"))) if path else ""
    return {
        "n": n,
        "name": clean(name, CAPS["name"]),
        "dir": clean(_home(os.path.dirname(str(path).rstrip("/"))), CAPS["where"]),
        "size": size
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0
        else None,
        "kind": kind if kind in ("file", "folder") else "file",
        "was": clean(was, CAPS["name"]) if was else None,
    }


def _common_dir(paths: list[str]) -> str:
    import os

    dirs = [os.path.dirname(str(p).rstrip("/")) for p in paths if p]
    try:
        return _home(os.path.commonpath(dirs)) if dirs else ""
    except ValueError:
        return ""


PLAN_TITLES = {
    "move": "Move",
    "copy": "Copy",
    "rename": "Rename",
    "create": "Create",
    "trash": "Move to the trash:",
    "purge": "Empty the trash",
}


def files_card_from_plan(plan: dict, message: str = "", skipped: int = 0) -> dict:
    """The body of a panel.files card for a pending file plan (ops are the agent's own records; everything is cleaned here)."""
    action = str(plan.get("action") or "")
    ops = [o for o in plan.get("ops") or [] if isinstance(o, dict)][:MAX_ITEMS]
    items = []
    for i, o in enumerate(ops, 1):
        size = (o.get("fingerprint") or {}).get("size")
        if action in ("trash", "purge"):
            items.append(_item(i, o.get("from") or o.get("to") or "", size))
        elif action == "rename":
            items.append(
                _item(i, o.get("to") or "", size, was=str(o.get("from") or "").rsplit("/", 1)[-1])
            )
        else:
            items.append(_item(i, o.get("to") or "", size))
    n = len(plan.get("ops") or [])
    noun = "file" if n == 1 else "files"
    if action in ("trash", "purge"):
        where = _common_dir([o.get("from") or "" for o in ops]) if action == "trash" else "Trash"
    elif action in ("move", "copy"):
        src = _common_dir([o.get("from") or "" for o in ops])
        dst = _common_dir([o.get("to") or "" for o in ops])
        where = f"{src} → {dst}"
    else:
        where = _common_dir([o.get("to") or "" for o in ops])
    title = {
        "trash": f"Move {n} {noun} to the trash",
        "purge": f"Empty the trash ({n} {noun}, permanent)",
        "rename": "Rename 1 file" if n == 1 else f"Rename {n} files",
        "create": "Create a text file" if n == 1 else f"Create {n} files",
    }.get(action, f"{PLAN_TITLES.get(action, action.title())} {n} {noun}")
    status = str(plan.get("status") or "pending")
    return {
        "title": clean(title, CAPS["title"]),
        "kind": "plan",
        "action": clean(action, 20),
        "status": status if status in FILE_STATUSES else "pending",
        "plan_id": clean(plan.get("id"), 40) or None,
        "where": clean(where, CAPS["where"]),
        "summary": clean_text(message, CAPS["summary"]),
        "all_items": items,
        "total": n,
        "total_bytes": plan.get("total_bytes")
        if isinstance(plan.get("total_bytes"), int)
        else None,
        "skipped": int(skipped or 0),
    }


def files_card_from_listing(data: dict, message: str = "") -> dict:
    """The body of a panel.files card for a folder listing or a find result."""
    raw = [i for i in data.get("items") or [] if isinstance(i, dict)][:MAX_ITEMS]
    items = []
    for i, it in enumerate(raw, 1):
        path = it.get("path") or it.get("relative_path") or it.get("name") or ""
        if "path" not in it and it.get("relative_path"):
            path = it["relative_path"]
        items.append(_item(i, str(path), it.get("size_bytes"), it.get("kind") or "file"))
    where = _home(str(data.get("path") or "")) or ""
    return {
        "title": clean(
            "Folder " + (where.rsplit("/", 1)[-1] or where) if where else "Files found",
            CAPS["title"],
        ),
        "kind": "listing",
        "action": None,
        "status": "listing",
        "plan_id": None,
        "where": clean(where, CAPS["where"]),
        "summary": clean_text(message, CAPS["summary"]),
        "all_items": items,
        "agent_more": bool(data.get("truncated")),
        "total": len(items),
        "total_bytes": None,
        "skipped": 0,
    }


class PanelHub:
    """Builds, validates, remembers (last HISTORY cards) and publishes panel messages through an EventHub."""

    def __init__(self, hub: Any, base: str = "", clock: Callable = time.time) -> None:
        self.hub = hub
        self.base = base  # the panel file server's origin + token path, sent to clients in `hello`
        self.clock = clock
        self.cards: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self._rest: dict[str, list] = {}  # file cards: rows not shown yet
        self.quiet = False  # set while "show more" re-lists through the file agent
        self._cut: dict[str, bool] = {}  # file cards whose agent listing was cut short
        self.list_ctx: dict[
            str, dict
        ] = {}  # folder-listing cards the agent cut short: where to list further
        self.seq = 0
        self._lock = threading.Lock()
        self.conv_fn: Callable[[], int | None] | None = (
            None  # the current conversation's id: each card is kept and shown per conversation
        )
        self.conv_state: dict | None = None
        hub.panel = self

    def _keep(self) -> None:
        """Called under the lock: the last PER_CONVERSATION cards of each conversation, at most MAX_KEPT in all (oldest go first)."""
        per: dict = {}
        for pid, c in reversed(list(self.cards.items())):
            k = c.get("conv")
            per[k] = per.get(k, 0) + 1
            if per[k] > PER_CONVERSATION:
                del self.cards[pid]
        while len(self.cards) > MAX_KEPT:
            self.cards.popitem(last=False)

    def _next(self) -> tuple[int, str]:
        with self._lock:
            self.seq += 1
            return self.seq, f"c{self.seq}-{int(self.clock())}"

    def _emit(self, msg: dict) -> dict | None:
        if msg.get("type") in CARD_TYPES and self.conv_fn is not None and "conv" not in msg:
            try:
                cid = self.conv_fn()
            except Exception:
                cid = None
            if isinstance(cid, int):
                msg["conv"] = cid
        problems = validate(msg)
        raw = json.dumps(msg, separators=(",", ":"))
        if not problems and len(raw.encode()) > MAX_BYTES:
            problems = ["message over size cap"]
        if problems:
            self._log(problems)
            return None
        self.hub._send(msg)
        return msg

    @staticmethod
    def _log(problems: list[str]) -> None:
        try:
            from julius import log

            log.event("panel_dropped", problems=problems[:5])
            log.console(
                f"  [side panel: a card was not shown ({'; '.join(problems[:3])})]", flush=True
            )
        except Exception:  # logging must never break a turn
            pass

    def _trim(self, body: dict) -> dict:
        """Shrink a body that would exceed MAX_BYTES: drop trailing images, then findings, then sources, and say so."""
        for key in ("images", "findings", "sources"):
            while (
                body[key]
                and len(json.dumps(body, separators=(",", ":")).encode()) > MAX_BYTES - 2000
            ):
                body[key].pop()
                body["truncated"] = True
        return body

    def research(
        self, result: dict, images: list[dict] | None = None, turn: int | None = None
    ) -> str | None:
        try:
            body = self._trim(research_card(result, images))
        except Exception as e:  # a malformed result must never break a spoken turn
            self._log([f"research_card failed: {type(e).__name__}"])
            return None
        seq, pid = self._next()
        msg = {
            "type": "panel.research",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": turn,
            "seq": seq,
            **body,
        }
        if not self._emit(msg):
            return None
        with self._lock:
            self.cards[pid] = msg
            self._keep()
        return pid

    # ---- chart, table and image-gallery cards -----------------------------------------------------
    def _publish(self, type_: str, body: dict, turn: int | None) -> str | None:
        seq, pid = self._next()
        msg = {
            "type": type_,
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": turn,
            "seq": seq,
            **body,
        }
        if not self._emit(msg):
            return None
        with self._lock:
            self.cards[pid] = msg
            self._keep()
        return pid

    def chart(self, card: dict, turn: int | None = None) -> str | None:
        """A chart card: `image` is a panel file reference to the drawn PNG, `labels` / `series` the data behind it (shown as a table on demand)."""
        body = {
            "title": clean(card.get("title"), CAPS["title"]),
            "kind": str(card.get("kind") or ""),
            "unit": clean(card.get("unit"), 20),
            "image": str(card.get("image") or ""),
            "alt": clean(card.get("alt"), CAPS["alt"]),
            "labels": [clean(x, 40) for x in (card.get("labels") or [])[:30]],
            "series": [
                {
                    "name": clean(s.get("name"), 40),
                    "values": [float(v) for v in s.get("values", [])[:30]],
                }
                for s in (card.get("series") or [])[:5]
            ],
        }
        return self._publish("panel.chart", body, turn)

    def vision(self, card: dict, turn: int | None = None) -> str | None:
        """A vision card: one look (camera, window, screen or dropped image) and Julius's answer. `image` is a panel file reference to an in-memory picture."""
        source = str(card.get("source") or "")
        body = {
            "title": clean(card.get("title") or "What Julius saw", CAPS["title"]),
            "source": source if source in VISION_SOURCES else "image",
            "question": clean(card.get("question"), CAPS["question"]),
            "answer": clean_text(card.get("answer"), CAPS["summary"]),
            "image": str(card.get("image") or ""),
            "alt": clean(card.get("alt"), CAPS["alt"]),
            "model": clean(card.get("model"), 40),
            "cost_usd": round(float(card.get("cost_usd") or 0.0), 6),
            "est_cost_usd": round(float(card.get("est_cost_usd") or 0.0), 6),
        }
        return self._publish("panel.vision", body, turn)

    def art(self, card: dict, turn: int | None = None) -> str | None:
        """An art card: the picture before and after (pixel art), the settings used and the palette. The four images are panel file references to in-memory pictures:
        `before` (a downsized copy of the original), `after` (what the card shows), `export` (the PNG that "save this card" writes) and `small` (the same at 1x)."""
        kind = str(card.get("kind") or "")
        body = {
            "title": clean(card.get("title") or "Pixel art", CAPS["title"]),
            "kind": kind if kind in ART_KINDS else "pixel",
            "before": str(card.get("before") or ""),
            "after": str(card.get("after") or ""),
            "compare": str(card.get("compare") or ""),
            "export": str(card.get("export") or ""),
            "small": str(card.get("small") or ""),
            "alt": clean(card.get("alt"), CAPS["alt"]),
            "summary": clean(card.get("summary"), CAPS["summary"]),
            "settings": [clean(x, 80) for x in (card.get("settings") or [])[:9]],
            "palette": [
                str(c).upper()
                for c in (card.get("palette") or [])[:MAX_ART_PALETTE]
                if HEX6.match(str(c).upper())
            ],
            "editing": bool(card.get("editing")),
            "sprite_id": re.sub(r"[^0-9a-f]", "", str(card.get("sprite_id") or ""))[:16],
            "main": bool(card.get("main")),
        }
        if reveal_ok(card.get("reveal")):
            body["reveal"] = card["reveal"]
        return self._publish("panel.art", body, turn)

    def _other_conversation(self, card: dict) -> bool:
        if self.conv_fn is None or card.get("conv") is None:
            return False
        try:
            return card.get("conv") != self.conv_fn()
        except Exception:
            return False

    def code(self, card: dict, turn: int | None = None) -> str | None:
        """A coding task's changes: the changed files and the unified diff of the scratch copy (cut to MAX_DIFF_LINES lines of 200 characters)."""
        body = {
            "title": clean(card.get("title") or "Code changes", CAPS["title"]),
            "project": clean(card.get("project"), CAPS["title"]),
            "summary": clean(card.get("summary"), CAPS["summary"]),
            "files": [
                {"path": clean(f.get("path"), 200), "status": str(f.get("status"))}
                for f in (card.get("files") or [])[:40]
                if isinstance(f, dict) and f.get("status") in CODE_STATUSES
            ],
            "diff": [
                str(x).replace("\t", "    ")[:200]
                for x in (card.get("diff") or [])[:MAX_DIFF_LINES]
            ],
        }
        if card.get("tests"):
            body["tests"] = clean(card["tests"], 200)
        return self._publish("panel.code", body, turn)

    def update_art(self, target: str, fields: dict) -> bool:
        """A sprite card after an edit: new pictures, settings and palette in place (one card, not one per edit); folded into the remembered card."""
        with self._lock:
            card = self.cards.get(target)
        if card is None or card.get("type") != "panel.art":
            return False
        if self._other_conversation(
            card
        ):  # reopened in another conversation: that one gets its own card
            return False
        patch: dict[str, Any] = {}
        for k in ("after", "export", "small"):
            if k in fields and IMAGE_REF.match(str(fields[k])):
                patch[k] = str(fields[k])
        if "settings" in fields:
            patch["settings"] = [clean(x, 80) for x in (fields.get("settings") or [])[:9]]
        if "palette" in fields:
            patch["palette"] = [
                str(c).upper()
                for c in fields["palette"][:MAX_ART_PALETTE]
                if HEX6.match(str(c).upper())
            ]
        if "summary" in fields:
            patch["summary"] = clean(fields["summary"], CAPS["summary"])
        if "editing" in fields:
            patch["editing"] = bool(fields["editing"])
        if "main" in fields:
            patch["main"] = bool(fields["main"])
        seq, pid = self._next()
        msg = {
            "type": "panel.update",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": card.get("turn"),
            "seq": seq,
            "target": target,
            **patch,
        }
        if not self._emit(msg):
            return False
        with self._lock:
            card.update(patch)
        return True

    def camera(self, card: dict, turn: int | None = None) -> str | None:
        """The still from the orb's camera window ("Use Photo"): `image` is a panel file reference to the in-memory photo."""
        body = {
            "title": clean(card.get("title") or "Photo", CAPS["title"]),
            "image": str(card.get("image") or ""),
            "note": clean(card.get("note"), CAPS["note"]),
        }
        return self._publish("panel.camera", body, turn)

    def camera_open(self, autostart: bool = False, auto_use_s: int = 0) -> bool:
        """Ask the orb to open its camera window (a native window, not a card). `autostart` starts the 3-second countdown at once; `auto_use_s` > 0 means a request
        continues after the shot, so the photo is used by itself that many seconds after the shot when nobody presses Retake or Use Photo."""
        seq, pid = self._next()
        msg = {
            "type": "panel.camera_open",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": None,
            "seq": seq,
            "autostart": bool(autostart),
            "auto_use_s": max(0, min(30, int(auto_use_s))),
        }
        return self._emit(msg) is not None

    def set_camera_status(self, enabled: bool, reason: str = "", cause: str = "") -> None:
        """Whether the camera button can be used, and why not: switched off in settings, or permission denied. Not a card: the orb enables or disables its camera buttons from it,
        and a reconnecting client gets the latest in the hello snapshot."""
        reason = clean(reason, 160)
        cause = cause if cause in ("settings", "permission") else ""
        state = {"enabled": bool(enabled), "reason": reason, "cause": cause}
        with self._lock:
            if getattr(self, "camera_state", None) == state:
                return
        seq, pid = self._next()
        msg = {
            "type": "panel.camera_status",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": None,
            "seq": seq,
            **state,
        }
        if self._emit(msg):
            with self._lock:
                self.camera_state = state

    def theory(self, card: dict, turn: int | None = None) -> str | None:
        """A theory card: a key, scale, chord or progression with computed note names and MIDI numbers, drawn by the UI as a piano and a chord chart."""
        kind = str(card.get("kind") or "")
        chords = []
        for c in (card.get("chords") or [])[:MAX_THEORY_CHORDS]:
            chords.append(
                {
                    "symbol": clean(c.get("symbol"), 24),
                    "roman": clean(c.get("roman"), 16),
                    "label": clean(c.get("label"), 40),
                    "notes": [clean(n, 6) for n in (c.get("notes") or [])[:8]],
                    "midi": [int(m) for m in (c.get("midi") or [])[:8] if 21 <= int(m) <= 108],
                    "root": int(c.get("root") or 0) % 12,
                }
            )
        body = {
            "title": clean(card.get("title") or "Theory", CAPS["title"]),
            "kind": kind if kind in THEORY_KINDS else "key",
            "key": clean(card.get("key"), 40),
            "scale_notes": [clean(n, 6) for n in (card.get("scale_notes") or [])[:12]],
            "chords": chords,
        }
        return self._publish("panel.theory", body, turn)

    def table(self, card: dict, turn: int | None = None) -> str | None:
        body = {
            "title": clean(card.get("title"), CAPS["title"]),
            "unit": clean(card.get("unit"), 20),
            "columns": [clean(c, 60) for c in (card.get("columns") or [])[:8]],
            "rows": [[clean(c, 80) for c in r] for r in (card.get("rows") or [])[:50]],
        }
        return self._publish("panel.table", body, turn)

    def images(
        self, title: str, question: str, images: list[dict], turn: int | None = None
    ) -> str | None:
        """A gallery card from already-cached images [{image, alt, page_url, page_title}] (the research images, shown larger)."""
        body = {
            "title": clean(title, CAPS["title"]),
            "question": clean(question, CAPS["question"]),
            "images": [
                {
                    "n": i + 1,
                    "image": im["image"],
                    "alt": clean(im.get("alt"), CAPS["alt"]),
                    "page_url": clean_url(im.get("page_url")),
                    "page_title": clean(im.get("page_title"), CAPS["title"]),
                }
                for i, im in enumerate(images[:MAX_IMAGES])
                if IMAGE_REF.match(str(im.get("image")))
            ],
        }
        return self._publish("panel.images", body, turn) if body["images"] else None

    # ---- file cards (previews of file plans, folder listings) ------------------------------------
    def files(self, card: dict, turn: int | None = None) -> str | None:
        """Publish a file card. `card` is the output of `files_card` (its `all_items` stay here for "show more")."""
        card = dict(card)
        rest = card.pop("all_items")
        cut = bool(
            card.pop("agent_more", False)
        )  # the agent itself stopped listing: more exists beyond what it returned
        first, rest = rest[:ITEM_PAGE], rest[ITEM_PAGE:]
        card.update(items=first, shown=len(first), more=bool(rest) or cut)
        seq, pid = self._next()
        msg = {
            "type": "panel.files",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": turn,
            "seq": seq,
            **card,
        }
        if not self._emit(msg):
            return None
        with self._lock:
            self.cards[pid] = msg
            self._rest[pid] = rest
            self._cut[pid] = cut
            for old in list(self._rest):
                if old not in self.cards:
                    del self._rest[old]
            self._keep()
        return pid

    def show_more(self, target: str, n: int = ITEM_PAGE) -> int:
        """Reveal the next `n` rows of a file card. -> how many were added (0 when there is nothing more or no such card)."""
        with self._lock:
            card, rest = self.cards.get(target), self._rest.get(target) or []
        if card is None or card.get("type") != "panel.files" or not rest:
            return 0
        more, rest = rest[:n], rest[n:]
        if not self._push(
            target,
            items=more,
            shown=card["shown"] + len(more),
            more=bool(rest) or self._cut.get(target, False),
            fold_items=more,
        ):
            return 0
        with self._lock:
            self._rest[target] = rest
        return len(more)

    def append_rows(self, target: str, rows: list[dict], more: bool) -> int:
        """Rows the agent listed beyond its first answer (a bigger re-list): numbered after the existing ones, all shown at once."""
        with self._lock:
            card = self.cards.get(target)
        if card is None or card.get("type") != "panel.files":
            return 0
        have = card["shown"]
        rows = [dict(r, n=have + i + 1) for i, r in enumerate(rows[: MAX_ITEMS - have])]
        if not rows:
            return 0
        added = 0
        self._cut[target] = more
        for i in range(0, len(rows), 40):  # chunks keep each message under the size cap
            chunk = rows[i : i + 40]
            last = i + 40 >= len(rows)
            if not self._push(
                target,
                items=chunk,
                shown=have + added + len(chunk),
                more=more if last else True,
                fold_items=chunk,
            ):
                break
            added += len(chunk)
        return added

    def has_more(self, target: str) -> bool:
        with self._lock:
            return bool(self._rest.get(target))

    def set_status(self, target: str, status: str, summary: str | None = None) -> bool:
        return self._push(target, status=status, summary=summary)

    def _push(self, target: str, fold_items: list | None = None, **patch: Any) -> bool:
        """A panel.update carrying `patch`, folded into the remembered card (so a reconnect sees the same card)."""
        with self._lock:
            card = self.cards.get(target)
        if card is None:
            return False
        patch = {k: v for k, v in patch.items() if v is not None}
        if "summary" in patch:
            patch["summary"] = clean_text(patch["summary"], CAPS["summary"])
        seq, pid = self._next()
        msg = {
            "type": "panel.update",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": card.get("turn"),
            "seq": seq,
            "target": target,
            **patch,
        }
        if not self._emit(msg):
            return False
        with self._lock:
            for k, v in patch.items():
                if k == "items":
                    card["items"] = card["items"] + fold_items
                else:
                    card[k] = v
        return True

    def newest(self, type_: str, status: str | None = None) -> dict | None:
        with self._lock:
            cards = list(self.cards.values())
        for c in reversed(cards):
            if c.get("type") == type_ and (status is None or c.get("status") == status):
                return c
        return None

    def update(
        self, target: str, images: list[dict] | None = None, note: str | None = None
    ) -> bool:
        """Late-arriving pieces for an existing card (fetched images); also folded into the remembered card for reconnects."""
        with self._lock:
            card = self.cards.get(target)
        if card is None:
            return False
        seq, pid = self._next()
        patch: dict[str, Any] = {}
        if images is not None:
            have = len(card["images"])
            patch["images"] = [
                {
                    "n": have + i + 1,
                    "image": im["image"],
                    "alt": clean(im.get("alt"), CAPS["alt"]),
                    "page_url": clean_url(im.get("page_url")),
                    "page_title": clean(im.get("page_title"), CAPS["title"]),
                }
                for i, im in enumerate(images[: MAX_IMAGES - have])
                if IMAGE_REF.match(str(im.get("image")))
            ]
        if note is not None:
            patch["note"] = clean(note, CAPS["note"])
        msg = {
            "type": "panel.update",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": card.get("turn"),
            "seq": seq,
            "target": target,
            **patch,
        }
        if not self._emit(msg):
            return False
        with self._lock:
            card["images"] = card["images"] + patch.get("images", [])
        return True

    def clear(self, target: str | None = None, reason: str = "done") -> None:
        seq, pid = self._next()
        msg = {
            "type": "panel.clear",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": None,
            "seq": seq,
            "scope": "card" if target else "all",
            "target": target,
            "reason": reason,
        }
        if not self._emit(msg):
            return
        with self._lock:
            if target:
                self.cards.pop(target, None)
            else:
                self.cards.clear()

    def snapshot(self) -> dict:
        """The `hello` extras: remembered cards (oldest first) and the file-server base."""
        with self._lock:
            out = {"cards": list(self.cards.values()), "base": self.base}
            if getattr(self, "camera_state", None):
                out["camera_status"] = dict(self.camera_state)
            if self.conv_state:
                out["conversations"] = dict(self.conv_state)
            return out

    def conversations(self, state: dict) -> bool:
        """The conversation list for the sidebar (not a card): sent on every change and in the hello snapshot."""
        body = {
            "current": state.get("current") if isinstance(state.get("current"), int) else None,
            "title": clean(state.get("title"), CAPS["title"]),
            "project": clean(state.get("project"), 60),
            "projects": [
                {
                    "id": int(p["id"]),
                    "name": clean(p.get("name"), 60),
                    "pinned": bool(p.get("pinned")),
                }
                for p in (state.get("projects") or [])[:50]
            ],
            "conversations": [
                {
                    "id": int(c["id"]),
                    "title": clean(c.get("title"), 80),
                    "project": clean(c.get("project"), 60),
                    "pinned": bool(c.get("pinned")),
                    "updated": float(c.get("updated") or 0),
                    "n": int(c.get("n") or 0),
                }
                for c in (state.get("conversations") or [])[:150]
            ],
            "trash": int(state.get("trash") or 0),
            "messages": [
                {
                    "who": "you" if m.get("who") == "you" else "julius",
                    "text": clean(m.get("text"), 400),
                }
                for m in (state.get("messages") or [])[-12:]
                if isinstance(m, dict)
            ],
        }
        seq, pid = self._next()
        msg = {
            "type": "panel.conversations",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": None,
            "seq": seq,
            **body,
        }
        if not self._emit(msg):
            return False
        with self._lock:
            self.conv_state = msg
        return True

    def memory_used(self, items: list[dict], turn: int | None = None) -> bool:
        """The notes the brain was given for this turn, each with its scope (the "Memory used" chip). Not kept."""
        body = {
            "items": [
                {
                    "id": int(it["id"]),
                    "scope": it["scope"]
                    if it.get("scope") in ("global", "project", "conversation")
                    else "global",
                    "label": clean(it.get("label"), 60),
                    "text": clean(it.get("text"), 300),
                }
                for it in items[:12]
            ]
        }
        seq, pid = self._next()
        msg = {
            "type": "panel.memory_used",
            "t": round(self.clock(), 3),
            "panel_id": pid,
            "turn": turn,
            "seq": seq,
            **body,
        }
        return self._emit(msg) is not None

    def lookup(self, panel_id: str | None = None) -> dict | None:
        """A remembered card (the newest when no id) for typed commands like "open source 3"."""
        with self._lock:
            if panel_id:
                return self.cards.get(panel_id)
            return next(reversed(self.cards.values()), None)
