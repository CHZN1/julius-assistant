"""Wires the side panel into the running service and holds what the typed commands do with a card.

- `start_panel`: the panel file server and PanelHub, started next to the event feed (config `[ui] panel`, `panel_port`, `panel_max_mb`). Never raises: no panel is better than no Julius.
- `make_sink`: what the research agent calls with a finished result. The card goes out at once (the spoken answer is not delayed by it); images are fetched, re-encoded and stored in the
  background and arrive later as a `panel.update`. Every failure here is logged and swallowed.
- `open_source` / `save_card` / `close_panel`: the commands the panel's buttons and the voice share ("open source 3", "open source 2 from the research on <question>", "save this card",
  "close the panel"). They act on the stored card, never on a URL or path taken from the spoken or typed words, and every URL is http(s) only."""

import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from julius import log
from julius.ui_bridge import panel as pn
from julius.ui_bridge import panelfiles as pf

NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    )
}
DEFAULT_SAVED_DIR = "~/Julius Saved"


# ---- startup -------------------------------------------------------------------------------------
def start_panel(hub: Any, ui_cfg: dict, root: Path) -> tuple[Any, Any, Any] | None:
    """-> (PanelHub, PanelFiles, PanelFileServer), or None when the panel is off or its port is taken."""
    if hub is None or not (ui_cfg or {}).get("panel", True):
        return None
    try:
        files = pf.PanelFiles(Path(root), max_mb=int((ui_cfg or {}).get("panel_max_mb", 200)))
        server = pf.PanelFileServer(
            files, int((ui_cfg or {}).get("panel_port", pf.DEFAULT_PORT))
        ).start()
        return pn.PanelHub(hub, base=server.base), files, server
    except Exception as e:  # port taken, directory not writable: Julius starts without the panel
        log.console(f"  [side panel unavailable: {type(e).__name__}: {e}]", flush=True)
        return None


def make_sink(
    panel: Any,
    files: Any,
    turn_fn: Callable[[], int] = lambda: 0,
    fetcher_factory: Callable | None = None,
) -> Callable[[dict], None]:
    """The function the research agent calls with each finished result."""

    def fetch_images(pid: str, result: dict) -> None:
        try:
            titles = {s["url"]: s.get("title", "") for s in result.get("sources", [])}
            cands = [
                {
                    "url": i["url"],
                    "alt": i.get("alt", ""),
                    "page": i.get("page", ""),
                    "page_title": titles.get(i.get("page", ""), ""),
                }
                for i in result.get("images", [])
            ]
            if not cands:
                return
            if fetcher_factory:
                fetcher = fetcher_factory()
            else:
                from julius.agents.research import Fetcher

                fetcher = Fetcher()
            got = pf.cache_images(fetcher, files, cands, log=log.event)
            if got:
                panel.update(pid, images=got)
        except Exception as e:
            log.event("panel_images_failed", error=type(e).__name__)

    def sink(result: dict) -> None:
        try:
            pid = panel.research(result, images=None, turn=turn_fn())
            if pid and result.get("images"):
                threading.Thread(
                    target=fetch_images, args=(pid, result), name="panel-images", daemon=True
                ).start()
        except Exception as e:
            log.event("panel_emit_failed", error=type(e).__name__)
            log.console(
                f"  [side panel: could not show the research card ({type(e).__name__})]", flush=True
            )

    return sink


def make_file_sink(panel: Any, turn_fn: Callable[[], int] = lambda: 0) -> Callable[[dict], None]:
    """What the file agent calls with each reply (`file_common.set_panel_sink`): a pending file plan becomes a card, a finished /
    cancelled / undone plan updates its card's status, a folder listing or find result becomes a list card. Never raises."""

    def card_for(plan_id: str | None) -> dict | None:
        if not plan_id:
            return None
        with panel._lock:
            cards = list(panel.cards.values())
        for c in reversed(cards):
            if c.get("type") == "panel.files" and c.get("plan_id") == plan_id:
                return c
        return None

    def mark(card: dict | None, status: str, summary: str | None = None) -> None:
        if card is not None and card.get("status") != status:
            panel.set_status(card["panel_id"], status, summary)

    def sink(res: dict) -> None:
        if getattr(
            panel, "quiet", False
        ):  # "show more" re-lists through the agent: that answer extends a card, it is not a new one
            return
        try:
            code, data = res.get("code"), res.get("data") or {}
            plan, msg = data.get("plan"), res.get("message") or ""
            if code == "preview" and plan and plan.get("ops"):
                for old in [c for c in _cards(panel) if c.get("status") == "pending"]:
                    mark(old, "expired")  # a newer plan replaces the one still waiting
                body = pn.files_card_from_plan(plan, msg, skipped=len(data.get("skipped") or []))
                panel.files(body, turn=turn_fn())
            elif code in ("completed", "purged") and plan:
                mark(card_for(plan.get("id")), "completed", msg)
            elif code == "partial" and plan:
                mark(card_for(plan.get("id")), "partial", msg)
            elif code == "undone" and plan:
                mark(card_for(plan.get("id")), "undone", msg)
            elif code == "undo_partial" and plan:
                mark(card_for(plan.get("id")), "partial", msg)
            elif code == "cancelled":
                pending = [c for c in _cards(panel) if c.get("status") == "pending"]
                mark(pending[-1] if pending else None, "cancelled")
            elif code in ("listed", "found") and data.get("items"):
                body = pn.files_card_from_listing(data, msg)
                pid = panel.files(body, turn=turn_fn())
                if pid and data.get("path") and data.get("truncated"):
                    panel.list_ctx[pid] = {"path": str(data["path"]), "limit": len(data["items"])}
        except Exception as e:  # a panel problem must never cost the spoken answer
            log.event("panel_files_failed", error=type(e).__name__)

    return sink


def _cards(panel: Any) -> list[dict]:
    with panel._lock:
        return [c for c in panel.cards.values() if c.get("type") == "panel.files"]


# ---- finding a card ------------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "on",
    "in",
    "about",
    "for",
    "to",
    "and",
    "is",
    "was",
    "what",
    "how",
    "who",
    "where",
    "when",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def find_card(panel: Any, question: str | None = None) -> dict | None:
    """The newest card, or (given words from a question) the stored research card whose question overlaps them most."""
    if panel is None:
        return None
    if not question:
        return panel.lookup()
    want = _words(question)
    if not want:
        return None
    best, score = None, 0.0
    with panel._lock:
        cards = list(panel.cards.values())
    for c in reversed(cards):  # newest first wins ties
        if c.get("type") != "panel.research":
            continue
        have = _words(c.get("question", ""))
        s = len(want & have) / len(want)
        if s > score:
            best, score = c, s
    return best if score >= 0.5 else None


# ---- the commands ---------------------------------------------------------------------------------
_NUM = r"(\d{1,2}|" + "|".join(w for w in NUMBER_WORDS if w != "zero") + r")"
OPEN_RE = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?open\s+(?:the\s+)?(?:source|link|citation)\s*(?:number\s+|no\.?\s*|#\s*)?"
    + _NUM
    + r"(?:\s+(?:from|of|in)\s+(?:the\s+)?(?:research|search|card|results?)\s+(?:on|about|for|into)\s+(.+?))?[\s.!?]*$",
    re.I,
)
SAVE_RE = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?save\s+(?:this|that|the\s+(?:latest|last|current|research))?\s*(?:card|research|panel)[\s.!?]*$",
    re.I,
)
MORE_RE = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:show|give|see|list)(?:\s+me)?\s+(?:some\s+|a\s+few\s+)?(?:more|the\s+rest|the\s+remaining|the\s+other\s+ones|the\s+next\s+(?:few|ones|batch)|everything(?:\s+else)?)"
    r"(?:\s+(?:files?|items?|results?|rows?))?[\s.!?]*$",
    re.I,
)
IMAGES_RE = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:show|open|pull up|bring up)(?:\s+me)?\s+(?:the\s+)?(?:(?:research|card|search)\s+)?(?:images|pictures|photos)"
    r"(?:\s+(?:from|of|in)\s+(?:the\s+)?(?:research|search|card|results?)(?:\s+(?:on|about|for|into)\s+(.+?))?)?[\s.!?]*$",
    re.I,
)
CLOSE_RE = re.compile(
    r"^\W*(?:please\s+)?(?:close|hide|dismiss|clear)\s+the\s+(?:side\s+)?panel[\s.!?]*$", re.I
)


def _opener(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(
            ["open", url], check=False, timeout=10
        )  # a list, no shell; the URL comes from the stored card
    else:
        webbrowser.open(url)


def open_source(
    panel: Any, n: int, question: str | None = None, opener: Callable[[str], None] | None = None
) -> str:
    card = find_card(panel, question)
    if card is None:
        return (
            "I couldn't find that research card."
            if question
            else "I don't have a research card to open a source from."
        )
    sources = card.get("sources") or []
    if not 1 <= n <= len(sources):
        return (
            f"That card has {len(sources)} source{'s' if len(sources) != 1 else ''}."
            if sources
            else "That card has no sources."
        )
    url = pn.clean_url(sources[n - 1].get("url"))
    if (
        not url
    ):  # cannot happen for a card the hub built, but the check is the safety, not the assumption
        return "I can't open that one."
    try:
        (opener or _opener)(url)
    except Exception as e:
        log.event("panel_open_failed", error=type(e).__name__)
        return "I couldn't open the browser."
    log.event("panel_open_source", n=n, host=urlsplit(url).hostname)
    return f"Opening source {n}."


def close_panel(panel: Any) -> str:
    if panel is None:
        return "There's no panel."
    panel.clear(reason="dismissed")
    return "Closed the panel."


# ---- save this card ---------------------------------------------------------------------------------
class SaveRefused(Exception):
    pass


def saved_dir(cfg_value: str | None) -> Path:
    """The folder cards are saved to: local only. Must be absolute (after ~), and not inside iCloud Drive."""
    raw = str(cfg_value or DEFAULT_SAVED_DIR)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise SaveRefused(
            "The saved-cards folder in julius.toml has to be a full path, like ~/Julius Saved."
        )
    probe = p.resolve(strict=False)
    if "Mobile Documents" in probe.parts or "com~apple~CloudDocs" in probe.parts:
        raise SaveRefused(
            "The saved-cards folder is inside iCloud. Pick a local folder in julius.toml, under ui, saved_dir."
        )
    return p


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n].strip("-") or "card"


def card_markdown(card: dict, image_files: list[tuple[dict, str]]) -> str:
    """The card as a markdown file: question, summary, findings with [n] citations, sources as plain links, images with their captions."""
    out = [f"# {card.get('question') or 'Research'}", ""]
    saved_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(card.get("t") or time.time()))
    out += [f"*Researched {saved_at}.*", "", card.get("summary", ""), ""]
    if card.get("findings"):
        out += ["## Findings", ""]
        for f in card["findings"]:
            cites = "".join(f"[{c}]" for c in f.get("cite", [f.get("source")]) if c)
            out.append(f"- {f.get('claim', '')} {cites}".rstrip())
        out.append("")
    if card.get("sources"):
        out += ["## Sources", ""]
        for s in card["sources"]:
            bits = [s.get("site") or "", s.get("date") or ""]
            out.append(
                f"{s['n']}. {s.get('title', '')}: <{s['url']}>"
                + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "")
            )
        out.append("")
    if image_files:
        out += ["## Images", ""]
        for im, rel in image_files:
            cap = im.get("alt") or im.get("page_title") or ""
            out += [
                f"![{cap}]({rel})",
                f"*{cap}*" + (f" (from <{im['page_url']}>)" if im.get("page_url") else ""),
                "",
            ]
    return "\n".join(out).rstrip() + "\n"


def save_card(
    panel: Any, files: Any, cfg_value: str | None = None, now: Callable[[], float] = time.time
) -> str:
    """Writes the newest card (markdown plus its images) into its own folder under the saved-cards folder. Returns what Julius says."""
    card = find_card(panel)
    if card is None or card.get("type") != "panel.research":
        return "I don't have a card to save."
    try:
        base = saved_dir(cfg_value)
    except SaveRefused as e:
        return str(e)
    try:
        base.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H%M", time.localtime(now()))
        name = f"{stamp} {_slug(card.get('question', ''))}"
        folder = base / name
        k = 1
        while folder.exists():  # never overwrite an earlier save
            k += 1
            folder = base / f"{name} ({k})"
        if (
            folder.resolve().parent != base.resolve()
        ):  # the slug is [a-z0-9-] only, but the check is cheap
            return "I couldn't save that card."
        folder.mkdir()
        kept: list[tuple[dict, str]] = []
        if card.get("images"):
            (folder / "images").mkdir(exist_ok=True)
        for i, im in enumerate(card.get("images") or [], 1):
            src = files.local_path(im.get("image", "")) if files is not None else None
            if src is None:
                continue
            rel = f"images/{i}{src.suffix}"
            (folder / rel).write_bytes(src.read_bytes())
            kept.append((im, rel))
        (folder / "card.md").write_text(card_markdown(card, kept), encoding="utf-8")
    except OSError as e:
        log.event("panel_save_failed", error=type(e).__name__)
        return "I couldn't save that card."
    log.event("panel_card_saved", images=len(kept))
    return f"Saved to {base.name}, in a folder called {folder.name}."


def show_images(panel: Any, question: str | None = None) -> str:
    """ "Show the images": the images of a research card as a gallery card of their own (larger, pinnable, openable)."""
    card = find_card(panel, question)
    if card is not None and card.get("type") != "panel.research" and not question:
        with (
            panel._lock
        ):  # the newest card is a chart or a gallery: the images belong to the newest research card
            research = [c for c in panel.cards.values() if c.get("type") == "panel.research"]
        card = research[-1] if research else None
    if card is None or card.get("type") != "panel.research":
        return "I don't have a research card to show images from."
    imgs = card.get("images") or []
    if not imgs:
        return "That research has no images."
    pid = panel.images(
        "Images: " + (card.get("question") or "research"),
        card.get("question", ""),
        [
            {
                "image": i["image"],
                "alt": i.get("alt", ""),
                "page_url": i.get("page_url", ""),
                "page_title": i.get("page_title", ""),
            }
            for i in imgs
        ],
        turn=card.get("turn"),
    )
    return (
        f"Showing {len(imgs)} image{'s' if len(imgs) != 1 else ''}."
        if pid
        else "I couldn't show the images."
    )


def show_more(panel: Any, lister: Callable[[str, int], dict] | None = None) -> str | None:
    """ "Show more" on the newest file card: reveal its next rows, or ask the file agent to list further. None when the newest card is not a file card."""
    card = find_card(panel)
    if card is None or card.get("type") != "panel.files":
        return "There's nothing to show more of right now."
    pid = card["panel_id"]
    if panel.has_more(pid):
        k = panel.show_more(pid)
        left = card["total"] - card["shown"] if card.get("kind") == "plan" else None
        if k and left:
            return f"Showing {k} more. {left} still to show."
        return f"Showing {k} more." if k else "That's everything."
    ctx = getattr(panel, "list_ctx", {}).get(pid)
    if card.get("more") and ctx:
        new_limit = min(200, max(ctx["limit"], 40) * 2)
        if new_limit <= ctx["limit"]:
            return "That's the most I can list from one folder."
        if lister is None:
            from julius.agents import files as jf

            def lister(path: str, limit: int) -> dict:
                return jf.agent().list(path, limit=limit)

        panel.quiet = True
        try:
            res = lister(ctx["path"], new_limit)
        finally:
            panel.quiet = False
        data = res.get("data") or {}
        body = pn.files_card_from_listing(data)
        rows = body["all_items"][ctx["limit"] :]
        k = panel.append_rows(pid, rows, bool(data.get("truncated")))
        ctx["limit"] = len(data.get("items") or [])
        if not data.get("truncated"):
            panel.list_ctx.pop(pid, None)
        return f"Showing {k} more." if k else "That's everything."
    return "That's everything on the card."


def handle(
    panel: Any,
    files: Any,
    text: str,
    saved_cfg: str | None = None,
    opener: Callable[[str], None] | None = None,
) -> str | None:
    """The router's entry point: a reply for a panel command, or None for anything else."""
    t = (text or "").strip()
    if not t or len(t) > 300:
        return None
    m = OPEN_RE.match(t)
    if m:
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
        return open_source(panel, n, m.group(2), opener)
    if SAVE_RE.match(t):
        return save_card(panel, files, saved_cfg)
    if CLOSE_RE.match(t):
        return close_panel(panel)
    if MORE_RE.match(t):
        return show_more(panel)
    m = IMAGES_RE.match(t)
    if m:
        return show_images(panel, m.group(1))
    return None
