"""The reply every agent (files, code, utilities) gives the voice loop: whether it worked and what to say."""

from typing import Any, TypedDict


class AgentResult(TypedDict):
    """One agent reply: `message` is spoken, `code` names the outcome (preview, done, clarification ...)."""

    agent: str  # "files", "code" or "utility"
    ok: bool
    code: str
    message: str
    data: dict[str, Any]  # outcome-specific extras (plan, items, speech_message ...)
