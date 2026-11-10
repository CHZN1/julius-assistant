"""Bounded deterministic file routing; ambiguity never becomes a broad move."""

import difflib
import os
import re
from pathlib import Path

from julius.brain import confirm

PLANNING_RE = re.compile(
    r"\b(?:organi[sz]e|sort|tidy|clean(?:\s|-)?up|declutter|categori[sz]e|group|rename (?:all|every|each))\b",
    re.I,
)
MOVE_RE = re.compile(r"\b(move|organize|organise|tidy|archive|put|sort|copy)\b", re.I)
FILEWORD = re.compile(
    r"\b(file|files|csvs?|notebooks?|pdfs?|images?|docs?|scripts?|everything|photos?|pictures?|documents?)\b",
    re.I,
)
UNDO = re.compile(
    r"^\s*(?:please\s+)?(?:undo(?:\s+(?:that|the last (?:move|copy|change)|last))?|revert the last (?:move|change)|put (?:them|it) back)\s*[.!]?\s*$",
    re.I,
)
CANCEL = re.compile(
    r"^\s*(?:no|nope|cancel(?: that| the (?:plan|move|copy))?|never mind|nevermind|stop)\s*[.!]?\s*$",
    re.I,
)
APPROVE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|do it|go ahead|approve|approved|confirm|sure|okay|ok|sounds good|move them|copy them|proceed|execute|go for it)(?:[, ]+\s*(?:please|go ahead|do it))?(?:[.!])?\s*$",
    re.I,
)
RESUME = re.compile(
    r"^\s*(?:show (?:(?:the|that) )?(?:last )?plan|resume (?:the|that|last) plan|go back to (?:the|that|last) plan)\s*[.!]?\s*$",
    re.I,
)
EXT = {
    "csv": "*.csv",
    "csvs": "*.csv",
    "notebook": "*.ipynb",
    "notebooks": "*.ipynb",
    "pdf": "*.pdf",
    "pdfs": "*.pdf",
    "script": "*.py",
    "scripts": "*.py",
    "doc": "*.docx",
    "docs": "*.docx",
    "image": ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.heic"],
    "images": ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.heic"],
    "photo": ["*.png", "*.jpg", "*.jpeg", "*.heic"],
    "photos": ["*.png", "*.jpg", "*.jpeg", "*.heic"],
    "picture": ["*.png", "*.jpg", "*.jpeg", "*.heic"],
    "pictures": ["*.png", "*.jpg", "*.jpeg", "*.heic"],
}


def norm(text):
    # Quoted names are literal: "Dash Reports" must not become "- Reports".
    quoted = []

    def protect(m):
        quoted.append(m.group())
        return f"QUOTEDTOKEN{len(quoted) - 1}"

    text = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', protect, text)
    for word, mark in [
        ("underscore", "_"),
        ("hyphen", "-"),
        ("dash", "-"),
        ("dot", "."),
        ("slash", "/"),
    ]:
        text = re.sub(rf"\s*\b{word}\b\s*", mark, text, flags=re.I)
    text = re.sub(r"\s*([_/])\s*", r"\1", text)
    for i, q in enumerate(quoted):
        text = text.replace(f"QUOTEDTOKEN{i}", q)
    return re.sub(r"\s+", " ", text).strip()


def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.casefold())


def _dirs(root, depth=2, limit=1000):
    root = Path(root)
    out = [Path(".")]
    stack = [(root, 0)]
    visited = 0
    while stack and visited < limit:
        folder, level = stack.pop()
        if level >= depth:
            continue
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    visited += 1
                    if visited > limit:
                        break
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        p = Path(entry.path)
                        out.append(p.relative_to(root))
                        stack.append((p, level + 1))
        except OSError:
            continue
    return out


def _clean_folder(phrase):
    text = phrase.strip().rstrip(".!?").strip()
    text = re.sub(r"^(?:the|my|a|an)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(?:folder|directory)$", "", text, flags=re.I)
    return text.strip().strip('"').strip("'")


_GLUED_VERB = re.compile(
    r"^((?i:trash|delete|remove|move|copy|rename|show|list|find|read|open|put|send))(?=[A-Z])|^((?i:trash|delete|remove))(?=[a-z0-9_-]{3,}\.[A-Za-z0-9]{1,5}\b)"
)


def clean_asr(text):
    """Repair common transcription artefacts before a second routing attempt: commas between words ("trash, report.txt, in sandbox"), a command verb glued to the
    next word ("TrashReport.txt"), runs of spaces. The original text is always tried first."""
    from julius.audio.listening import strip_leading_wake

    out = _GLUED_VERB.sub(
        lambda m: (m.group(1) or m.group(2)) + " ", strip_leading_wake(text.strip())
    )
    out = re.sub(r"\s*[,;]\s+", " ", out)
    return re.sub(r"\s+", " ", out).strip()


EXTRA_FOLDERS = {}  # name -> absolute path of folders added in [files.folders] outside Documents/Desktop/Downloads; set by FileSession


def match_dir(phrase, root, cutoff=0.80):
    if not phrase:
        return None
    phrase = _clean_folder(phrase)
    root = Path(root)
    key = _key(phrase)
    if key in EXTRA_FOLDERS:
        return EXTRA_FOLDERS[key]
    if key in {"documents", "document", "here", "thisfolder", "currentfolder", "root"}:
        return "Documents"
    if key in {"downloads", "desktop"}:
        return "Downloads" if key == "downloads" else "Desktop"
    path = Path(phrase).expanduser()
    if path.is_absolute():
        return str(path) if path.is_dir() else None
    if phrase.startswith(("Documents/", "Desktop/", "Downloads/")):
        return phrase if (root.parent / path).is_dir() else None
    if (root / path).is_dir():
        return "Documents/" + str(path)
    scored = []
    for directory in _dirs(root):
        if directory == Path("."):
            continue
        score = max(
            difflib.SequenceMatcher(None, key, _key(s)).ratio()
            for s in (str(directory), directory.name)
        )
        if score >= cutoff:
            scored.append((score, str(directory)))
    scored.sort(reverse=True)
    if not scored or len(scored) > 1 and scored[0][0] - scored[1][0] < 0.12:
        return None
    return "Documents/" + scored[0][1]


def _mask_literals(text):
    """Hide quoted names so their words are not parsed; returns (masked, restore function)."""
    literals = []

    def hide(m):
        literals.append(m.group())
        return f"PATHLITERAL{len(literals) - 1}"

    masked = re.sub(r'"[^"\n]*"|(?<!\w)\'[^\'\n]+\'', hide, text)

    def restore(value):
        for i, literal in enumerate(literals):
            value = value.replace(f"PATHLITERAL{i}", literal)
        return value

    return masked, restore


def _destination(phrase, raw_phrase, source, root):
    """The destination folder, or a clarification dict when it is not clearly one."""
    if phrase.casefold() in {"it", "them", "there", "that", "that folder"} or not phrase:
        return {"clarification": "Please name the destination folder."}
    destination = match_dir(phrase, root)
    if destination is not None:
        return destination
    # Existing near-matches require clarification; only explicitly requested new folders are created.
    explicit_new = bool(re.search(r"\b(?:new|create)\b", raw_phrase, re.I))
    clean = re.sub(
        r"^(?:new|create)\s+(?:folder\s+(?:(?:named|called)\s+)?)?", "", phrase, flags=re.I
    )
    if "/" in phrase:
        return phrase
    if explicit_new and clean and not Path(clean).is_absolute():
        return source + "/" + clean
    return {
        "clarification": "I could not uniquely identify that destination. Give its full path, or say “a new folder named …”."
    }


def _file_pattern(selection):
    """The filename pattern the words select, or a clarification dict."""
    pattern = _ext_pattern(selection) or "*"
    names = re.findall(r'"([^"]+)"|\'([^\']+)\'', selection)
    if names:
        name = next(a or b for a, b in names)
        return name if "." in name or any(c in name for c in "*?[") else f"*{name}*"
    if re.search(
        r"\b(this|that|these|those|a|an)\s+(?:file|files|script|scripts|pdf|pdfs|csv|csvs|image|images|notebook|notebooks)\b",
        selection,
        re.I,
    ) and not re.search(r"\b(all|every)\b", selection, re.I):
        return {
            "clarification": "Please name the exact files. I cannot tell which files you mean from that reference alone."
        }
    name = re.search(r"(?<![\w/\'])([\w.-]+\.[a-z0-9]{1,8})\b", selection, re.I)
    if name:
        return name.group(1)
    if pattern == "*":
        fragment = re.search(r"\b[\w.-]*[_-][\w.-]+\b", selection)
        if fragment:
            return "*" + fragment.group() + "*"
        if not re.search(r"\b(all|everything)\b", selection, re.I):
            return {
                "clarification": "Which files should I select? Name a file, file type, or say all files."
            }
    return pattern


def parse(text, root):
    """Parse a move or copy request into an action dict, a clarification dict, or None."""
    text = norm(text)
    if re.match(r"^(?:how\b|why\b|what is\b|explain\b|tell me about\b)", text, re.I):
        return None
    masked, restore = _mask_literals(text)
    if not (
        MOVE_RE.search(text)
        and (FILEWORD.search(text) or re.search(r"\.[a-z0-9]{1,8}\b", text, re.I))
    ):
        return None
    if re.search(r"\b(don't|do not|never|not yet|without moving)\b", text, re.I):
        return {
            "clarification": "No files were changed. Tell me when you want to make a file plan."
        }
    # Source and destination are parsed independently; never fall back for an explicit unknown source.
    match = re.search(r"\b(?:into|to)\s+(.+?)\s*$", masked, re.I)
    if not match:
        return {"clarification": "Which folder should the files go into?"}
    before = masked[: match.start()]
    destination_phrase = _clean_folder(restore(match.group(1)))
    source_match = re.search(r"\b(?:from|in|inside)\s+(.+?)\s*$", before, re.I)
    selection = before[: source_match.start()] if source_match else before
    source = match_dir(restore(source_match.group(1)), root) if source_match else "Documents"
    selection = restore(selection)
    if not source:
        return {
            "clarification": "I could not uniquely identify the source folder. Please give its full name or path."
        }
    destination = _destination(destination_phrase, match.group(1), source, root)
    if isinstance(destination, dict):
        return destination
    pattern = _file_pattern(selection)
    if isinstance(pattern, dict):
        return pattern
    return {
        "action": "copy" if re.search(r"\bcopy\b", selection, re.I) else "move",
        "pattern": pattern,
        "source": source,
        "destination": destination,
        "recursive": bool(re.search(r"\b(recursively|subfolders|subdirectories)\b", text, re.I)),
    }


EMPTY_TRASH = re.compile(
    r"^\s*(?:please\s+)?(?:empty|clear|clean out|permanently delete)(?: out)?\s+(?:the |my |everything in the )?(?:trash|bin|trash can|recycle bin)\s*[.!]?\s*$",
    re.I,
)
TRASH_INFO = re.compile(
    r"^\s*(?:how (?:big|full|large) is|how much is in|what(?:\'s| is) in|show(?: me)?|check)\s+(?:the |my )?(?:trash|bin|trash can)\s*[.?!]?\s*$",
    re.I,
)
FILE_HISTORY = re.compile(
    r"^\s*(?:what (?:files )?(?:did|have) you (?:change|changed|move|moved|trash|trashed|do)(?: with my files)?(?: today| recently| lately| so far)?|(?:show|list)(?: me)?(?: the)? (?:file )?(?:history|changes|recent file changes))\s*[.?!]?\s*$",
    re.I,
)
TRASH_VERB = re.compile(r"^(?:trash|bin|delete|remove|throw away|get rid of)\s+(.+)$", re.I)
MOVE_TO_TRASH = re.compile(
    r"^(?:move|put|send)\s+(.+?)\s+(?:to|in|into)\s+(?:the |my )?(?:trash|bin|trash can)$", re.I
)


def _ext_pattern(selection):
    """The glob for a file type named in the words ("pdfs", "csv"), or None."""
    for word, patterns in EXT.items():
        if re.search(rf"\b{word}\b", selection, re.I):
            return patterns
    return None


def _trash_pattern(selection):
    """A quoted name, an explicit filename or a file type; None when none of them is given."""
    names = re.findall(r'"([^"]+)"|\'([^\']+)\'', selection)
    if names:
        name = next(a or b for a, b in names)
        return name if "." in name or any(c in name for c in "*?[") else f"*{name}*"
    explicit = re.search(r"(?<![\w/\'])([\w.-]+\.[a-z0-9]{1,8})\b", selection, re.I)
    return explicit.group(1) if explicit else _ext_pattern(selection)


def parse_trash(text, root):
    """'trash notes.txt in Downloads', 'move the pdfs in Downloads to the trash': a named file, a quoted name or a file type; never 'everything'."""
    body = re.sub(
        r"^(?:(?:please|can you|could you|would you|just|now|go ahead and|hey)[\s,]+)+",
        "",
        text.strip(),
        flags=re.I,
    ).rstrip(".!")
    gone = re.match(
        r"^(?:i (?:want|need|would like)|i'd like)\s+(.+?)\s+(?:gone|deleted|trashed|in the trash|in the bin)\b",
        body,
        re.I,
    )
    if gone:
        body = "trash " + gone.group(1)
    m = MOVE_TO_TRASH.fullmatch(body) or TRASH_VERB.fullmatch(body)
    if not m:
        return None
    selection = m.group(1).strip()
    if re.fullmatch(
        r"(?:the )?(?:memory|memories|note|notes|that|this|it|that memory|my memory)",
        selection,
        re.I,
    ):
        return None
    folder = None
    located = re.fullmatch(
        r"(.+?)\s+(?:in|inside|from|on)\s+(?:my |the )?(.+?)(?:\s+(?:folder|directory))?",
        selection,
        re.I,
    )
    if located:
        selection, folder = located.group(1).strip(), located.group(2).strip()
    source = match_dir(folder, root) if folder else "Downloads"
    if folder and not source:
        return {"clarification": "Please give the full folder name or path."}
    pattern = _trash_pattern(selection)
    too_broad = re.search(r"\b(?:everything|all)\b", selection, re.I) and not re.search(
        r"\.[a-z0-9]{1,8}\b|\b(?:csvs?|pdfs?|notebooks?|images?|scripts?|docs?)\b", selection, re.I
    )
    if pattern is None or too_broad:
        return {
            "clarification": "Please name the exact files, a file type like PDFs, or a quoted name. I will not guess what to throw away."
        }
    return {"source": source, "pattern": pattern}


def _in_folder(phrase):
    """'notes.txt in Downloads' -> 'Downloads/notes.txt' so a folder named after the file reaches the right place."""
    located = re.fullmatch(
        r"(.+?)\s+(?:in|inside|from)\s+(?:my |the )?(downloads|desktop|documents)(?:\s+(?:folder|directory))?",
        phrase.strip(),
        re.I,
    )
    return f"{located.group(2).capitalize()}/{located.group(1).strip()}" if located else phrase


def _rename_request(body, _root):
    rename = re.fullmatch(r"rename\s+(?:the file\s+|file\s+)?(.+?)\s+to\s+(.+)", body, re.I)
    if not rename:
        return None
    old, new = (_clean_folder(x) for x in rename.groups())
    old = _in_folder(old)
    if any(c in old + new for c in "*?["):
        return (
            "clarify",
            {
                "clarification": "Rename one exact file at a time. Please give its current and new filename."
            },
        )
    return ("rename", {"path": old, "destination": new})


def _create_request(body, _root):
    create = re.fullmatch(
        r'(?:create|write)\s+(?:a (?:text )?file\s+|file\s+)?"([^"\n]+)"\s+(?:containing|with text|with the text)\s+"([^"\n]*)"[.!]?',
        body,
        re.I,
    )
    if not create:
        return None
    return ("create", {"destination": create.group(1), "content": create.group(2)})


def _read_request(body, _root):
    read = re.fullmatch(r"read\s+(?:the file\s+|file\s+)?(.+)", body, re.I)
    if read and (
        re.search(r"\.[a-z0-9]{1,8}\b", read.group(1), re.I) or read.group(1).startswith('"')
    ):
        return ("read", {"path": _in_folder(_clean_folder(read.group(1)))})
    return None


def _list_request(body, root):
    listing = re.fullmatch(
        r"(?:list|show)\s+(?:me\s+)?(?:the\s+)?(?:files|folders|contents)(?:\s+(?:in|inside|of)\s+(.+))?[.!]?",
        body,
        re.I,
    )
    if not listing:
        return None
    folder = match_dir(listing.group(1), root) if listing.group(1) else "Documents"
    if folder:
        return ("list", {"directory": folder})
    return ("clarify", {"clarification": "Please give the full folder name or path to list."})


def _find_request(body, root):
    finding = re.fullmatch(
        r"(?:find|search for)\s+(.+?)(?:\s+(?:in|inside|from)\s+(.+))?[.!]?", body, re.I
    )
    if not finding:
        return None
    selection, folder = finding.groups()
    filename = re.search(r'"([^"\n]+)"', selection)
    if filename:
        value = filename.group(1)
        pattern = value if "." in value or any(c in value for c in "*?[") else "*" + value + "*"
    elif re.fullmatch(r"[\w.*?\[\]-]+\.[a-z0-9*?]{1,8}", selection.strip(), re.I):
        pattern = selection.strip()
    else:
        pattern = _ext_pattern(selection)
    if pattern is None:
        return None
    source = match_dir(folder, root) if folder else "Documents"
    if source:
        return ("find", {"pattern": pattern, "source": source})
    return ("clarify", {"clarification": "Please give the full folder name or path to search."})


_SIMPLE_REQUESTS = (_rename_request, _create_request, _read_request, _list_request, _find_request)


def parse_simple_request(text, root):
    """Rename, create, read, list and find requests: (kind, args) or None."""
    body = re.sub(r"^(?:(?:please|can you|could you|would you)\s+)+", "", text.strip(), flags=re.I)
    for parser in _SIMPLE_REQUESTS:
        found = parser(body, root)
        if found:
            return found
    return None


def _state_command(text, pending, has_last):
    """Undo, cancel, resume, approve and the trash/history questions."""
    if UNDO.fullmatch(text):
        return ("undo", None)
    if pending and (CANCEL.fullmatch(text) or confirm.is_no(text)):
        return ("cancel", None)
    if has_last and RESUME.fullmatch(text):
        return ("resume", None)
    if pending and (APPROVE.fullmatch(text) or confirm.is_yes(text)):
        return ("approve", None)
    if EMPTY_TRASH.fullmatch(text):
        return ("empty_trash", None)
    if TRASH_INFO.fullmatch(text):
        return ("trash_info", None)
    if FILE_HISTORY.fullmatch(text):
        return ("history", {"today": bool(re.search(r"\btoday\b", text, re.I))})
    return None


def route(text, root, pending=False, has_last=False):
    """Map a spoken file request to (kind, args), or None when it is not a file command."""
    text = norm(text)
    command = _state_command(text, pending, has_last)
    if command:
        return command
    if PLANNING_RE.search(text):
        return None  # organize/sort/clean-up need a plan; the Sonnet planner takes them
    trash = parse_trash(text, root)
    if trash:
        return ("clarify", trash) if "clarification" in trash else ("trash", trash)
    if re.search(
        r"\b(?:in|into|to)\s+(?:the |my )?(?:trash|bin|trash can)\b", text, re.I
    ) and re.match(
        r"^(?:\W*(?:please|just|now|can you|could you)\s+)*(?:move|put|send|throw|drop)\b",
        text,
        re.I,
    ):
        return (
            "clarify",
            {
                "clarification": "Please name the exact files, a file type like PDFs, or a quoted name. I will not guess what to throw away."
            },
        )  # the trash is never "a folder to move into"
    simple = parse_simple_request(text, root)
    if simple:
        return simple
    args = parse(text, root)
    if args:
        return ("clarify", args) if "clarification" in args else (args["action"], args)
    if pending and re.match(
        r"^(?:yes|okay|ok|sure)\b.*\b(?:but|except|only|instead|change)\b", text, re.I
    ):
        return (
            "clarify",
            {
                "clarification": "That sounds like a change to the plan. Please state the revised file request, or say just yes to confirm the displayed plan."
            },
        )
    return None
