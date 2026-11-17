"""The conversations sidebar's actions (POST /v1/conversation): the same actions as the voice commands, by id.

Switching never happens under a turn (asked while Julius is answering, it happens when the turn ends). Delete puts the conversation in the
trash, and only after the sidebar's own confirm sheet (`confirm: true`); restore brings it back. Search is local (FTS5). Every action
answers with the new sidebar state, and the same state goes out on the feed (panel.conversations) so every window shows it."""

from __future__ import annotations

from typing import Any

from julius.conversations.manager import Conversations

_ACTIONS = {
    "list",
    "switch",
    "new",
    "rename",
    "pin",
    "move",
    "delete",
    "restore",
    "trash",
    "search",
    "forget_note",
}


def _id(body: dict, key: str = "id") -> int | None:
    v = body.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _text(body: dict, key: str, cap: int = 120) -> str:
    v = body.get(key)
    return v.strip()[:cap] if isinstance(v, str) else ""


def op(
    conv: Conversations, body: dict, busy: bool = False, history: list[dict] | None = None
) -> dict:
    action = str(body.get("action") or "")
    if action not in _ACTIONS:
        return {"ok": False, "error": "unknown action"}
    try:
        said = _do(conv, action, body, busy, history if history is not None else [])
    except _No as e:
        return {"ok": False, "error": str(e)}
    out: dict[str, Any] = {"ok": True, "state": conv.state()}
    if isinstance(said, dict):
        out.update(said)
    elif said:
        out["said"] = said
    if action not in ("list", "search", "trash"):
        conv.changed()
    return out


class _No(Exception):
    pass


def _conv(conv: Conversations, body: dict, trash: bool = False) -> dict:
    cid = _id(body)
    c = conv.store.get(cid) if cid else None
    if c is None or (c["deleted_at"] is not None) != trash:
        raise _No("no such conversation")
    return c


def _do(conv: Conversations, action: str, body: dict, busy: bool, history: list[dict]) -> Any:  # noqa: C901
    store = conv.store
    if action == "list":
        return None
    if action == "search":
        q = _text(body, "query", 80)
        hits = store.search(q, 12) if q else []
        return {
            "results": [
                {
                    "id": h["id"],
                    "title": h["title"] or "Untitled",
                    "project": h["project"],
                    "line": h["line"],
                    "updated": h["updated"],
                }
                for h in hits
            ]
        }
    if action == "trash":
        return {
            "results": [
                {
                    "id": c["id"],
                    "title": c["title"] or "Untitled",
                    "project": c["project"],
                    "deleted_at": c["deleted_at"],
                }
                for c in store.list(trash=True, limit=100)
            ]
        }
    if action == "switch":
        c = _conv(conv, body)
        if c["id"] == conv.current:
            return "already here"
        if busy:
            conv.switch(c["id"])
            return "after this answer"
        conv.apply_now(c["id"], history)
        return "switched"
    if action == "new":
        title, project = _text(body, "title", 80), _text(body, "project", 60)
        cur = conv.info()
        if cur is not None and cur["n"] == 0:
            cid = cur["id"]
            if title:
                store.rename(cid, title)
        else:
            cid = store.new(title)
        if project:
            p = store.find_project(project)
            store.move(cid, p["name"] if p else project)
        if busy:
            conv.switch(cid)
        else:
            conv.apply_now(cid, history)
        return "new"
    if action == "rename":
        c = _conv(conv, body)
        if not store.rename(c["id"], _text(body, "title", 80)):
            raise _No("empty name")
        return "renamed"
    if action == "pin":
        c = _conv(conv, body)
        store.pin(c["id"], bool(body.get("on", True)))
        return "pinned" if body.get("on", True) else "unpinned"
    if action == "move":
        c = _conv(conv, body)
        project = _text(body, "project", 60)
        if not project:
            raise _No("which project?")
        return conv.move_to(c, project)
    if action == "delete":
        if body.get("confirm") is not True:
            raise _No("confirm first")
        c = _conv(conv, body)
        store.trash(c["id"])
        if c["id"] == conv.current:
            if busy:
                conv.switch(None)
            else:
                conv.apply_now(None, history)
        return "in the trash"
    if action == "restore":
        c = _conv(conv, body, trash=True)
        store.restore(c["id"])
        return "restored"
    if action == "forget_note":
        mid = _id(body)
        rows = (
            store._q("SELECT id FROM memories WHERE id=? AND deleted_at IS NULL", (mid,))
            if mid
            else []
        )
        if not rows:
            raise _No("no such note")
        store.forget_memory(mid)
        return "forgotten"
    raise _No("unknown action")
