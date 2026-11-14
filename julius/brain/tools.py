"""Tools Julius can call. Each returns a short string he reads aloud."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

import julius.agents.files as jf
from julius.brain.dispatcher import Dispatcher

SAFE_ROOT = Path.home()


def _safe(p: Any) -> Any:
    import julius.agents.files as jf

    return jf.agent().resolve(p)


def calculate(expression: str, **_) -> str:
    """Bounded arithmetic parsed as AST; no Python evaluation or symbolic execution."""
    from julius.brain.calculator import evaluate

    return f"{expression} = {evaluate(expression):,.10g}"


def get_datetime(location: None = None, offset_days: int = 0, **_) -> str:
    """Local time, or time in another place if location is given. offset_days=1/-1 gives tomorrow's/yesterday's date (local only)."""
    n = datetime.now().astimezone()
    if offset_days and not location:
        return (
            (n + timedelta(days=int(offset_days))).strftime("%A, %B %-d %Y ")
            + "("
            + str(n.tzinfo)
            + ")"
        )
    if not location:
        return n.strftime("%A, %B %-d %Y, %-I:%M %p ") + str(n.tzinfo)
    g = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1},
        timeout=10,
    ).json()
    if not g.get("results"):
        return f"Couldn't find {location}."
    r = g["results"][0]
    tz = r.get("timezone")
    if not tz:
        return f"No timezone data for {r['name']}."
    there = datetime.now(ZoneInfo(tz))
    diff = (there.utcoffset() - n.utcoffset()).total_seconds() / 3600
    rel = (
        "same time as you"
        if abs(diff) < 0.1
        else f"{abs(diff):g} hours {'ahead of' if diff > 0 else 'behind'} you"
    )
    return f"{r['name']}: {there.strftime('%A, %B %-d, %-I:%M %p')} ({tz}) — {rel}."


NO_HOME = "No city was given and no home city is set. Ask which city, or set home_city under [profile] in julius.local.toml."


def _home_or_ask() -> str:
    from julius import profile

    return profile.home_city()


def get_weather(location: None = None, **_) -> str:
    location = location or _home_or_ask()
    if not location:
        return NO_HOME
    g = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1},
        timeout=10,
    ).json()
    if not g.get("results"):
        return f"Couldn't find {location}."
    r = g["results"][0]
    w = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=10,
    ).json()
    c, d = w["current"], w["daily"]
    CODES = {
        0: "clear",
        1: "mostly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "foggy",
        51: "light drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        71: "light snow",
        80: "showers",
        95: "thunderstorms",
    }
    return (
        f"{r['name']}: {round(c['temperature_2m'])}°F, "
        f"{CODES.get(c['weather_code'], 'unsettled')}, feels like "
        f"{round(c['apparent_temperature'])}. High {round(d['temperature_2m_max'][0])}, "
        f"low {round(d['temperature_2m_min'][0])}."
    )


def get_forecast(location: None = None, days: int = 5, **_) -> str:
    location = location or _home_or_ask()
    if not location:
        return NO_HOME
    g = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1},
        timeout=10,
    ).json()
    if not g.get("results"):
        return f"Couldn't find {location}."
    r = g["results"][0]
    w = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,weather_code",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": max(1, min(int(days), 16)),
        },
        timeout=10,
    ).json()
    d = w["daily"]
    CODES = {
        0: "clear",
        1: "mostly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "fog",
        51: "drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        71: "snow",
        80: "showers",
        95: "storms",
    }
    lines = []
    for i, day in enumerate(d["time"]):
        lines.append(
            f"{datetime.fromisoformat(day).strftime('%a')}: "
            f"{round(d['temperature_2m_max'][i])}/{round(d['temperature_2m_min'][i])}, "
            f"{CODES.get(d['weather_code'][i], 'mixed')}, "
            f"{d['precipitation_probability_max'][i]}% rain"
        )
    return f"{r['name']} forecast:\n" + "\n".join(lines)


def get_scores(league: str = "mlb", day: str = "today", **_) -> str:
    """Scores for a day or a short span. day: today, yesterday, last night, a weekday (Sunday), a date (2026-10-25, Oct 25) or last week."""
    from julius.brain.score_days import resolve_days

    paths = {
        "mlb": "baseball/mlb",
        "nba": "basketball/nba",
        "nfl": "football/nfl",
        "nhl": "hockey/nhl",
        "wnba": "basketball/wnba",
    }
    p = paths.get(league.lower())
    if not p:
        return f"Don't have scores for {league}."
    url = f"https://site.api.espn.com/apis/site/v2/sports/{p}/scoreboard"
    first, last, day = resolve_days(day)
    stamp = (
        first.strftime("%Y%m%d")
        if first == last
        else f"{first.strftime('%Y%m%d')}-{last.strftime('%Y%m%d')}"
    )
    params = {"dates": stamp, "limit": 100}
    j = requests.get(url, params=params, timeout=10).json()
    out = []
    for e in j.get("events", []):
        c = e["competitions"][0]
        home = next(x for x in c["competitors"] if x["homeAway"] == "home")
        away = next(x for x in c["competitors"] if x["homeAway"] == "away")
        stt = e["status"]["type"]
        state = "FINAL" if stt["completed"] else "LIVE" if stt["state"] == "in" else "SCHEDULED"
        hn, an = home["team"]["displayName"], away["team"]["displayName"]
        if state == "SCHEDULED":
            out.append(f"SCHEDULED: {an} at {hn}, {stt['shortDetail']}")
        else:
            hs, as_ = int(home.get("score", 0)), int(away.get("score", 0))
            if hs == as_:
                out.append(f"{state}: {an} and {hn} tied {hs}-{as_}")
                continue
            won, lost = ((hn, hs), (an, as_)) if hs > as_ else ((an, as_), (hn, hs))
            out.append(
                f"{state}: {won[0]} {'beat' if state == 'FINAL' else 'leads'} {lost[0]} {won[1]}-{lost[1]}"
            )
    if len(out) > 25:
        out = out[:25] + [f"…and {len(out) - 25} more games."]
    scores = (
        (f"{league.upper()} ({day}) — {len(out)} games:\n" + "\n".join(out))
        if out
        else f"No {league.upper()} games {day}."
    )
    retrieved = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return scores + f"\nSource: ESPN scoreboard; retrieved {retrieved}."


def find_files(pattern: Any, source: str = ".", recursive: bool = True, **_) -> Any:
    import julius.agents.files as jf

    return jf.render(jf.agent().find(pattern=pattern, source=source, recursive=recursive))


def get_quote(symbol: str, days: int = 0, **_) -> str:
    """Live price for a stock or crypto. Accepts a ticker or a company/crypto name (julius_tickers); crypto uses BTC-USD style tickers."""
    import julius.brain.tickers as tk

    found = tk.resolve(symbol)
    if found.ambiguous:
        return tk.ambiguity_message(symbol.strip(), found.ambiguous)
    if not found.symbol:
        return f"No data for {symbol.strip()}."
    s = found.symbol
    if s in tk.CRYPTO_CODES:
        s = f"{s}-USD"

    try:
        days = max(0, min(int(days or 0), 30))
    except (TypeError, ValueError):
        days = 0

    def chart(sym: Any) -> Any:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"range": "3mo" if days else "2d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        ).json()
        return (r.get("chart") or {}).get("result")

    res = chart(s)
    if (
        not res and found.how == "ticker"
    ):  # it looked like a ticker but is not one: maybe it was a name in capitals
        again = tk.resolve(symbol.strip().title())
        if again.ambiguous:
            return tk.ambiguity_message(symbol.strip(), again.ambiguous)
        if again.symbol and again.symbol != s:
            s = again.symbol
            res = chart(s)
    if not res:
        return f"No data for {s}."
    meta = res[0]["meta"]
    if days:
        return _closing_prices(s, meta, res[0], days)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return f"No price for {s}."
    name = meta.get("shortName", s)
    line = f"{name} ({s}): {price:,.2f} {meta.get('currency', 'currency unknown')}"
    if prev:
        d = price - prev
        line += f", {'up' if d >= 0 else 'down'} {abs(d):,.2f} ({abs(d / prev) * 100:.2f}%) versus previous close"
    hi, lo = meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow")
    if hi and lo:
        line += (
            f". 52-week range {lo:,.2f} to {hi:,.2f} {meta.get('currency', 'currency unknown')}."
        )
    stamp = meta.get("regularMarketTime")
    if stamp:
        line += " As of " + datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d %H:%M UTC") + "."
    else:
        line += " Quote time unavailable."
    return line + " Source: Yahoo Finance; quotes may be delayed."


def _closing_prices(symbol: str, meta: dict, result: dict, days: int) -> str:
    """The last `days` daily closes, newest last, one line each (for a chart or a table)."""
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = [
        (datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d"), c)
        for t, c in zip(result.get("timestamp") or [], closes, strict=False)
        if c is not None
    ][-days:]
    if not rows:
        return f"No closing prices for {symbol}."
    cur = meta.get("currency", "currency unknown")
    lines = "\n".join(f"{d}: {c:,.2f}" for d, c in rows)
    return (
        f"{meta.get('shortName', symbol)} ({symbol}) daily closing prices, last {len(rows)} trading days ({cur}):\n{lines}\n"
        "Source: Yahoo Finance; quotes may be delayed."
    )


def get_news(query: str, **_) -> dict[str, Any]:
    from ddgs import DDGS

    with DDGS(timeout=10) as d:
        hits = list(d.news(query, timelimit="d", max_results=3))[:3]
    if not hits:
        return {
            "ok": False,
            "code": "no_results",
            "message": "No recent news results found.",
            "data": {},
        }
    items = [{k: h.get(k, "") for k in ("title", "date", "source", "url", "body")} for h in hits]
    message = "News search results (headlines and snippets, articles not verified):\n" + "\n".join(
        f"{h['title']} — {h['source']} ({h['date'] or 'date unavailable'})\n{h['url']}"
        for h in items
    )
    return {"ok": True, "code": "completed", "message": message, "data": {"items": items}}


def web_search(query: str, n: int = 3, **_) -> Any:
    from ddgs import DDGS

    with DDGS(timeout=10) as d:
        hits = list(d.text(query, max_results=n))
    if not hits:
        return "Nothing found."
    return "\n".join(f"{h['title']}: {h['body'][:200]}\n{h.get('href', '')}" for h in hits)


def list_files(directory: str = ".", **_) -> Any:
    import julius.agents.files as jf

    return jf.render(jf.agent().list(directory))


def read_file(path: str | Path, max_chars: int = 2000, **_) -> Any:
    import julius.agents.files as jf

    return jf.render(jf.agent().read(path, max_chars))


REGISTRY = {
    "get_datetime": get_datetime,
    "get_weather": get_weather,
    "get_forecast": get_forecast,
    "get_scores": get_scores,
    "calculate": calculate,
    "get_quote": get_quote,
    "web_search": web_search,
    "list_files": list_files,
    "read_file": read_file,
}

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Current date and time. Give a location for time elsewhere, "
            "otherwise returns local time. Omit location for local time; never use a placeholder. Only for what time or date it is NOW; "
            "never for when a game, show or event starts or is scheduled (use get_scores for games), and never for calendar, meeting, schedule or reminder questions (use list_planner_items).",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City, e.g. 'Tokyo'"},
                    "offset_days": {
                        "type": "integer",
                        "description": "Local date only: 1 for tomorrow, -1 for yesterday. For any other date use date_calculate.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Current weather and today's high/low. Omit location to use Springfield, Illinois.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City, e.g. 'Springfield, Illinois'",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": "Multi-day weather forecast for a place. Use when asked about "
            "upcoming days, trips, or 'this week'. Omit location to use Springfield, Illinois.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Requested city; omit to use Springfield, Illinois",
                    },
                    "days": {"type": "integer", "description": "How many days ahead, 1-16"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for CURRENT information you cannot know — recent "
            "news, today's events, new releases. Do NOT use for jokes, "
            "opinions, conversation, general knowledge, explanations, or "
            "anything you can answer yourself.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a folder under the user's Documents directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Path relative to Documents"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the beginning of a text file under Documents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_scores",
            "description": "Game scores and schedules for one day or a short span: today, yesterday, last night, "
            "a weekday like Sunday (the most recent one), a date, or last week. Not for player stats, box scores, "
            "past seasons or championships — answer those from your own knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "league": {"type": "string", "description": "one of: mlb, nba, nfl, nhl, wnba"},
                    "day": {
                        "type": "string",
                        "description": "'today', 'yesterday', 'last night', 'Sunday', 'last week' or a date such as '2026-10-25'",
                    },
                },
                "required": ["league"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Calculate bounded arithmetic with +, -, *, /, **, parentheses, sqrt, abs, pi and e. MUST be used for anything beyond trivial mental math: decimals, percentages, several operations, or any number over two digits (only a single small whole-number operation like 7 times 8 may be answered without it). For percentages write 'X% of Y' (for example '17.5% of 2480'); a bare % is modulo. Algebra and calculus are not supported.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "Latest available, possibly delayed price for a stock or cryptocurrency. Pass the ticker (AAPL, TSLA, SPY, BTC, ETH) "
            "or just the company or coin name as the user said it (Apple, Home Depot, Bitcoin): names are resolved for you. "
            "If the result starts with AMBIGUOUS, ask the user which company he means. "
            "For a price history (a chart or table over the last N days) pass days: you get that many daily closing prices. "
            "ALWAYS use this for prices — never web_search, never guess.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker or company/crypto name, e.g. 'AAPL', 'Apple' or 'Bitcoin'",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Optional: return the last N daily closing prices (1-30) instead of the latest quote",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
]

# Mutations are deliberately absent from model tools. The human-command
# FileSession handles preview/confirm/execute; a model assertion cannot approve.
REGISTRY["find_files"] = find_files
SCHEMAS += [
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find existing files by filename pattern inside Documents, Desktop, or Downloads. Search is bounded; report truncation honestly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "source": {"type": "string"},
                    "recursive": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
    }
]

REGISTRY["get_news"] = get_news
SCHEMAS.append(
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "Search recent news with publisher, publication date and source URL. Results are headlines/snippets, not verified full articles.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
)

_planner = None


def planner() -> Any:
    global _planner
    if _planner is None:
        from julius.brain.planning import Planner

        _planner = Planner(jf.STATE)
    return _planner


def list_planner_items(kind: str | None = None) -> Any:
    return planner().list(kind)


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    from julius.brain.conversions import convert_units as convert

    return convert(value, from_unit, to_unit)


def date_calculate(
    start: Any,
    operation: Any,
    timezone_name: str = "America/Los_Angeles",
    amount: int = 0,
    end: Any | None = None,
    target_timezone: Any | None = None,
) -> Any:
    from julius.brain.conversions import date_calculate as compute

    return compute(start, operation, timezone_name, amount, end, target_timezone)


def _utility_schema(
    name: str, description: str, properties: dict[str, str], required: list[Any | str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": v} for k, v in properties.items()},
                "required": required,
            },
        },
    }


REGISTRY.update(
    list_planner_items=list_planner_items,
    convert_units=convert_units,
    date_calculate=date_calculate,
)
SCHEMAS += [
    _utility_schema(
        "list_planner_items",
        "The user's calendar and to-do list: read their active events, meetings, tasks and reminders. ALWAYS use this for any question about what is on the calendar, scheduled, planned, due or coming up (what's on my calendar tomorrow, anything scheduled Friday, do I have meetings, what's my day look like); it returns every active item with its date, so pick the ones that match the day asked. Never answer those from get_datetime. Only explicit human commands can add, complete or cancel items.",
        {"kind": "string"},
        [],
    ),
    _utility_schema(
        "convert_units",
        "Convert dimension-compatible units. Supports length, mass, time, volume and Celsius/Fahrenheit/Kelvin. US volume units require us_gal or us_cup. No currency conversion.",
        {"value": "number", "from_unit": "string", "to_unit": "string"},
        ["value", "from_unit", "to_unit"],
    ),
    _utility_schema(
        "date_calculate",
        "Compute explicit ISO dates/times with IANA time zones. Operations: add_days (calendar days), add_hours (elapsed hours), difference (elapsed hours), convert_timezone. Rejects ambiguous/nonexistent local times.",
        {
            "start": "string",
            "operation": "string",
            "timezone_name": "string",
            "amount": "number",
            "end": "string",
            "target_timezone": "string",
        },
        ["start", "operation"],
    ),
]


def read_document(path: str, query: None = None) -> dict[str, Any]:
    from julius.brain.documents import read_document as extract

    return extract(path, query)


REGISTRY["read_document"] = read_document
SCHEMAS.append(
    _utility_schema(
        "read_document",
        "Extract bounded PDF/DOCX/UTF-8 text with page/paragraph/line citations, or search an exact phrase. No OCR; report partial extraction and unsupported formats honestly. File scope is Documents/Desktop/Downloads.",
        {"path": "string", "query": "string"},
        ["path"],
    )
)

_reminders = None


def reminders() -> Any:
    global _reminders
    if _reminders is None:
        import os

        from julius.agents.utility_session import UtilitySession
        from julius.brain import orchestrator
        from julius.brain.apple import AppleProvider
        from julius.brain.reminders import ReminderService

        session = UtilitySession(
            planner(),
            os.environ.get("JULIUS_TIMEZONE", "America/Los_Angeles"),
            AppleProvider(planner()),
        )
        _reminders = ReminderService(session, lambda: orchestrator._STATE["user_text"])
    return _reminders


def set_reminder(title: str, when: Any) -> Any:
    return reminders().set(title, when)


def list_reminders() -> Any:
    return reminders().list()


def cancel_reminder(title: str) -> Any:
    return reminders().cancel(title)


REGISTRY.update(
    set_reminder=set_reminder, list_reminders=list_reminders, cancel_reminder=cancel_reminder
)
SCHEMAS += [
    _utility_schema(
        "set_reminder",
        "Set a reminder when the person asks to be reminded. 'title' is what to remind them of, e.g. 'call mom'. 'when' is their own time phrase: 'at 3:45 PM', 'tomorrow morning', 'in 20 minutes', 'friday at 9am'. The result is a sentence to speak back.",
        {"title": "string", "when": "string"},
        ["title", "when"],
    ),
    _utility_schema("list_reminders", "List active reminders with their times.", {}, []),
    _utility_schema(
        "cancel_reminder",
        "Cancel an active reminder by its title when the person asks to cancel or delete it.",
        {"title": "string"},
        ["title"],
    ),
]

# Memory tools: a backup for when a phrasing slips past the memory shortcut. They only act on what the user said this turn, so text from a web page or a file cannot write or erase memory.
memory_assistant = None


def _said_this_turn() -> str:
    from julius.brain import orchestrator

    return orchestrator._STATE.get("user_text") or ""


def _grounded(text: str, utterance: str, need: float) -> bool:
    from julius.memory.store import words

    w = set(words(text))
    u = set(words(utterance))
    return bool(w) and len(w & u) / len(w) >= need


def remember_fact(text: str) -> str:
    if memory_assistant is None:
        return "Memory is not available."
    if not _grounded(text, _said_this_turn(), 0.6):
        return "Not saved: that is not something the user said just now."
    return memory_assistant._remember(text, _said_this_turn())


def forget_fact(topic: str = "") -> str:
    if memory_assistant is None:
        return "Memory is not available."
    topic = (topic or "").strip()
    if not topic:
        return memory_assistant._forget_that()
    if not _grounded(topic, _said_this_turn(), 0.01):
        return "Not deleted: that topic is not what the user just asked to forget. Ask him what to forget."
    return memory_assistant._forget(topic)


def list_memories(topic: str = "") -> str:
    if memory_assistant is None:
        return "Memory is not available."
    return memory_assistant._list((topic or "").strip() or None)


# File and coding backup tools: when a phrasing slips past the deterministic routers, Haiku hands the user's request to the same human sessions instead of declining.
# They stage previews only (the sessions own every yes), and they act on what the user said this turn.
file_session = None
code_session = None


def _norm_name(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9.]", "", str(s).casefold().replace(" dot ", "."))


def file_request(request: str) -> str:
    import re

    if file_session is None:
        return "File commands are not available."
    said, req = _said_this_turn().strip(), (request or "").strip()
    names = re.findall(r"[\w][\w.-]*\.[A-Za-z0-9]{1,8}\b", req)
    if not req or any(_norm_name(n) not in _norm_name(said) for n in names):
        req = said  # file names must be ones the user actually said
    turn = jf._TURN["n"]
    out = file_session.handle(req, turn)
    if out is None and req != said and said:
        out = file_session.handle(said, turn)
    if out is None:
        return "That is not a file command the controller understood. Ask the user which file and folder, e.g. 'trash report.txt in Downloads'. Do not say you cannot do file work."
    return out["message"] + (
        " (Preview only: nothing changes until the user says yes.)"
        if out.get("code") in ("preview", "pending_status")
        else ""
    )


def code_request(request: str = "", goal: str = "", project: str = "") -> str:
    if code_session is None:
        return "The coding agent is not available."
    said, turn = _said_this_turn().strip(), jf._TURN["n"]
    if goal and project:
        out = code_session.stage(goal, project)
    else:
        out = code_session.handle((request or said).strip(), turn) or (
            code_session.handle(said, turn) if said else None
        )
    if out is None:
        return "That is not a coding-agent command I understood. The user can say 'have the coding agent fix the failing tests in <project>', then yes to start; later 'show the changes', 'merge it', 'undo the merge' or 'cancel the coding task'."
    return out["message"] + (
        " (Staged only: nothing runs or merges until the user says yes.)"
        if out.get("code") == "preview"
        else ""
    )


def render_chart(
    kind: str = "",
    title: str = "",
    labels: list | None = None,
    series: list | None = None,
    unit: str = "",
    columns: list | None = None,
    rows: list | None = None,
) -> str:
    from julius.ui_bridge import charts

    return charts.render_chart(
        {
            "kind": kind,
            "title": title,
            "labels": labels,
            "series": series,
            "unit": unit,
            "columns": columns,
            "rows": rows,
        }
    )


def research_task(question: str = "") -> str:
    import julius.agents.research as research

    return research.research_task({"question": question})


REGISTRY.update(
    research_task=research_task,
    remember_fact=remember_fact,
    forget_fact=forget_fact,
    list_memories=list_memories,
    file_request=file_request,
    code_request=code_request,
    render_chart=render_chart,
)


def analyze_audio(path: str = "") -> str:
    from julius.music import tool as music_tool

    return music_tool.analyze_audio(path)


REGISTRY["analyze_audio"] = analyze_audio


def find_samples(query: str = "", bpm: int = 0, key: str = "") -> str:
    from julius.music import tool as music_tool

    return music_tool.find_samples(query, bpm, key)


REGISTRY["find_samples"] = find_samples
SCHEMAS.append(
    _utility_schema(
        "find_samples",
        "Search the user's own sample and loop library by name, BPM or key as written in file names ('find my loops around 150 BPM'). Also use it for 'update my sample index'. Read-only; approved folders only.",
        {"query": "string", "bpm": "integer", "key": "string"},
        [],
    )
)


def music_theory(
    key: str = "", progression: str = "", chord: str = "", scale: str = "", style: str = ""
) -> str:
    from julius.music import tool as music_tool

    return music_tool.music_theory(key, progression, chord, scale, style)


REGISTRY["music_theory"] = music_theory
SCHEMAS.append(
    _utility_schema(
        "music_theory",
        "Music theory help, computed exactly (note names are never guessed): the notes of a key, scale or mode and its chords; the notes, inversions and voicings of a chord; or the analysis and smooth voicing of a chord progression, with a piano and chord chart in the side panel. Give ONE of: 'key' alone for 'what chords go with A minor' / 'chords in Bb major'; 'scale' for a scale or mode ('D dorian', 'A blues', 'G harmonic minor'); 'chord' for a chord ('Cmaj9', 'F#m7b5') with optional 'key'; 'progression' ('ii V I', 'Am F C G', 'i VI III VII') with 'key' (required for Roman numerals, optional for chord names). Add 'style' for a genre or era (reggae or one drop, dub, neo-soul or lo-fi, Motown or soul, jazz, blues): 'one drop reggae progression' = style 'one drop reggae'; 'dub bassline chords' = style 'dub'; 'neo-soul voicings for Dm9' = style 'neo-soul' + chord 'Dm9'; 'Motown-style ii-V' = style 'Motown' + progression 'ii V'. Genre and era requests are normal requests: ALWAYS answer them with this tool. Extended and altered chords work (9, 11, 13, maj9, 6/9, 7b9, 7#9, 7#11, 7b13, 7alt, m7b5). ALWAYS call it when the user names specific chords or a progression (even 'is Am F C G a good progression?'), a scale or mode, or asks what chords or notes belong to a key: never answer those from memory, and never give note names that did not come from this tool. Say the result briefly and musically, using only the notes it returned. Suggestions only: you never write to the user's projects and never imitate a named artist's style.",
        {
            "key": "string",
            "scale": "string",
            "chord": "string",
            "progression": "string",
            "style": "string",
        },
        [],
    )
)
SCHEMAS.append(
    _utility_schema(
        "analyze_audio",
        "Measure the tempo (BPM), musical key and length of an audio file (wav, aiff, mp3, flac, m4a), on this computer, read-only. Use it for 'what's the BPM of X', 'what key is X in', 'analyze this track' or 'how long is this song file'. 'path' is the file name or full path the user said; pass 'dropped' for a file he just dropped on the orb. It shows a card and returns the facts: say the BPM and key briefly, and mention it if the confidence is low or another tempo is possible. This tool finds the file itself from a bare name (Downloads, Documents, Desktop), so call it directly: never call find_files or list_files first. Never for identifying a song, and never invent a BPM or key.",
        {"path": "string"},
        ["path"],
    )
)
SCHEMAS += [
    _utility_schema(
        "remember_fact",
        "Save a lasting fact the user just told you or asked you to remember (his own words, short, e.g. 'my favorite team is the Sharks'). Use it when he says remember/note/keep in mind. Never for passwords, keys or card numbers.",
        {"text": "string"},
        ["text"],
    ),
    _utility_schema(
        "forget_fact",
        "Permanently delete a saved memory when the user asks to forget, delete or remove it. 'topic' is what it is about, e.g. 'Sharks'; leave empty for 'forget that' (the most recent one). You CAN delete memories with this tool: never say you cannot.",
        {"topic": "string"},
        [],
    ),
    _utility_schema(
        "list_memories",
        "List what you remember about the user, optionally about one topic.",
        {"topic": "string"},
        [],
    ),
    _utility_schema(
        "file_request",
        "Hand the user's file request to the file controller: find, list, read, move, copy, rename, create, move to the trash (recoverable), undo, empty the trash, or what changed. 'request' is his own words, with the real file and folder names. The controller shows a preview and nothing changes until the user says yes. Also for 'undo that' and 'what did you change'. NOT for changes to many files at once (organise, sort, clean up, rename or move everything of a kind): use escalate_to_planner for those. You CAN do file work: never say you cannot.",
        {"request": "string"},
        ["request"],
    ),
    _utility_schema(
        "research_task",
        'Run the web research agent: it searches, opens and reads pages and returns an answer with sources that appear on the side panel. ALWAYS use it when the user says research, find sources on, or search the web for something, and for "look up ..." unless a dedicated tool fits (price, weather, scores, time, calculator): then use that tool. Pass the full question. Its result is your answer: say its summary in your own voice, briefly; never add facts it did not state, and if it says it could not verify something, say so.',
        {"question": "string"},
        ["question"],
    ),
    _utility_schema(
        "code_request",
        "Use the sandboxed coding agent. For a new task pass 'goal' and 'project' (a folder under the dev folder); for show the changes, merge it, undo the merge, status or cancel the coding task pass 'request' with the user's words. Call it immediately when the user asks for code work; never just describe it. It needs the user's yes to start and a separate yes to merge. You CAN do this: never say you cannot code.",
        {"request": "string", "goal": "string", "project": "string"},
        [],
    ),
    {
        "type": "function",
        "function": {
            "name": "render_chart",
            "description": "Show a chart or a table in the side panel from DATA you give it (never code). Use it when the user asks to chart, graph, plot, compare numbers visually or show a table, with numbers you already have from the conversation or a tool; never invent the numbers. kind: bar, hbar (long labels), line (over time), pie (parts of a whole) or table. Charts: 'labels' (the x-axis or slice names) and 'series' [{name, values}] with one value per label. Tables: 'columns' and 'rows' (each row one cell per column). Get the numbers first (call the price, weather or research tool if you do not have them), then call this, then say in one sentence what it shows; do not read the numbers out.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["bar", "hbar", "line", "pie", "table"]},
                    "title": {"type": "string"},
                    "unit": {"type": "string", "description": "axis unit, e.g. 'metres' or '$'"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "values": {"type": "array", "items": {"type": "number"}},
                            },
                            "required": ["name", "values"],
                        },
                    },
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "required": ["kind", "title"],
            },
        },
    },
]

_dispatcher = None


def dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        from julius.brain.dispatcher import Dispatcher

        _dispatcher = Dispatcher(REGISTRY, SCHEMAS, jf.agent())
    return _dispatcher


def call(name: str, args: dict[str, str | float]) -> str:
    service = dispatcher()
    if (
        name == "escalate_to_planner"
    ):  # runs other tools inside itself, so it cannot hold the dispatcher's lock
        from julius.brain import orchestrator

        return orchestrator.run_escalation(args, service)
    return service.render(service.dispatch(name, args))


def call_many(calls: list[Any]) -> list[tuple[str, bool]]:
    """Run [(name, args), ...]; returns [(rendered_text, ok), ...] in order. Parallel-safe lookups run concurrently."""
    service = dispatcher()
    if any(n == "escalate_to_planner" for n, _ in calls):
        results = []
        for name, args in calls:
            text = call(name, args)
            results.append((text, service.last_result["ok"] if service.last_result else True))
        return results
    tasks = service.dispatch_many(calls)
    return [(service.render(t), t["ok"]) for t in tasks]


# Tool schemas cost ~1.2k prompt tokens. Offer the model only the tools a request could use;
# unmatched requests keep the full catalogue, so this never removes a capability.
CHART_WORDS = r"chart|graph|\bplot\b|\btable\b|visuali[sz]|pie chart"

_SCHEMA_GROUPS = (
    (
        r"weather|forecast|rain|temperature|degrees|\bhot\b|\bcold\b|snow|sunny|humid",
        ("get_weather", "get_forecast"),
    ),
    (
        r"\btime\b|clock|timezone|time zone|what day|\bdate\b|days? (?:until|from|between|ago)|weeks? (?:until|from)|hours? (?:ahead|behind)",
        ("get_datetime", "date_calculate"),
    ),
    (
        r"price|worth|trading|stock|crypto|bitcoin|\bbtc\b|\beth\b|ticker|market|clos(?:e|ing)",
        ("get_quote",),
    ),
    (r"score|scores|\bgames?\b|\bwon\b|\bbeat\b|standings|playing", ("get_scores",)),
    (
        r"calculate|\btimes\b|\bplus\b|\bminus\b|divided|multipl|squared|sqrt|percent|%|\d\s*[-+*/x]\s*\d",
        ("calculate", "convert_units"),
    ),
    (
        r"convert|kilomet|\bmiles?\b|pounds?|kilogram|celsius|fahrenheit|\binch|\bfeet\b|\bmeters?\b|liters?|gallon|ounce",
        ("convert_units",),
    ),
    (r"search|look up|google|\bnews\b|latest|recent|headline", ("web_search", "get_news")),
    (r"research|look up|find sources|search the web", ("research_task",)),
    (r"\bfiles?\b|folder|director|\blist\b|downloads|desktop", ("list_files",)),
    (r"\bread\b|\bopen\b|contents|\bfile\b", ("read_file",)),
    (r"\bfind\b|where|locate|named|search for", ("find_files",)),
    (r"document|\bpdf\b|\bdocx?\b", ("read_document", "read_file")),
    (
        r"remember|forget|memor|\bdelete (?:that|it|the)\b|\berase\b|\bnoted?\b|\bsaved?\b",
        ("remember_fact", "forget_fact", "list_memories"),
    ),
    (
        r"trash|\bbin\b|delete|remove|\bmove\b|\bcopy\b|rename|undo|empty the|get rid|throw away|folder|\bfiles?\b|sandbox|downloads|desktop|documents|what did you change",
        ("file_request",),
    ),
    (CHART_WORDS, ("render_chart",)),
    (
        r"\bbpm\b|\btempo\b|beats per minute|what key|\bkey of\b|\bkey is\b|\banaly[sz]e (?:this|the|that|my)\b.*\b(?:track|song|file|audio|loop|sample|wav|mp3)|\.(?:wav|aiff?|mp3|flac|m4a)\b",
        ("analyze_audio",),
    ),
    (
        r"\bloops?\b|\bsamples?\b|sample (?:library|index|folder)|my (?:beats|stems|drums)|re-?scan|update my .*index",
        ("find_samples",),
    ),
    (
        r"\bchords?\b|\bscales?\b|dorian|phrygian|lydian|mixolydian|locrian|pentatonic|progression|voicing|inversion|arpeggio|\bdiatonic\b|circle of fifths|\bii[- ]?v(?:[- ]?i)?\b|roman numeral|reggae|\bdub\b|one[- ]?drop|neo[- ]?soul|motown|rocksteady|\bska\b|lo-?fi (?:chords|progression)|(?:jazz|blues|soul|gospel) (?:chords|progression|voicings)|12[- ]bar|\bin the key of\b|\b[a-g][#b]? (?:major|minor)\b",
        ("music_theory",),
    ),
    (
        r"\bcod(?:e|ing)\b|\bbugs?\b|\btests?\b|refactor|\bscripts?\b|opus|merge|\bprojects?\b|programming|\bfix (?:the|my|this)\b",
        ("code_request",),
    ),
    (
        r"remind|\btimer\b|\balarm\b|\btasks?\b|calendar|\bevents?\b|to-?do|schedule",
        ("list_planner_items", "set_reminder", "list_reminders", "cancel_reminder"),
    ),
)


LIVE_TOOLS = frozenset({"get_weather", "get_forecast", "get_quote", "get_scores", "get_news"})
_ASKING = re.compile(
    r"^\W*(?:julius\W+)?(?:what|what's|whats|who|who's|whos|how|when|where|which|is|are|was|were|did|does|do|will|can|could|tell|give|check|look|show|get|any)\b",
    re.I,
)


_PLANNER_REQUEST = re.compile(
    r"^\W*(?:julius\W+)?(?:(?:please|can you|could you)\W+)*(?:plan|compare)\b|\bitinerary\b", re.I
)


_RESEARCH_REQUEST = re.compile(
    r"^\W*(?:julius\W+)?(?:(?:please|can you|could you|go ahead and)\W+)*(?:do (?:some )?research\b|research\b|look up\b|find (?:me )?sources? (?:on|for|about)\b|search the web for\b)",
    re.I,
)


_LOOK_UP = re.compile(
    r"^\W*(?:julius\W+)?(?:(?:please|can you|could you|go ahead and)\W+)*look up\b", re.I
)
_DEDICATED = frozenset(
    {
        "get_weather",
        "get_forecast",
        "get_datetime",
        "date_calculate",
        "get_quote",
        "get_scores",
        "calculate",
        "convert_units",
    }
)


def research_required(user_text: str) -> bool:
    """True when the first model call must be research_task. Explicit research phrasing (research, find sources on, search the
    web for) always is. "look up ..." is too, unless a dedicated tool fits (price, weather, scores, time, calculator): then
    that tool answers it."""
    text = user_text or ""
    if _RESEARCH_REQUEST.search(text):
        if not _LOOK_UP.search(text):
            return True
        names = {s["function"]["name"] for s in select_schemas(text)}
        return not (names & _DEDICATED) or names >= {s["function"]["name"] for s in SCHEMAS}
    return False


_MATH_OPS = re.compile(
    r"\bplus\b|\bminus\b|\btimes\b|\bdivided\b|\bmultipl\w*|\bsquared\b|\bcubed\b|\bpercent\b|\bsquare root\b|\bto the power\b|%|\d\s*[-+*/x^]\s*\d",
    re.I,
)
_MATH_ASK = re.compile(
    r"^\W*(?:julius\W+)?(?:(?:please|can you|could you)\W+)*(?:what(?:'s| is)|calculate|compute|work out|how much is|figure out)\b",
    re.I,
)


def calculation_required(user_text: str) -> bool:
    """True for an arithmetic question that is not trivial mental math (decimals, percentages, several operations, or any number over
    two digits): the first model call must be calculate, because a recalled or mentally computed answer is a guess."""
    text = user_text or ""
    if not _MATH_ASK.search(text) or not _MATH_OPS.search(text):
        return False
    ops = len(_MATH_OPS.findall(text))
    nums = re.findall(r"\d[\d,]*\.?\d*", text)
    big = any(len(re.sub(r"\D", "", n.split(".")[0])) > 2 for n in nums)
    return ops >= 2 or big or "." in "".join(nums) or "%" in text or "percent" in text.lower()


def planner_required(user_text: str) -> bool:
    """True when the request opens with plan or compare (or asks for an itinerary): those always go to the planner,
    even when every fact in them is a simple lookup, so the first model call must be escalate_to_planner."""
    return bool(_PLANNER_REQUEST.search(user_text or ""))


_WEEKDAY_QUESTION = re.compile(
    r"\bday of the week\b|\bwhat day (?:is|was|will|does|did)\b|\bwhich day (?:is|was|will|does)\b",
    re.I,
)


def weekday_question(user_text: str) -> bool:
    """True for 'what day of the week is March 3rd 2027': calendar arithmetic is done by the date tool, never recalled."""
    return bool(_WEEKDAY_QUESTION.search(user_text or ""))


def live_tool_required(user_text: str) -> bool:
    """True when this turn asks for something that changes (a score, price, weather, schedule, news) and one of those live tools can answer it: the first model call
    must then call a tool, even if the same topic came up earlier in the conversation (an earlier answer is stale, and a remembered one is a guess)."""
    from julius.brain import livedata

    text = user_text or ""
    if not livedata.is_live_query(text):
        return False
    if not ("?" in text or _ASKING.match(text)):
        return False  # "thanks for the score" is not a request
    names = {s["function"]["name"] for s in select_schemas(text)}
    if names == {s["function"]["name"] for s in SCHEMAS}:
        return False  # nothing specific matched
    if names & (LIVE_TOOLS - {"get_quote"}):
        return True
    return "get_quote" in names and _about_a_security(
        text
    )  # "price of gas" matches the price words but is not a stock or coin


_SECURITY_WORDS = re.compile(
    r"\b(?:stocks?|shares?|crypto\w*|bitcoin|btc|ethereum|eth|ticker|trading|nasdaq|nyse|dow|s&p|quote|market)\b",
    re.I,
)


def _about_a_security(text: str) -> bool:
    import julius.brain.tickers as tk

    if _SECURITY_WORDS.search(text):
        return True
    words = " " + tk.normalize(text) + " "
    return any(f" {k} " in words for k in tk.TABLE if len(k) >= 3)


def select_schemas(
    user_text: str,
) -> list[Any]:
    """Schemas relevant to this request, in catalogue order; all of them when nothing matches."""
    import re

    text = (user_text or "").lower()
    names = set()
    for pattern, tools in _SCHEMA_GROUPS:
        if re.search(pattern, text):
            names.update(tools)
    chosen = [s for s in SCHEMAS if s["function"]["name"] in names]
    return chosen or SCHEMAS
