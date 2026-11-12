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

HISTORY = 10
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
COMMON = {"type": "str", "t": "num", "panel_id": "str", "turn": "int?", "seq": "int"}
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
    "panel.update": {
        "target": "str",
        "images": "list?",
        "note": "str?",
        "items": "list?",
        "status": "str?",
        "shown": "int?",
        "more": "bool?",
        "summary": "str?",
    },
    "panel.clear": {"scope": "str", "target": "str?", "reason": "str"},
}
CLEAR_SCOPES = ("all", "card")
CLEAR_REASONS = ("new_topic", "dismissed", "expired", "done")
FINDING_FIELDS = {"n": "int", "claim": "str", "cite": "list"}
SOURCE_FIELDS = {"n": "int", "title": "str", "site": "str", "date": "str?", "url": "str"}
ITEM_FIELDS = {
    "n": "int",
    "name": "str",
    "dir": "str",
    "size": "int?",
    "kind": "str",
    "was": "str?",
}
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


def validate(msg: Any) -> list[str]:
    """Schema check of one published message; returns a list of problems (empty = conformant)."""
    errors: list[str] = []
    if not isinstance(msg, dict) or msg.get("type") not in SCHEMAS:
        return ["unknown panel type"]
    _check({**COMMON, **SCHEMAS[msg["type"]]}, msg, "msg", errors)
    if errors:
        return errors
    if msg["type"] == "panel.research":
        for i, f in enumerate(msg["findings"]):
            _check(FINDING_FIELDS, f, f"findings[{i}]", errors)
        for i, s in enumerate(msg["sources"]):
            _check(SOURCE_FIELDS, s, f"sources[{i}]", errors)
            if isinstance(s, dict) and not clean_url(s.get("url")):
                errors.append(f"sources[{i}].url: not http(s)")
        for i, im in enumerate(msg["images"]):
            _check(IMAGE_FIELDS, im, f"images[{i}]", errors)
            if isinstance(im, dict) and not IMAGE_REF.match(str(im.get("image"))):
                errors.append(f"images[{i}].image: not a panel file reference")
    if msg["type"] == "panel.files":
        if msg["status"] not in FILE_STATUSES:
            errors.append("panel.files: bad status")
        for i, it in enumerate(msg["items"]):
            _check(ITEM_FIELDS, it, f"items[{i}]", errors)
    if msg["type"] == "panel.update":
        for i, it in enumerate(msg.get("items") or []):
            _check(ITEM_FIELDS, it, f"items[{i}]", errors)
        if msg.get("status") is not None and msg["status"] not in FILE_STATUSES:
            errors.append("panel.update: bad status")
    if msg["type"] == "panel.clear" and (
        msg["scope"] not in CLEAR_SCOPES or msg["reason"] not in CLEAR_REASONS
    ):
        errors.append("panel.clear: bad scope or reason")
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
            findings.append({"n": len(findings) + 1, "claim": claim, "cite": [n] if n else []})
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
        hub.panel = self

    def _next(self) -> tuple[int, str]:
        with self._lock:
            self.seq += 1
            return self.seq, f"c{self.seq}-{int(self.clock())}"

    def _emit(self, msg: dict) -> dict | None:
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
            while len(self.cards) > HISTORY:
                self.cards.popitem(last=False)
        return pid

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
            while len(self.cards) > HISTORY:
                self.cards.popitem(last=False)
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
            return {"cards": list(self.cards.values()), "base": self.base}

    def lookup(self, panel_id: str | None = None) -> dict | None:
        """A remembered card (the newest when no id) for typed commands like "open source 3"."""
        with self._lock:
            if panel_id:
                return self.cards.get(panel_id)
            return next(reversed(self.cards.values()), None)
