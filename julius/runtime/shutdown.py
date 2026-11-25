"""Wrapping up: release devices, summarise the conversation into staged memory, offer the review."""

import json
import re
import subprocess
import sys

import julius.memory.sessions as jm
from julius import identity, log
from julius.brain.backend import LLM_BACKEND, close_backend, llm_chat
from julius.paths import REPO_ROOT
from julius.runtime.session import Session

EXTRACTION_PROMPT = identity.fill("""Extract ONLY what the user explicitly stated about himself or explicitly
committed to doing. Be extremely conservative. Most conversations yield nothing.

ALREADY KNOWN — never repeat or rephrase these:
FACTS:
{known}
PROJECTS:
{projects}

NEVER record any of the following:
- anything about {name} failing, struggling, or not working
- anything about the user's mood, frustration, or tone
- any task that did not succeed
- any project invented from a question rather than stated by the user

A fact qualifies ONLY if the user stated it directly about himself, it will still be
true in a month, and it is not already known.

A project qualifies ONLY if the user explicitly said he is building it.

When in doubt, leave it out. Empty lists are the correct answer most of the time.

Return ONLY JSON:
{{"summary": "one or two sentences on what was discussed",
  "facts": [],
  "projects": []}}

CONVERSATION:
{conversation}""")


def extraction_prompt(turns: list[dict]) -> str:
    """The prompt that asks the model what, if anything, is worth remembering."""
    conversation = "\n".join(
        f"{'the user' if h['role'] == 'user' else identity.name()}: {h['content']}"
        for h in turns[-40:]
    )
    known = "\n".join(f"- {f['text']}" for f in jm._load("facts.json")["facts"])
    projects = "\n".join(f"- {p['name']}" for p in jm._load("projects.json")["projects"])
    return EXTRACTION_PROMPT.format(
        known=known or "(none)", projects=projects or "(none)", conversation=conversation
    )


def stage_session_memory(turns: list[dict]) -> None:
    """Ask the model for lasting facts and projects and stage them for review."""
    try:
        reply = llm_chat(
            messages=[{"role": "user", "content": extraction_prompt(turns)}],
            think=False,
            options={"temperature": 0.1, "num_predict": 600},
        )
        raw = re.sub(r"<think>.*?</think>", "", reply.message.content or "", flags=re.DOTALL)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}
        staged = [{"kind": "fact", "text": f} for f in data.get("facts", [])]
        staged += [{**p, "kind": "project"} for p in data.get("projects", [])]
        if staged:
            jm.stage(staged)
        if data.get("summary"):
            jm.set_last_session(data["summary"])
        log.console(f"  staged {len(staged)} item(s)")
    except Exception as exc:
        log.console(f"  extraction failed ({type(exc).__name__}) — transcript saved")


def offer_review() -> None:
    """Run the interactive review of the staged items."""
    try:
        subprocess.run([sys.executable, "-m", "julius.memory.review"], cwd=REPO_ROOT)
    except Exception:
        log.console("  run: python -m julius.memory.review")


def wrap_up(s: Session) -> None:
    """Called on Ctrl+C or /quit."""
    log.console("\n  wrapping up...")
    s.eyes.stop()
    if s.speech_player is not None:
        s.speech_player.close()
    s.tts.close()
    turns = [h for h in s.history if h["role"] in ("user", "assistant")]
    if len(turns) >= 4 and not s.scripted:
        stage_session_memory(turns)
    if LLM_BACKEND == "ollama":
        close_backend()
    log.console("  goodbye")
    if not s.scripted and jm.staged():
        offer_review()
