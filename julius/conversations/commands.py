"""Conversation commands, matched by fixed patterns before the brain (no model call), like the panel commands:

  new conversation · new conversation about the sprite editor · new chat in the Julius project
  switch to my Julius project · open the conversation about sprites · go back to the chat about drum loops
  what were we talking about in the sprite conversation (reads its summary, does not switch)
  rename this conversation to … · pin / unpin this conversation · move this to the Julius project
  delete this conversation (asks first; to the trash) · restore my last deleted conversation
  list my conversations · which conversation is this
  remember for this project: … · note for this chat: …

Looser wording reaches the brain, which has the `conversations` tool for the same actions."""

from __future__ import annotations

import re
import time

from julius.conversations.manager import Conversations

_LEAD = r"^\W*(?:(?:hey\s+)?julius,?\s+)?(?:please\s+|can you\s+|could you\s+|let'?s\s+|ok(?:ay)?,?\s+|now\s+)?"
_CONV = r"(?:conversation|chat|thread)"
_END = r"(?:,?\s+please)?[\s.!?]*$"

NEW = re.compile(
    _LEAD + rf"(?:start|begin|open|make|have|do)?\s*(?:a\s+)?(?:new|fresh|another)\s+{_CONV}"
    rf"(?:\s+(?:about|on|for|called|named)\s+(?P<title>.+?))?(?:\s+(?:in|for|under)\s+(?:the\s+|my\s+)?(?P<project>.+?)\s+project)?"
    + _END,
    re.I,
)
SWITCH_PROJECT = re.compile(
    _LEAD
    + r"(?:switch|go|jump|get|take\s+me)\s+(?:back\s+)?(?:over\s+)?to\s+(?:my\s+|the\s+|our\s+)?(?:project\s+(?P<a>.+?)|(?P<b>.+?)\s+project)"
    + _END,
    re.I,
)
OPEN = re.compile(
    _LEAD
    + rf"(?:open|switch\s+to|go\s+back\s+to|back\s+to|resume|continue|pick\s+up|pull\s+up|reopen|return\s+to)\s+(?:the|my|our|that)\s+"
    rf"(?:\w+\s+)?{_CONV}\s+(?:about|on|called|named|where\s+we\s+(?:talked|were\s+talking)\s+about|from\s+\w+\s+about)\s+(?P<q>.+?)"
    + _END,
    re.I,
)
OPEN_NAMED = re.compile(  # "open the sprite editor conversation"
    _LEAD
    + rf"(?:open|switch\s+to|go\s+back\s+to|resume|continue|reopen|return\s+to)\s+(?:the|my|our)\s+(?P<q>.+?)\s+{_CONV}"
    + _END,
    re.I,
)
RECAP = re.compile(
    _LEAD
    + rf"what\s+(?:were|was|did)\s+(?:we|i)\s+(?:talking|talk|discussing|discuss|saying|say|doing|do|decide|agree|settle|conclude|figure\s+out|pick|choose)\s+(?:about\s+|on\s+)?in\s+(?:the|my|our|that)\s+(?P<q>.+?)(?:\s+{_CONV})?"
    + _END,
    re.I,
)
RENAME = re.compile(
    _LEAD
    + rf"(?:rename|retitle)\s+(?:this|the|our)\s+{_CONV}(?:\s+to|\s+as)?\s+[\"'“]?(?P<t>.+?)[\"'”]?"
    + _END
    + "|"
    + _LEAD
    + rf"(?:call|name|title)\s+(?:this|the|our)\s+{_CONV}\s+[\"'“]?(?P<t2>.+?)[\"'”]?"
    + _END,
    re.I,
)
PIN = re.compile(_LEAD + rf"(?P<un>un)?pin\s+(?:this|the|our)\s+{_CONV}" + _END, re.I)
MOVE = re.compile(
    _LEAD
    + rf"(?:move|file|put|add)\s+(?:this|it|the|our)(?:\s+{_CONV})?\s+(?:in)?to\s+(?:the\s+|my\s+|our\s+)?(?P<p>.+?)\s+project"
    + _END,
    re.I,
)
DELETE = re.compile(
    _LEAD + rf"(?:delete|remove|trash|bin|get\s+rid\s+of)\s+(?:this|the|our)\s+{_CONV}" + _END, re.I
)
RESTORE = re.compile(
    _LEAD
    + rf"(?:restore|undelete|bring\s+back|recover)\s+(?:the|my|our|that)\s+(?:last\s+)?(?:deleted\s+)?{_CONV}(?:\s+(?:about|on|called)\s+(?P<q>.+?))?"
    + _END,
    re.I,
)
LIST = re.compile(
    _LEAD
    + rf"(?:(?:list|show|tell\s+me)\s+(?:me\s+)?(?:my|our|the|all)\s+(?:recent\s+)?{_CONV}s|what\s+{_CONV}s\s+(?:do\s+i|have\s+we|do\s+we)\s+have)"
    + _END,
    re.I,
)
WHERE = re.compile(
    _LEAD
    + rf"(?:which|what)\s+{_CONV}\s+(?:is\s+this|are\s+we\s+in|am\s+i\s+in)|what(?:'s|\s+is)\s+this\s+{_CONV}(?:\s+called)?"
    + _END,
    re.I,
)
NOTE = re.compile(
    _LEAD
    + rf"(?:remember|note|save\s+a\s+note|make\s+a\s+note)\s+(?:for|in)\s+(?:this|the|our)\s+(?P<scope>project|{_CONV})\s*(?::|,|that|—|-)?\s*(?P<text>.{{3,280}}?)"
    + _END
    + "|"
    + _LEAD
    + rf"(?:note|remember)\s+(?:for|in)\s+(?:this|the)\s+(?P<scope2>project|{_CONV})\s*(?::|,)\s*(?P<text2>.{{3,280}}?)"
    + _END,
    re.I,
)
YES = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|sure|do\s+it|go\s+ahead|confirm|delete\s+it|ok(?:ay)?)\b[\w\s,]*[.!]?\s*$",
    re.I,
)
CHOICE = re.compile(
    r"^\W*(?:the\s+)?(?P<n>first|second|1st|2nd|top|other|latest|newest|older|earlier)(?:\s+one)?"
    + _END,
    re.I,
)


PATTERNS = (NEW, RENAME, PIN, MOVE, DELETE, RESTORE, LIST, WHERE, RECAP, NOTE, OPEN, OPEN_NAMED)


def is_command(text: str) -> bool:
    """True when the words are a conversation command (the routing eval's shortcut check; switching to a project also needs the project)."""
    t = (text or "").strip()
    return bool(t) and (any(p.match(t) for p in PATTERNS) or bool(SWITCH_PROJECT.match(t)))


def handle(conv: Conversations | None, text: str) -> str | None:
    """The reply for a conversation command, or None. Sets `conv.command_turn` so the command itself is not recorded."""
    if conv is None:
        return None
    t = (text or "").strip()
    if not t or len(t) > 320:
        return None
    out = _route(conv, t)
    if out is not None:
        conv.command_turn = True
    return out


def _route(conv: Conversations, t: str) -> str | None:
    answered = _answer(conv, t)
    if answered is not None:
        return answered
    return _command(conv, t)


def _answer(conv: Conversations, t: str) -> str | None:
    """His answer to the one question a command asked: delete it? which of two conversations?"""
    if conv.pending_delete is not None:
        cid, until = conv.pending_delete
        if time.time() < until and YES.match(t):
            return conv.confirm_delete()
        conv.pending_delete = None
        if time.time() < until and re.match(
            r"^\W*(?:no|nope|don'?t|cancel|keep\s+it|never\s*mind)\b", t, re.I
        ):
            return "Kept it."
    if conv.choices:
        choices, conv.choices = conv.choices, []
        m = CHOICE.match(t)
        if m:
            n = m.group("n").lower()
            pick = choices[1] if n in ("second", "2nd", "other") else choices[0]
            if n in ("latest", "newest"):
                pick = max(choices, key=lambda c: c["updated"])
            elif n in ("older", "earlier"):
                pick = min(choices, key=lambda c: c["updated"])
            return conv.open(pick["id"])
        words = set(re.findall(r"[a-z0-9]+", t.lower()))
        named = [
            c
            for c in choices
            if words
            & set(re.findall(r"[a-z0-9]+", (c["title"] or "").lower())) - {"the", "a", "of"}
        ]
        if len(named) == 1:
            return conv.open(named[0]["id"])
    return None


def _command(conv: Conversations, t: str) -> str | None:
    m = NOTE.match(t)
    if m:
        scope = (m.group("scope") or m.group("scope2")).lower()
        text = (m.group("text") or m.group("text2")).strip()
        return conv.note("project" if scope == "project" else "conversation", text)
    m = NEW.match(t)
    if m:
        return conv.new(
            (m.group("title") or "").strip(), (m.group("project") or "").strip() or None
        )
    m = RENAME.match(t)
    if m:
        return conv.rename(m.group("t") or m.group("t2") or "")
    m = PIN.match(t)
    if m:
        return conv.pin(not m.group("un"))
    m = MOVE.match(t)
    if m:
        return conv.move(m.group("p"))
    if DELETE.match(t):
        return conv.ask_delete()
    m = RESTORE.match(t)
    if m:
        return conv.restore(m.group("q") or "")
    if LIST.match(t):
        return conv.listing()
    if WHERE.match(t):
        return conv.where()
    m = RECAP.match(t)
    if m:
        return conv.recap(m.group("q"))
    m = SWITCH_PROJECT.match(t)
    if m:
        name = m.group("a") or m.group("b")
        if conv.store.find_project(name) is not None or m.group("b"):
            return conv.switch_project(name)
    m = OPEN.match(t) or OPEN_NAMED.match(t)
    if m:
        return conv.switch_to(m.group("q"))
    return None


def tool(
    conv: Conversations | None,
    action: str = "",
    query: str = "",
    title: str = "",
    project: str = "",
    text: str = "",
) -> str:
    """The `conversations` brain tool: the same actions as the commands, for looser wording."""
    if conv is None:
        return "Conversations aren't available right now."
    a = (action or "").strip().lower()
    conv.command_turn = a not in ("note",)
    if a == "new":
        return conv.new(title or query, project or None)
    if a in ("switch", "open"):
        if project and not query:
            return conv.switch_project(project)
        return (
            conv.switch_to(query or title)
            if (query or title)
            else "Which conversation? Give me a few words about it."
        )
    if a == "recap":
        return conv.recap(query or title)
    if a == "rename":
        return conv.rename(title or query)
    if a in ("pin", "unpin"):
        return conv.pin(a == "pin")
    if a == "move":
        return conv.move(project or query)
    if a == "delete":
        return conv.ask_delete()
    if a == "restore":
        return conv.restore(query)
    if a == "list":
        return conv.listing()
    if a in ("current", "where"):
        return conv.where()
    if a == "note":
        conv.command_turn = False
        return conv.note(
            "project" if project or "project" in (query or "").lower() else "conversation",
            text or query,
        )
    conv.command_turn = False
    return "I can start, switch, recap, rename, pin, move, delete or restore a conversation, or list them."
