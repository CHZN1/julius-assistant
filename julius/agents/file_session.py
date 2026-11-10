"""Human-command controller for file previews, confirmations, and results."""

import re
from pathlib import Path

from julius.agents.file_agent import FileProblem, result
from julius.agents.file_router import clean_asr, route

# Questions about the plan that is waiting for a yes: answered with the plan, which stays pending (an unrelated topic still drops it).
ABOUT_PENDING = re.compile(
    r"^(?:(?:so|and|well|okay|ok|wait|hey)[\s,]+)*(?:did you (?:do|move|trash|delete|finish|go ahead)(?: it| that)?|have you (?:done|moved|trashed|deleted|finished)(?: it| that)?|(?:is|was) (?:it|that) (?:done|finished|moved|trashed)|has (?:it|that) (?:been )?(?:done|moved|trashed|deleted)|did (?:it|that) (?:work|happen)|what(?:'s| is) (?:it )?(?:waiting|pending)|what (?:are you|were you) (?:going|about) to (?:do|trash|move)|what (?:will|would) (?:this|that|it) (?:do|change|trash|move)|which files?(?: (?:is it|was that|are you|is that))?|are you sure|(?:read|say) (?:it|that|the plan) (?:back|again)|what was (?:the plan|that))\s*[?.!]*$",
    re.I,
)
# After "Please name the exact files...", a bare filename ("Oh, report.txt") completes the trash request.
FOLLOW_UP = re.compile(
    r"^(?:(?:oh|um|uh|er|no|well|it'?s|its|that'?s|i meant|i said|just|the)[\s,]+)*(?P<name>[\w.-]+\.[a-z0-9]{1,8})(?:\s+(?:in|from|on)\s+(?:my |the )?(?P<folder>.+?))?\s*[.!?]*$",
    re.I,
)
BARE_YES = re.compile(
    r"^(?:(?:yes|yeah|yep|yup|sure|absolutely|definitely)(?:[\s,]+(?:please|do it|go ahead))?|(?:please\s+)?do it|go ahead|please do|confirm(?:ed)?|yes please)\s*[.!]*$",
    re.I,
)
NOTHING_WAITING = "There's nothing waiting for a yes right now. What would you like me to do?"
TRASHY = re.compile(r"^\W*(?:please\s+)?(?:trash|bin|delete|remove|throw away|get rid of)\b", re.I)
TRASH_PHRASE = re.compile(r"\b(?:in|into|to)\s+(?:the |my )?(?:trash(?: can)?|bin)\b", re.I)
DOCUMENT_COMMAND = re.compile(r"^(?:read document|extract text from|search document) ", re.I)
FOLDER_WORDS = re.compile(r"\b(?:in|inside|from|on)\b", re.I)


class FileSession:
    def __init__(self, agent):
        self.agent = agent
        self.pending = None
        self.last = None
        self.last_result = None
        self.clarify_turn = None
        self.suppress_idle_yes = False
        from julius.agents import file_router

        standard = {agent.root / n for n in ("Documents", "Desktop", "Downloads")}
        file_router.EXTRA_FOLDERS.clear()
        for (
            folder,
            mode,
        ) in (
            agent.modes.items()
        ):  # a folder added in [files.folders] can be named by voice ("in sandbox")
            if mode != "none" and folder not in standard and folder.is_dir():
                file_router.EXTRA_FOLDERS[file_router._key(folder.name)] = str(folder)

    def _fuzzy_trash(self, out, args, text, turn):
        """Exact name found nothing. Look for the one file that was probably meant, in the folder he named (or, if he named none, in every folder Julius may change)."""
        from julius.agents.filename_fuzzy import closest

        named = bool(FOLDER_WORDS.search(TRASH_PHRASE.sub("", text)))
        try:
            first = self.agent.resolve(args["source"])
        except (FileProblem, OSError):
            return out
        folders = (
            [first]
            if named
            else [f for f, mode in self.agent.modes.items() if mode == "write" and f.is_dir()]
        )
        found = []  # (folder, file name)
        for folder in folders:
            names = sorted(
                p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")
            )
            found += [(folder, n) for n in closest(args["pattern"], text, names)]
        if len(found) == 1:
            return self.agent.propose(
                "trash", source=str(found[0][0]), pattern=found[0][1], origin="human", turn=turn
            )
        if len(found) > 1:
            return result(
                False,
                "clarification",
                "I found "
                + " and ".join(f"{n} in {f.name}" for f, n in found[:3])
                + ". Which one should I trash? Say the exact name and folder.",
            )
        return out

    def handle(self, text: str, turn: int):
        """Turn one human utterance into a file-agent result, or None when it is not a file request."""
        if DOCUMENT_COMMAND.match(text.strip()):
            self.pending = None
            return None
        action, text = self._route(text)
        if not action:
            reply = self._pending_question(text)
            if reply:
                return reply
            action, text = self._clarify_follow_up(text, turn)
        if not action:
            return self._no_action(text)
        kind, args = action
        self.clarify_turn = (
            turn if kind == "clarify" and TRASHY.match(clean_asr(text) or text) else None
        )
        handler = getattr(self, "_do_" + kind, None) or self._do_undo
        out = handler(args, text, turn)
        self.last_result = out
        return out

    def _route(self, text: str):
        """Route the words as heard, then once more with the recognizer's usual mistakes repaired."""
        action = self._route_words(text)
        if not action:
            repaired = clean_asr(text)
            if repaired and repaired != text:
                action = self._route_words(repaired)
                if action:
                    text = repaired
        return action, text

    def _route_words(self, text: str):
        return route(
            text,
            self.agent.root / "Documents",
            pending=self.pending is not None,
            has_last=self.last is not None,
        )

    def _pending_question(self, text: str):
        """"Did you do it?" while a plan waits: answered with the plan, which stays pending."""
        if not (self.pending and ABOUT_PENDING.match(text.strip())):
            return None
        base = (
            re.sub(r"^(?:Not yet\. )+", "", (self.last_result or {}).get("message", ""))
            if self.pending == self.last
            else ""
        )
        out = result(
            True, "pending_status", "Not yet. " + (base or "A change is waiting for your yes.")
        )
        self.last_result = out
        return out

    def _clarify_follow_up(self, text: str, turn: int):
        """A bare filename right after "name the exact files" completes the trash request."""
        if self.clarify_turn is None or turn - self.clarify_turn > 2:
            return None, text
        follow = FOLLOW_UP.match(clean_asr(text) or text)
        if not follow:
            return None, text
        where = f" in {follow.group('folder')}" if follow.group("folder") else ""
        command = f"trash {follow.group('name')}{where}"
        action = self._route_words(command)
        return (action, command) if action else (None, text)

    def _no_action(self, text: str):
        if not self.pending and not self.suppress_idle_yes and BARE_YES.match(text.strip()):
            self.clarify_turn = None
            out = result(True, "nothing_pending", NOTHING_WAITING)
            self.last_result = out
            return out
        # A generic yes after an unrelated topic must not execute an old plan.
        self.pending = None
        self.clarify_turn = None
        return None

    # -- one handler per kind of request ---------------------------------------------------------

    def _remember_plan(self, out, strict: bool = True) -> None:
        """Track the plan a proposal created as the one waiting for a yes."""
        if strict:
            self.pending = out["data"]["plan"]["id"] if out["ok"] else None
        else:
            has_plan = out["ok"] and "plan" in out.get("data", {})
            self.pending = out["data"]["plan"]["id"] if has_plan else None
        if self.pending:
            self.last = self.pending

    def _replace_pending(self) -> None:
        if self.pending:
            self.agent.cancel(self.pending)

    def _do_clarify(self, args, _text, _turn):
        self.pending = None
        return result(False, "clarification", args["clarification"])

    def _read_only(self, kind, args):
        self.pending = None
        if kind == "read" and str(args.get("path", "")).lower().endswith((".pdf", ".docx")):
            from julius.brain.documents import read_document

            return read_document(args["path"], file_agent=self.agent)
        return getattr(self.agent, kind)(**args)

    def _do_read(self, args, _text, _turn):
        return self._read_only("read", args)

    def _do_list(self, args, _text, _turn):
        return self._read_only("list", args)

    def _do_find(self, args, _text, _turn):
        return self._read_only("find", args)

    def _do_rename(self, args, _text, turn):
        self._replace_pending()
        try:
            old = self.agent.resolve(args["path"])
            target = Path(args["destination"])
            if not target.is_absolute() and len(target.parts) == 1:
                args["destination"] = str(old.parent / target)
        except (FileProblem, OSError):
            pass  # The service returns the path error in its structured result.
        out = self.agent.propose("rename", **args, turn=turn)
        self._remember_plan(out)
        return out

    def _do_create(self, args, _text, turn):
        self._replace_pending()
        out = self.agent.propose("create", **args, turn=turn)
        self._remember_plan(out)
        return out

    def _transfer(self, kind, args, turn):
        self._replace_pending()
        out = self.agent.propose(
            kind,
            source=args["source"],
            destination=args["destination"],
            pattern=args["pattern"],
            recursive=args.get("recursive", False),
            turn=turn,
        )
        self._remember_plan(out)
        return out

    def _do_move(self, args, _text, turn):
        return self._transfer("move", args, turn)

    def _do_copy(self, args, _text, turn):
        return self._transfer("copy", args, turn)

    def _do_trash(self, args, text, turn):
        self._replace_pending()
        out = self.agent.propose(
            "trash", source=args["source"], pattern=args["pattern"], origin="human", turn=turn
        )
        literal = not any(c in args["pattern"] for c in "*?[")
        if (
            not out["ok"]
            and out.get("code") in ("no_matches", "not_found", "outside_scope")
            and not FOLDER_WORDS.search(TRASH_PHRASE.sub("", text))
            and literal
        ):
            out = self._trash_in_any_folder(out, args, turn)
        if not out["ok"] and out.get("code") in ("no_matches", "not_found") and literal:
            # the recognizer mangled the name ("TrashReport.TXT"): offer the real file, still behind a yes
            out = self._fuzzy_trash(out, args, text, turn)
        self._remember_plan(out, strict=False)
        return out

    def _trash_in_any_folder(self, out, args, turn):
        """No folder was named: look for that exact name in every folder Julius may change."""
        homes = [f for f, mode in self.agent.modes.items() if mode == "write" and f.is_dir()]
        hits = [
            f
            for f in homes
            if self.agent.find(pattern=args["pattern"], source=str(f), recursive=False)[
                "data"
            ].get("items")
        ]
        if len(hits) == 1:
            return self.agent.propose(
                "trash", source=str(hits[0]), pattern=args["pattern"], origin="human", turn=turn
            )
        if len(hits) > 1:
            return result(
                False,
                "clarification",
                f"I found {args['pattern']} in {' and '.join(f.name for f in hits)}. Say which folder, for example trash {args['pattern']} in {hits[0].name}.",
            )
        return out

    def _do_empty_trash(self, _args, _text, turn):
        self._replace_pending()
        out = self.agent.propose_empty_trash(turn=turn)
        self._remember_plan(out)
        return out

    def _do_trash_info(self, _args, _text, _turn):
        self.pending = None
        return self.agent.trash_info()

    def _do_history(self, args, _text, _turn):
        self.pending = None
        return self.agent.history(today=bool((args or {}).get("today")))

    def _do_approve(self, _args, _text, turn):
        out = self.agent.execute(self.pending, confirmed=True, turn=turn)
        self.pending = None
        return out

    def _do_cancel(self, _args, _text, _turn):
        out = self.agent.cancel(self.pending)
        self.pending = None
        return out

    def _do_resume(self, _args, _text, _turn):
        out = self.agent.show(self.last)
        plan = out["data"].get("plan", {})
        if out["ok"] and plan.get("status") == "pending":
            self.pending = self.last
            out["message"] += " Say yes to confirm or cancel to discard."
        else:
            self.pending = None
        return out

    def _do_undo(self, _args, _text, _turn):
        self.pending = None
        return self.agent.undo()
