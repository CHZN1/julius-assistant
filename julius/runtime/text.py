"""Pure text helpers for the runtime: spoken-text cleanup, intent hints, history repair."""

import re

from julius import config
from julius.audio.speech_text import normalize_for_speech

STALL = re.compile(
    r"\b(let me (check|get|grab|look|see|pull)|i'll (get|check|grab|"
    r"pull|look)|one (sec|moment)|hang on|give me a sec|"
    r"i'm on it|coming right up|right away)\b",
    re.I,
)

TOOL_HINT = re.compile(
    r"\b(weather|forecast|rain|temperature|degrees|hot|cold|snow|"
    r"time|clock|timezone|what day|date|"
    r"price|worth|trading|stock|crypto|bitcoin|btc|eth|ticker|market|"
    r"score|scores|game|games|won|beat|standings|playing|"
    r"calculate|times|plus|minus|divided|multiply|squared|sqrt|percent|"
    r"search|look up|google|news|latest|recent|"
    r"file|files|folder|directory|list|read)\b",
    re.I,
)

# Requests that probably need the planner (cloud brain only): several steps, research, organising files.
ESCALATE_HINT = re.compile(
    r"\b(plan|organi[sz]e|reorgani[sz]e|research|compare|summari[sz]e|analy[sz]e|figure out|step by step|"
    r"walk me through|investigate|clean up|sort out|sub-?agents?|delegate|put together|and then|then also|everything)\b",
    re.I,
)

CURRENT_TIME_QUERY = re.compile(
    r"\b(what(?:'s| is) (?:the )?(?:time|date|day)|"
    r"what time is it|what day is it|"
    r"current (?:time|date)|time right now|today(?:'s)? date)\b",
    re.I,
)

SCHEDULE_QUERY = re.compile(
    r"\b(?:what time|when)\b.*\b(?:start|starts|begin|begins|kick ?off|tip ?off|puck drop|first pitch|game|match|show|playing|on tonight)\b|\b(?:game|match)\b.*\b(?:start|starts|begin|begins|what time|when)\b",
    re.I,
)


def schedule_league(text):
    """Which league a schedule question is about, from a league name or a team name; None when unclear."""
    low = (text or "").lower()
    for lg in ("nhl", "nfl", "mlb", "nba", "wnba"):
        if re.search(rf"\b{lg}\b", low):
            return lg
    cfg = config.load_config().get("asr", {})
    for lg in ("nhl", "nfl"):
        if any(re.search(rf"\b{re.escape(t.lower())}\b", low) for t in cfg.get(f"{lg}_teams", [])):
            return lg
    extra = {
        "nba": ["warriors", "lakers", "celtics", "kings"],
        "mlb": ["giants", "athletics", "dodgers", "yankees"],
        "nfl": ["49ers", "niners", "raiders"],
    }
    for lg, names in extra.items():
        if any(re.search(rf"\b{n}\b", low) for n in names):
            return lg
    return None


BAD_ID = re.compile(
    r"(just )?an? (text-based |large )?(ai|A\.I\.) (assistant|model)|"
    r"language model|i'm just (a |an )?(program|bunch of code|code|bot)|"
    r"bunch of code",
    re.I,
)

FIXES = {
    r"\bAI\b": "A.I.",
    r"\bML\b": "M.L.",
    r"\bLLM\b": "L.L.M.",
    r"\bAPI\b": "A.P.I.",
    r"\bGPU\b": "G.P.U.",
    r"\bCPU\b": "C.P.U.",
    r"\bRAM\b": "ram",
    r"\bOS\b": "O.S.",
    r"\bURL\b": "U.R.L.",
    r"\bSQL\b": "sequel",
    r"\bJSON\b": "jason",
    r"\bCSV\b": "C.S.V.",
    r"\bPDF\b": "P.D.F.",
    r"\bUSB\b": "U.S.B.",
    r"\bUI\b": "U.I.",
    r"\bUX\b": "U.X.",
    r"\bNLP\b": "N.L.P.",
    r"\bCNN\b": "C.N.N.",
    r"\bRNN\b": "R.N.N.",
    r"\bTTS\b": "T.T.S.",
    r"\bSSD\b": "S.S.D.",
    r"\bVGG\b": "V.G.G.",
    r"\bIDE\b": "I.D.E.",
    r"\bresume\b": "résumé",
    r"\bcafe\b": "café",
    r"\bnaive\b": "naïve",
    r"\bcliche\b": "cliché",
    r"\bfiance\b": "fiancé",
    r"\bdeja vu\b": "déjà vu",
    r"(\d)\s*-\s*(\d)": r"\1 to \2",
    r"\bvs\.?\b": "versus",
    r"\be\.g\.\b": "for example",
    r"\bi\.e\.\b": "that is",
    r"\betc\.\b": "et cetera",
    r"&": " and ",
    r"%": " percent",
}


def clean_for_speech(t):
    t = normalize_for_speech(
        t
    )  # markdown/URLs/emoji out; degrees, percent, money, times, dates, decimals as words
    t = re.sub(r"[#\[\]~]", "", t)
    for p, r in FIXES.items():
        t = re.sub(p, r, t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


HOLD_SECONDS = 4.0
META_LEAK = re.compile(
    r"\b(?:length guidance|no tools? (?:needed|required|necessary)|system prompt|my instructions|per my instructions|the guidance|the system (?:says|prompt|default)|(?:replies|answers) default|"
    r"he explicitly (?:asked|wants|requested)|the user (?:wants|is asking|explicitly|asked for)|spoken-friendly|so I'll (?:give|keep|go|tell) (?:him|a fuller|a proper)|without tools)\b",
    re.I,
)  # the model narrating its instructions or reasoning is never spoken


def split_long_sentence(p, limit=110):
    """The voice slows down on long sentences (the s06 story stuttered on every chunk). Speak clauses of about `limit` characters instead; the stream stays open between them."""
    p = (p or "").strip()
    if len(p) <= limit:
        return [p]
    pieces, cur = [], ""
    for clause in re.split(
        r"(?<=[,;:\u2014])\s+|\s+(?=(?:and|but|so|because|while|which|until|then)\s)", p
    ):
        if cur and len(cur) + 1 + len(clause) > limit:
            pieces.append(cur)
            cur = clause
        else:
            cur = f"{cur} {clause}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def repair_history(h):
    """Drop tool calls that have no matching tool result (and stray results): the API rejects the whole conversation otherwise, and every later turn fails."""
    out, i = [], 0
    while i < len(h):
        m = h[i]
        if m.get("tool_calls"):
            n = len(m["tool_calls"])
            results = []
            j = i + 1
            while j < len(h) and h[j].get("role") == "tool":
                results.append(h[j])
                j += 1
            if len(results) >= n:
                out.append(m)
                out.extend(results)
            i = j
            continue
        if m.get("role") == "tool":
            i += 1
            continue
        out.append(m)
        i += 1
    h[:] = out
