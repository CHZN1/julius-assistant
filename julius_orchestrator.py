"""Sonnet 5.5 planner behind escalate_to_planner, with Haiku sub-agents and a propose-only file agent.

Haiku (the voice front end) calls escalate_to_planner for multi-step work. The planner (Sonnet) may answer with
utility tools itself, write a system prompt and task for a sub-agent and run its tool loop (Haiku, bounded steps),
check the result, and return a short spoken summary. File changes can only be *proposed*: the plan is staged in
the human FileSession and runs only after the user says yes. There is no coding agent yet.
"""
import json
from types import SimpleNamespace

import julius_cloud as jc
import julius_config as cfg

MAX_RESULT_CHARS = 6000
SPOKEN_LIMIT = 500

# Read-only utilities a sub-agent may be given. Never files_propose, delegate or escalation.
SUBAGENT_TOOLS = ("get_datetime", "date_calculate", "calculate", "convert_units", "get_weather", "get_forecast", "get_quote",
                  "get_scores", "web_search", "get_news", "read_document", "list_planner_items", "list_files", "read_file", "find_files")

PLANNER_SYSTEM = """You are the planner behind Julius, a voice assistant. Julius's quick model hands you tasks that need several steps.
Your final reply is spoken aloud: at most two short sentences, plain words, no lists, no markdown, no URLs.

Work like this:
- Use the utility tools yourself for simple lookups.
- For work that needs several lookups or a long tool sequence, call delegate with a precise system_prompt and task you write for a
  sub-agent, and list only the tools it needs. Read its result critically against the task before you answer; if it is wrong or
  incomplete, delegate once more with a sharper task, otherwise answer.
- File changes: you can only propose them with files_propose. Nothing is applied until the user says yes, so never say a file was
  moved, copied, renamed or created; say what you proposed and ask the user to say yes or cancel.
- Cleaning up: you may propose action trash (move loose files to the recoverable trash, never a permanent delete) with source and pattern, or
  with paths. Only loose files; folders, apps, music and audio files, anything in a code project, recently changed files, source code and
  installers in use are protected and are skipped automatically, so do not try to include them. Look first (list_files/find_files),
  propose only what is clearly junk, and tell the user what you left alone.
- Coding: code_task stages a coding task for a stronger coding model that works on a sandboxed COPY of one project folder under the projects
  folder. Use it only when the user clearly asks for code to be written, fixed or tested, with a clear goal and a named project; if the project is
  unclear, ask which one. Never say it started or finished: it only starts after the user says yes, and changes reach the project only after a
  separate merge yes. You cannot run code or edit code yourself.
- Research: research_task runs a read-only web research sub-agent for questions that need current or sourced facts from the web beyond a
  quick search (comparisons, "find out about", "look up and summarise"). Give it one precise question. It returns a short answer with its
  cited findings; speak only what it confirmed, say plainly if it could not confirm, and do not repeat URLs.
- Never invent results. If a tool failed or found nothing, say that plainly."""

SUBAGENT_FOOTER = ("\n\nYou are a sub-agent. Use only your tools, stop as soon as the task is answered, and reply with the facts "
                   "found in under 120 words. If something could not be found, say so.")

DELEGATE_SCHEMA = {"name": "delegate", "description": "Run a sub-agent with its own system prompt, task and tool list, and return its result.",
    "input_schema": {"type": "object", "properties": {
        "system_prompt": {"type": "string", "description": "Role and rules for the sub-agent"},
        "task": {"type": "string", "description": "The exact task to complete"},
        "tools": {"type": "array", "items": {"type": "string", "enum": list(SUBAGENT_TOOLS)}, "description": "Tools the sub-agent may use"},
        "max_steps": {"type": "integer", "description": "Optional step limit, capped by configuration"}},
        "required": ["system_prompt", "task", "tools"]}}

FILES_PROPOSE_SCHEMA = {"name": "files_propose", "description": "Stage a file change for the user's confirmation. Nothing is applied until the user says yes.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["move", "copy", "rename", "create", "trash"]},
        "paths": {"type": "array", "items": {"type": "string"}, "description": "Exact files to move to the trash (trash only)"},
        "source": {"type": "string", "description": "Source folder for move/copy"},
        "destination": {"type": "string", "description": "Destination folder (move/copy) or new name/path (rename)"},
        "pattern": {"type": "string", "description": "Glob such as *.csv for move/copy"},
        "recursive": {"type": "boolean"},
        "path": {"type": "string", "description": "File to rename or create"},
        "content": {"type": "string", "description": "Exact text for create"}},
        "required": ["action"]}}

CODE_TASK_SCHEMA = {"name": "code_task", "description": "Stage a coding task (write, fix or test code) for the sandboxed coding agent. Nothing runs until the user says yes.",
    "input_schema": {"type": "object", "properties": {
        "goal": {"type": "string", "description": "What the coding agent should do, in one or two precise sentences"},
        "project": {"type": "string", "description": "Name of the project folder under the projects folder"},
        "criteria": {"type": "string", "description": "What done looks like, e.g. which tests must pass"},
        "budget_usd": {"type": "number", "description": "Optional spend limit in dollars; the default is used when omitted and a hard maximum applies"}},
        "required": ["goal", "project"]}}

RESEARCH_TASK_SCHEMA = {"name": "research_task", "description": "Run a read-only web research sub-agent (searches and reads pages, cites sources) and return a short sourced answer. The full structured result is saved for the side panel.",
    "input_schema": {"type": "object", "properties": {"question": {"type": "string", "description": "One precise research question"}}, "required": ["question"]}}

_STATE = {"file_session": None, "turn": lambda: 0, "user_text": "", "code_session": None}


def configure(file_session=None, turn=None, user_text=None, code_session=None):
    """The runtime supplies the human FileSession that owns confirmations, the turn number, and each turn's exact words."""
    if user_text is not None:
        _STATE["user_text"] = user_text
    if file_session is not None:
        _STATE["file_session"] = file_session
    if turn is not None:
        _STATE["turn"] = turn
    if code_session is not None:
        _STATE["code_session"] = code_session


def _clip(text):
    text = str(text)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + " …[truncated]"


def utility_tools(names=None):
    """Messages API tool definitions for Julius's utilities (stable order)."""
    import julius_tools as jt
    schemas = [s for s in jt.SCHEMAS if names is None or s["function"]["name"] in names]
    tools = jc.convert_tools(schemas)
    return [{k: v for k, v in t.items() if k != "cache_control"} for t in tools]


def files_propose(args):
    """Stage a proposal in the human FileSession; never executes."""
    session = _STATE["file_session"]
    if session is None:
        return "File proposals are not available in this session."
    agent, action = session.agent, args["action"]
    try:
        if action in ("move", "copy"):
            out = agent.propose(action, source=args.get("source", "."), destination=args["destination"], pattern=args.get("pattern", "*"),
                                recursive=bool(args.get("recursive", False)), turn=_STATE["turn"]())
        elif action == "trash":
            out = agent.propose("trash", source=args.get("source", "."), pattern=args.get("pattern", "*"), paths=args.get("paths"), origin="planner", turn=_STATE["turn"]())
        elif action == "rename":
            out = agent.propose("rename", path=args["path"], destination=args["destination"], turn=_STATE["turn"]())
        else:
            out = agent.propose("create", destination=args.get("path") or args["destination"], content=args["content"], turn=_STATE["turn"]())
    except KeyError as exc:
        return f"files_propose needs {exc.args[0]}."
    if out["ok"]:
        if session.pending:
            agent.cancel(session.pending)
        session.pending = session.last = out["data"]["plan"]["id"]
    session.last_result = out
    return out["message"] + (" (Staged only: the user must say yes to apply it.)" if out["ok"] else "")


def code_task(args):
    """Stage a coding task in the human CodeSession; never runs it."""
    session = _STATE["code_session"]
    if session is None:
        return "Coding tasks are not available in this session."
    out = session.stage(args.get("goal", ""), args.get("project", ""), criteria=args.get("criteria", ""), cap=args.get("budget_usd"), origin="planner")
    return out["message"] + (" (Staged only: the user must say yes to start it.)" if out["ok"] and out["code"] == "preview" else "")


class Loop:
    """A bounded Messages API tool loop for one tier."""

    def __init__(self, brain, tier, system, tools, execute, max_steps, note):
        self.brain, self.tier, self.execute, self.max_steps, self.note = brain, tier, execute, max_steps, note
        settings = jc.TIERS[tier]
        self.kwargs = {"model": brain.config["models"][tier], "max_tokens": settings["max_tokens"], "cache_control": {"type": "ephemeral"},
                       "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                       **{k: v for k, v in settings.items() if k != "max_tokens"}}
        if tools:
            self.kwargs["tools"] = tools[:-1] + [{**tools[-1], "cache_control": {"type": "ephemeral"}}]
        self.steps = 0

    def _call(self, messages, **extra):
        if self.tier == "sonnet" and self.brain.tracker.cap_reached():
            raise SpendCap()
        response = self.brain.slow_client().messages.create(messages=messages, **self.kwargs, **extra)
        self.brain.tracker.record(self.tier, response.usage, note=self.note)
        return response

    @staticmethod
    def text_of(response):
        return " ".join(b.text.strip() for b in response.content if getattr(b, "type", "") == "text" and b.text.strip())

    def run(self, task):
        messages = [{"role": "user", "content": task}]
        for _ in range(self.max_steps):
            self.steps += 1
            response = self._call(messages)
            messages.append({"role": "assistant", "content": response.content})  # thinking blocks must be returned unchanged
            if response.stop_reason != "tool_use":
                return self.text_of(response), "done"
            results = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                try:
                    out, is_error = _clip(self.execute(block.name, dict(block.input))), False
                except SpendCap:
                    raise
                except Exception as exc:
                    out, is_error = f"{block.name} failed: {type(exc).__name__}: {exc}", True
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        self.steps += 1  # step limit: one last call with tools disabled to summarise
        messages[-1]["content"].append({"type": "text", "text": "Step limit reached. Say what you found and what remains, in two short sentences."})
        final = self._call(messages, tool_choice={"type": "none"})
        return self.text_of(final), "step_limit"


class SpendCap(Exception):
    pass


class Planner:
    def __init__(self, brain, dispatcher=None):
        self.brain, self.dispatcher = brain, dispatcher
        self.config = brain.config
        self.subagent_runs = []

    def tools(self):
        return utility_tools() + [DELEGATE_SCHEMA, FILES_PROPOSE_SCHEMA, CODE_TASK_SCHEMA, RESEARCH_TASK_SCHEMA]

    def execute(self, name, args):
        import julius_tools as jt
        if name == "delegate":
            return self.delegate(args)
        if name == "files_propose":
            return files_propose(args)
        if name == "code_task":
            return code_task(args)
        if name == "research_task":
            import julius_research
            return julius_research.research_task(args, self.brain)
        return jt.call(name, args)

    def delegate(self, args):
        import julius_tools as jt
        names = [n for n in args.get("tools", []) if n in SUBAGENT_TOOLS]
        limit = max(1, min(int(args.get("max_steps") or 99), self.config["orchestrator"]["subagent_max_steps"]))
        loop = Loop(self.brain, "haiku", args["system_prompt"] + SUBAGENT_FOOTER, utility_tools(names) if names else None,
                    lambda name, a: jt.call(name, a) if name in names else f"{name} is not available to this sub-agent.", limit, "subagent")
        text, why = loop.run(args["task"])
        self.subagent_runs.append({"task": args["task"][:200], "tools": names, "steps": loop.steps, "stopped": why})
        return f"[sub-agent finished in {loop.steps} step(s), {why}] {text}"

    def run(self, task):
        loop = Loop(self.brain, "sonnet", PLANNER_SYSTEM, self.tools(), self.execute, self.config["orchestrator"]["max_steps"], "planner")
        text, why = loop.run(task)
        self.steps, self.stopped = loop.steps, why
        return text


def spoken(text):
    text = " ".join(str(text).replace("*", "").replace("#", "").split())
    return text if len(text) <= SPOKEN_LIMIT else text[:SPOKEN_LIMIT].rsplit(" ", 1)[0] + "."


def run_escalation(args, dispatcher, brain=None):
    """escalate_to_planner(task) -> short spoken summary for Haiku to say. Failures return a spoken explanation."""
    import anthropic
    brain = brain or jc.get_brain()
    task = (args or {}).get("task", "")
    original = _STATE["user_text"].strip()
    if not (isinstance(task, str) and task.strip()):
        task = original  # the front end sometimes sends no arguments; the user's own words are the task
    elif original and original.lower() not in task.lower():
        task = f"{task}\n\nThe user's exact words: {original}"  # keeps details a paraphrase could drop
    ok, message = False, ""
    previous_limit = getattr(dispatcher, "turn_call_limit", None)
    try:
        if not isinstance(task, str) or not task.strip():
            message = "I need to know what to plan."
        elif brain.auth_bad:
            message = jc.AUTH_MESSAGE
        elif brain.tracker.cap_reached():
            message = "Planning is paused because today's spending limit was reached."
        else:
            if previous_limit is not None:
                dispatcher.turn_call_limit = 60  # planner and sub-agents make many tool calls
            planner = Planner(brain, dispatcher)
            message, ok = spoken(planner.run(task)) or "The planner returned nothing.", True
    except SpendCap:
        message = "Planning stopped because today's spending limit was reached."
    except (anthropic.AuthenticationError, jc.MissingKey):
        brain.mark_auth_bad()
        message = jc.AUTH_MESSAGE
    except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError, anthropic.InternalServerError):
        message = "I couldn't reach the planner right now."
    finally:
        if previous_limit is not None:
            dispatcher.turn_call_limit = previous_limit
    if dispatcher is not None and hasattr(dispatcher, "record_external"):
        dispatcher.record_external("escalate_to_planner", ok, message, agent="planner")
    return message
