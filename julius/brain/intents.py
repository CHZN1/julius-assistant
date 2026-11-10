"""Conservative routing for live requests, retaining tool context across follow-ups."""

import re

from julius import profile
from julius.audio.listening import normalize_request
from julius.audio.speech_text import EXACT_REQUEST, speakable
from julius.audio.spoken_math import arithmetic_expression

CRYPTO_NAMES = {"bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH", "solana": "SOL"}

REGIONS = set(
    x.lower()
    for x in [
        "Alabama",
        "Alaska",
        "Arizona",
        "Arkansas",
        "California",
        "Colorado",
        "Connecticut",
        "Delaware",
        "Florida",
        "Georgia",
        "Hawaii",
        "Idaho",
        "Illinois",
        "Indiana",
        "Iowa",
        "Kansas",
        "Kentucky",
        "Louisiana",
        "Maine",
        "Maryland",
        "Massachusetts",
        "Michigan",
        "Minnesota",
        "Mississippi",
        "Missouri",
        "Montana",
        "Nebraska",
        "Nevada",
        "Ohio",
        "Oklahoma",
        "Oregon",
        "Pennsylvania",
        "Tennessee",
        "Texas",
        "Utah",
        "Vermont",
        "Virginia",
        "Washington",
        "Wisconsin",
        "Wyoming",
        "CA",
        "NY",
        "TX",
        "FL",
        "WA",
        "OR",
        "AZ",
        "NV",
        "UT",
        "CO",
        "IL",
        "MA",
        "NJ",
        "PA",
        "OH",
        "MI",
        "GA",
        "NC",
        "SC",
        "VA",
        "Japan",
        "France",
        "Germany",
        "Italy",
        "Spain",
        "Canada",
        "Mexico",
        "England",
        "UK",
        "USA",
        "China",
        "India",
        "Brazil",
        "Australia",
        "Ireland",
        "Scotland",
        "Korea",
    ]
) | {
    "new york",
    "new jersey",
    "new hampshire",
    "new mexico",
    "north carolina",
    "south carolina",
    "north dakota",
    "south dakota",
    "rhode island",
    "west virginia",
    "south korea",
}


_DECLINE = object()  # sentinel: this request belongs to the brain, not a single-tool shortcut
_PLACES_NOT_ONE = {"today", "tomorrow", "this week", "right now", "the week"}
_DATE_QUESTION = (
    r"what(?: is|.s)? (?:the )?(?:current |today.s )?date(?: (today|tomorrow|yesterday))?"
    r"|what day (?:is it|is today|it is)(?: (today|tomorrow|yesterday))?"
    r"|what day (is tomorrow|was yesterday|is today)"
)
_TOPICS = (
    r"\b(?:weather|forecast)\b",
    r"\b(?:what time|current time|what day|date|the time)\b",
    r"\b(?:price|worth|trading|quote|stock)\b|\bbitcoin\b",
    r"\b(?:news|headlines)\b",
    r"\b(?:scores?|results)\b",
)
_PATTERN_TOOLS = (  # (regex, tool, builder of args from the match)
    (
        r"(?:convert )?(-?\d+(?:\.\d+)?) ([A-Za-z_°]+) (?:to|in) ([A-Za-z_°]+)",
        "convert_units",
        lambda m: {"value": float(m.group(1)), "from_unit": m.group(2), "to_unit": m.group(3)},
    ),
    (
        r'(?:read document|extract text from) "([^"\n]+)"',
        "read_document",
        lambda m: {"path": m.group(1)},
    ),
    (
        r'search document "([^"\n]+)" for "([^"\n]+)"',
        "read_document",
        lambda m: {"path": m.group(1), "query": m.group(2)},
    ),
    (
        r"(?:what(?: is|.s| are)(?: the)? |show me |tell me |get )?(?:latest |today.s |recent )?(?:news|headlines)(?: (?:about|on|for) (.+))?",
        "get_news",
        lambda m: {"query": m.group(1) or "top news"},
    ),
    (
        r"(?:what(?: are|.s)(?: the)? |show me |get )?(mlb|nba|nfl|nhl|wnba) (?:scores|results)(?: (today|yesterday))?",
        "get_scores",
        lambda m: {"league": m.group(1).lower(), "day": (m.group(2) or "today").lower()},
    ),
    (
        r"(?:what(?: is|.s)(?: the)? |get |show me )?(?:price|quote|stock price) (?:of|for) ([A-Za-z^.=-]{1,24})",
        "get_quote",
        lambda m: {"symbol": m.group(1).upper()},
    ),
)


def _clean(text: str) -> str:
    """Drop the vision suffix and trailing punctuation."""
    return re.sub(r"\s*\[you can see:.*", "", text, flags=re.S).strip().rstrip("?.!")


def _brain_request(low: str) -> bool:
    """Compound asks (plans, comparisons, several lookups in one sentence) go to the brain."""
    if re.search(
        r"\b(?:plan|trip|itinerary|compare|organi[sz]e|research|step by step|and then)\b", low
    ):
        return True
    if re.search(r"\b(?:weather|forecast)\b", low) and re.search(
        r"\b(?:what time|current time|what day|date)\b", low
    ):
        return True
    if re.search(r"\b(?:weather|forecast|time)\b", low) and re.search(
        r"\b(?:in|for)\s+[^,]+?\s+and\s+\w", low
    ):
        return True  # "weather in Springfield and Tokyo"
    return sum(bool(re.search(x, low)) for x in _TOPICS) > 1  # two different asks in one request


def _location(t: str):
    """The single place named in the request, None, or _DECLINE when it is not one place."""
    loc = re.search(r"\bin\s+(.+)$", t, re.I) or re.search(r"\bfor\s+(.+)$", t, re.I)
    location = loc.group(1).strip() if loc else None
    if location and location.lower() in _PLACES_NOT_ONE:
        location = None
    if location:
        location = re.sub(
            r"\s+(?:for\s+)?(?:today|tomorrow|right now|this week)$", "", location, flags=re.I
        )
    if not location:
        return location
    # One place only: "City" or "City, State"; lists, "and"/"or" or extra clauses are not one.
    if not re.fullmatch(
        r"[A-Za-z][A-Za-z.' -]{1,40}(?:, ?[A-Za-z][A-Za-z.' -]{1,30})?", location
    ) or re.search(r"\b(?:and|or|plus|then)\b", location, re.I):
        return _DECLINE
    if "," in location and location.split(",")[1].strip().lower() not in REGIONS:
        return _DECLINE  # "Shelbyville, Springfield" is two places
    return location


def _pattern_tool(t: str):
    for pattern, tool, build in _PATTERN_TOOLS:
        m = re.fullmatch(pattern, t, re.I)
        if m:
            return tool, build(m)
    return None


def _calculator_or_bitcoin(low: str):
    expression = arithmetic_expression(low)
    if expression is not None:
        return "calculate", {"expression": expression}
    if re.search(r"\bbitcoin\b.*\b(?:trading|price|worth)\b", low):
        return "get_quote", {"symbol": "BTC-USD"}
    return None



class LiveIntentRouter:
    def __init__(self):
        self.last = None
        self.relative_only = False

    def resolve(self, text: str) -> tuple[str, dict] | None:
        """Map a request to one (tool, args) shortcut, or None when the brain should handle it."""
        self.relative_only = False
        self.exact = bool(EXACT_REQUEST.search(text))
        t = _clean(normalize_request(text))
        t_and = _clean(normalize_request(text, keep_and=True))  # follow-ups that start with "and"
        low = t.lower()
        location = _location(t) if not _brain_request(low) else _DECLINE
        if location is _DECLINE:
            return self._decline()
        for step in (self._clock, self._weather):
            found = step(low, location)
            if found is not None:
                return self._decline() if found is _DECLINE else found
        found = _pattern_tool(t)
        if found is None and self.last:
            found = self._follow_up(t, t_and, low, location)
        if found is None:
            found = _calculator_or_bitcoin(low)
        if found is None and not re.fullmatch(r"(?:thanks|thank you|okay|ok|got it)", low):
            self.last = None
        return found

    def _decline(self) -> None:
        """Hand the request to the brain and forget the last lookup."""
        self.last = None
        return None

    def _clock(self, low: str, location: str | None):
        """Time and date questions (today, tomorrow or yesterday only)."""
        if re.search(
            r"\bwhat time (?:does|do|will|did|are|is the|is that|is it (?:on|at|for))\b", low
        ):  # "what time does the game start" is a schedule question, not the clock
            return _DECLINE
        if not re.search(
            r"\b(?:what time|current time|what day|current date|today.s date|what(?:.s| is) (?:the )?date)\b",
            low,
        ):
            return None
        if re.search(r"\b(?:what day|date)\b", low):
            if not re.fullmatch(_DATE_QUESTION, low):
                return _DECLINE
            word = next((w for w in ("tomorrow", "yesterday") if w in low), None)
            if word and not location:
                return "get_datetime", {"offset_days": 1 if word == "tomorrow" else -1}
        return "get_datetime", {"location": location} if location else {}

    def _weather(self, low: str, location: str | None):
        if not re.search(r"\b(?:weather|forecast)\b", low) or re.match(
            r"^(?:explain|why|teach|what is (?:weather|a forecast)|i |we )", low
        ):
            return None
        fn = "get_forecast" if re.search(r"forecast|tomorrow|week", low) else "get_weather"
        if not (location or profile.home_city()):
            return _DECLINE  # no city and no home city: let the model ask which
        args = {"location": location or profile.home_city()}
        if fn == "get_forecast":
            args["days"] = 2 if "tomorrow" in low else 7
        return fn, args

    def _follow_up(self, t: str, t_and: str, low: str, location: str | None):
        """Short follow-ups that reuse the previous lookup ("and tomorrow?", "and in Paris?")."""
        fn, args, _out = self.last
        for step in (self._what_about, self._place_swap, self._other_symbol, self._tomorrow):
            found = step(fn, args, t, t_and, location)
            if found is not None:
                return found
        if fn in ("get_weather", "get_forecast") and re.fullmatch(r"is that for .+", t, re.I):
            return fn, dict(args, location=re.fullmatch(r"is that for (.+)", t, re.I).group(1))
        if fn == "get_datetime" and re.search(r"how many hours (?:ahead|behind)", low):
            self.relative_only = True
            return fn, args.copy()
        return None

    @staticmethod
    def _what_about(fn, args, t, _t_and, location):
        about = re.sub(
            r"^and\s+(?=(?:what|how) about\b)", "", t, flags=re.I
        )  # "And what about tomorrow?"
        asked = re.fullmatch(r"(?:what|how) about (.+)", about, re.I)
        if not asked:
            return None
        target = re.sub(r"^(?:in|for)\s+", "", asked.group(1), flags=re.I)
        if fn in ("get_weather", "get_forecast") and re.fullmatch(
            r"tomorrow(?: in .+)?", target, re.I
        ):
            return "get_forecast", {
                "location": location or args.get("location") or profile.home_city(),
                "days": 2,
            }
        if fn in ("get_datetime", "get_weather", "get_forecast"):
            return fn, dict(args, location=target)
        return None

    @staticmethod
    def _place_swap(fn, args, t, _t_and, _location):
        """"and in Shelbyville?" / "and for Tokyo?" repeat the last lookup somewhere else."""
        place = re.fullmatch(r"(?:and )?(?:what about |how about )?(?:in|for) (.+)", t, re.I)
        if place and fn in ("get_datetime", "get_weather", "get_forecast"):
            return fn, dict(args, location=place.group(1).strip())
        return None

    @staticmethod
    def _other_symbol(fn, _args, _t, t_and, _location):
        """"and ETH?" / "what about Tesla?" repeats the last price lookup for another symbol."""
        other = re.fullmatch(
            r"(?:and |what about |how about )(?:and )?(?:for )?([A-Za-z^.=-]{1,12})", t_and, re.I
        )
        if (
            other
            and fn == "get_quote"
            and other.group(1).lower()
            not in {"tomorrow", "today", "yesterday", "that", "this", "it"}
        ):
            name = other.group(1)
            return "get_quote", {"symbol": CRYPTO_NAMES.get(name.lower(), name.upper())}
        return None

    @staticmethod
    def _tomorrow(fn, args, _t, t_and, location):
        """"and tomorrow" / "tomorrow?" after a weather answer."""
        if fn in ("get_weather", "get_forecast") and re.fullmatch(
            r"(?:and |what about |how about )?tomorrow(?: in .+)?", t_and, re.I
        ):
            return "get_forecast", {
                "location": location or args.get("location") or profile.home_city(),
                "days": 2,
            }
        return None

    def record(self, fn, args, out):
        self.last = (fn, dict(args), out)

    def spoken_result(self, fn, out, args=None):
        if fn == "get_datetime" and self.relative_only and " — " in out:
            return out.split(" — ", 1)[1].capitalize()
        ready = speakable(
            fn, out, args, exact=getattr(self, "exact", False)
        )  # ready-to-speak sentence for weather, forecast, time, calculator, price
        if ready:
            return ready
        if fn == "get_quote" and ". 52-week range" in out:
            return out.split(". 52-week range", 1)[0] + "."
        if fn == "read_document":
            lines = out.splitlines()
            return " ".join(lines[:2])[:500] + (
                " The remaining extracted text and citations are on screen."
                if len(lines) > 2
                else ""
            )
        if fn == "get_news":
            return " ".join(
                line for line in out.splitlines() if not line.startswith(("https://", "http://"))
            )
        lines = out.splitlines()
        if fn == "calculate" and " = " in out:
            return "That's " + out.split(" = ", 1)[1] + "."
        if fn == "get_forecast" and (args or {}).get("days") == 2 and len(lines) == 3:
            return lines[0] + " Tomorrow, " + lines[2]
        if fn == "get_forecast" and len(lines) > 3:
            return " ".join(lines[:3])
        return out
