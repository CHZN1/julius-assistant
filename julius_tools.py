"""Tools Julius can call. Each returns a short string he reads aloud."""
import json, requests, subprocess
from pathlib import Path
from datetime import datetime

SAFE_ROOT = Path.home()

def _safe(p):
    import julius_files as jf
    return jf.agent().resolve(p)

def calculate(expression, **_):
    """Bounded arithmetic parsed as AST; no Python evaluation or symbolic execution."""
    from julius_calculator import evaluate
    return f"{expression} = {evaluate(expression):,.10g}"

from zoneinfo import ZoneInfo

def get_datetime(location=None, **_):
    """Local time, or time in another place if location is given."""
    n = datetime.now().astimezone()
    if not location:
        return n.strftime("%A, %B %d %Y, %-I:%M %p ") + str(n.tzinfo)
    g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": location, "count": 1}, timeout=10).json()
    if not g.get("results"):
        return f"Couldn't find {location}."
    r = g["results"][0]
    tz = r.get("timezone")
    if not tz:
        return f"No timezone data for {r['name']}."
    there = datetime.now(ZoneInfo(tz))
    diff = (there.utcoffset() - n.utcoffset()).total_seconds()/3600
    rel = ("same time as you" if abs(diff) < 0.1 else
           f"{abs(diff):g} hours {'ahead of' if diff > 0 else 'behind'} you")
    return (f"{r['name']}: {there.strftime('%A, %B %d, %-I:%M %p')} "
            f"({tz}) — {rel}.")

def get_weather(location="Springfield, Illinois", **_):
    g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": location, "count": 1}, timeout=10).json()
    if not g.get("results"): return f"Couldn't find {location}."
    r = g["results"][0]
    w = requests.get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": r["latitude"], "longitude": r["longitude"],
                             "current": "temperature_2m,apparent_temperature,weather_code",
                             "daily": "temperature_2m_max,temperature_2m_min",
                             "temperature_unit": "fahrenheit", "timezone": "auto",
                             "forecast_days": 1}, timeout=10).json()
    c, d = w["current"], w["daily"]
    CODES = {0:"clear",1:"mostly clear",2:"partly cloudy",3:"overcast",45:"foggy",
             51:"light drizzle",61:"light rain",63:"rain",65:"heavy rain",
             71:"light snow",80:"showers",95:"thunderstorms"}
    return (f"{r['name']}: {round(c['temperature_2m'])}°F, "
            f"{CODES.get(c['weather_code'],'unsettled')}, feels like "
            f"{round(c['apparent_temperature'])}. High {round(d['temperature_2m_max'][0])}, "
            f"low {round(d['temperature_2m_min'][0])}.")

def get_forecast(location="Springfield, Illinois", days=5, **_):
    g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": location, "count": 1}, timeout=10).json()
    if not g.get("results"): return f"Couldn't find {location}."
    r = g["results"][0]
    w = requests.get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": r["latitude"], "longitude": r["longitude"],
                             "daily": "temperature_2m_max,temperature_2m_min,"
                                      "precipitation_probability_max,weather_code",
                             "temperature_unit": "fahrenheit", "timezone": "auto",
                             "forecast_days": max(1, min(int(days), 16))}, timeout=10).json()
    d = w["daily"]
    CODES = {0:"clear",1:"mostly clear",2:"partly cloudy",3:"overcast",45:"fog",
             51:"drizzle",61:"light rain",63:"rain",65:"heavy rain",
             71:"snow",80:"showers",95:"storms"}
    lines = []
    for i, day in enumerate(d["time"]):
        lines.append(f"{datetime.fromisoformat(day).strftime('%a')}: "
                     f"{round(d['temperature_2m_max'][i])}/{round(d['temperature_2m_min'][i])}, "
                     f"{CODES.get(d['weather_code'][i],'mixed')}, "
                     f"{d['precipitation_probability_max'][i]}% rain")
    return f"{r['name']} forecast:\n" + "\n".join(lines)

def get_scores(league="mlb", day="today", **_):
    """Scores for today or yesterday. day: 'today' | 'yesterday'"""
    from datetime import timedelta
    paths = {"mlb":"baseball/mlb","nba":"basketball/nba","nfl":"football/nfl",
             "nhl":"hockey/nhl","wnba":"basketball/wnba"}
    p = paths.get(league.lower())
    if not p: return f"Don't have scores for {league}."
    url = f"https://site.api.espn.com/apis/site/v2/sports/{p}/scoreboard"
    if str(day).lower() not in {'today', 'yesterday'}:
        raise ValueError('Scores support today or yesterday only.')
    params = {"dates": datetime.now().strftime("%Y%m%d")}
    if str(day).lower() == "yesterday":
        params["dates"] = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    j = requests.get(url, params=params, timeout=10).json()
    out = []
    for e in j.get("events", []):
        c = e["competitions"][0]
        home = next(x for x in c["competitors"] if x["homeAway"] == "home")
        away = next(x for x in c["competitors"] if x["homeAway"] == "away")
        stt  = e["status"]["type"]
        state = ("FINAL" if stt["completed"] else
                 "LIVE" if stt["state"] == "in" else "SCHEDULED")
        hn, an = home["team"]["displayName"], away["team"]["displayName"]
        if state == "SCHEDULED":
            out.append(f"SCHEDULED: {an} at {hn}, {stt['shortDetail']}")
        else:
            hs, as_ = int(home.get("score",0)), int(away.get("score",0))
            if hs == as_:
                out.append(f"{state}: {an} and {hn} tied {hs}-{as_}")
                continue
            w, l = ((hn,hs),(an,as_)) if hs > as_ else ((an,as_),(hn,hs))
            out.append(f"{state}: {w[0]} {'beat' if state=='FINAL' else 'leads'} "
                       f"{l[0]} {w[1]}-{l[1]}")
    scores = (f"{league.upper()} ({day}) — {len(out)} games:\n" + "\n".join(out)) if out \
           else f"No {league.upper()} games {day}."
    from datetime import timezone
    retrieved = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return scores + f"\nSource: ESPN scoreboard; retrieved {retrieved}."


def find_files(pattern, source=".", recursive=True, **_):
    import julius_files as jf
    return jf.render(jf.agent().find(pattern=pattern, source=source, recursive=recursive))

def get_quote(symbol, **_):
    """Live price for a stock or crypto. Crypto uses BTC-USD style tickers."""
    s = symbol.strip().upper()
    if s in {"BTC","ETH","SOL","XRP","DOGE","ADA","AVAX","LINK","MATIC","DOT"}:
        s = f"{s}-USD"
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}",
                     params={"range":"2d","interval":"1d"},
                     headers={"User-Agent":"Mozilla/5.0"}, timeout=10).json()
    res = (r.get("chart") or {}).get("result")
    if not res: return f"No data for {s}."
    meta = res[0]["meta"]
    price = meta.get("regularMarketPrice")
    prev  = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None: return f"No price for {s}."
    name = meta.get("shortName", s)
    line = f"{name} ({s}): {price:,.2f} {meta.get('currency', 'currency unknown')}"
    if prev:
        d = price - prev
        line += f", {'up' if d>=0 else 'down'} {abs(d):,.2f} ({abs(d/prev)*100:.2f}%) versus previous close"
    hi, lo = meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow")
    if hi and lo: line += f". 52-week range {lo:,.2f} to {hi:,.2f} {meta.get('currency', 'currency unknown')}."
    stamp = meta.get('regularMarketTime')
    if stamp:
        from datetime import timezone
        line += " As of " + datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%d %H:%M UTC') + "."
    else:
        line += " Quote time unavailable."
    return line + " Source: Yahoo Finance; quotes may be delayed."

def get_news(query, **_):
    from ddgs import DDGS
    with DDGS(timeout=10) as d:
        hits = list(d.news(query, timelimit="d", max_results=3))[:3]
    if not hits:
        return {"ok": False, "code": "no_results", "message": "No recent news results found.", "data": {}}
    items = [{k: h.get(k, '') for k in ('title', 'date', 'source', 'url', 'body')} for h in hits]
    message = "News search results (headlines and snippets, articles not verified):\n" + "\n".join(
        f"{h['title']} — {h['source']} ({h['date'] or 'date unavailable'})\n{h['url']}" for h in items)
    return {"ok": True, "code": "completed", "message": message, "data": {"items": items}}

def web_search(query, n=3, **_):
    from ddgs import DDGS
    with DDGS(timeout=10) as d:
        hits = list(d.text(query, max_results=n))
    if not hits: return "Nothing found."
    return "\n".join(f"{h['title']}: {h['body'][:200]}\n{h.get('href', '')}" for h in hits)

def list_files(directory=".", **_):
    import julius_files as jf
    return jf.render(jf.agent().list(directory))

def read_file(path, max_chars=2000, **_):
    import julius_files as jf
    return jf.render(jf.agent().read(path, max_chars))

REGISTRY = {"get_datetime": get_datetime, "get_weather": get_weather,
            "get_forecast": get_forecast, "get_scores": get_scores,
            "calculate" : calculate, "get_quote" : get_quote,
            "web_search": web_search, "list_files": list_files,
            "read_file": read_file}

SCHEMAS = [

    {"type":"function","function":{"name":"get_datetime",
        "description":"Current date and time. Give a location for time elsewhere, "
                    "otherwise returns local time. Omit location for local time; never use a placeholder.",
        "parameters":{"type":"object","properties":{
        "location":{"type":"string","description":"City, e.g. 'Tokyo'"}},
        "required":[]}}},
    {"type":"function","function":{"name":"get_weather",
        "description":"Current weather and today's high/low. Omit location to use Springfield, Illinois.",
        "parameters":{"type":"object","properties":{
        "location":{"type":"string","description":"City, e.g. 'Springfield, Illinois'"}},
        "required":[]}}},
    {"type":"function","function":{"name":"get_forecast",
        "description":"Multi-day weather forecast for a place. Use when asked about "
                    "upcoming days, trips, or 'this week'. Omit location to use Springfield, Illinois.",
        "parameters":{"type":"object","properties":{
        "location":{"type":"string","description":"Requested city; omit to use Springfield, Illinois"},
        "days":{"type":"integer","description":"How many days ahead, 1-16"}},
        "required":[]}}},
      {"type":"function","function":{"name":"web_search",
        "description":"Search the web for CURRENT information you cannot know — recent "
                    "news, today's events, new releases. Do NOT use for jokes, "
                    "opinions, conversation, general knowledge, explanations, or "
                    "anything you can answer yourself.",
        "parameters":{"type":"object","properties":{
        "query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"list_files",
        "description":"List files in a folder under the user's Documents directory.",
        "parameters":{"type":"object","properties":{
        "directory":{"type":"string","description":"Path relative to Documents"}},
        "required":[]}}},
    {"type":"function","function":{"name":"read_file",
        "description":"Read the beginning of a text file under Documents.",
        "parameters":{"type":"object","properties":{
        "path":{"type":"string"}},"required":["path"]}}},
      {"type":"function","function":{"name":"get_scores",
        "description":"Scores for games happening TODAY or YESTERDAY only. Do NOT use "
                    "for historical results, past seasons, championships, or any "
                    "question about a specific year — answer those from your own "
                    "knowledge without a tool.",
        "parameters":{"type":"object","properties":{
        "league":{"type":"string","description":"one of: mlb, nba, nfl, nhl, wnba"},
        "day":{"type":"string","description":"'today' or 'yesterday'"}},
        "required":["league"]}}},
    {"type":"function","function":{"name":"calculate",
        "description":"Calculate bounded arithmetic with +, -, *, /, **, parentheses, sqrt, abs, pi and e. For percentages write 'X% of Y' (for example '17.5% of 2480'); a bare % is modulo. Algebra and calculus are not supported.",
        "parameters":{"type":"object","properties":{
        "expression":{"type":"string"}},"required":["expression"]}}},
    {"type":"function","function":{"name":"get_quote",
        "description":"Latest available, possibly delayed price for a stock or cryptocurrency. Use ticker symbols: "
                    "AAPL, TSLA, SPY for stocks; BTC, ETH, SOL for crypto. "
                    "ALWAYS use this for prices — never web_search, never guess.",
        "parameters":{"type":"object","properties":{
        "symbol":{"type":"string","description":"Ticker, e.g. 'AAPL' or 'BTC'"}},
        "required":["symbol"]}}}
]

# Mutations are deliberately absent from model tools. The human-command
# FileSession handles preview/confirm/execute; a model assertion cannot approve.
import julius_files as jf
REGISTRY['find_files'] = find_files
SCHEMAS += [{"type":"function","function":{"name":"find_files",
    "description":"Find existing files by filename pattern inside Documents, Desktop, or Downloads. Search is bounded; report truncation honestly.",
    "parameters":{"type":"object","properties":{
        "pattern":{"type":"string"},"source":{"type":"string"},"recursive":{"type":"boolean"}},"required":["pattern"]}}}]

REGISTRY['get_news'] = get_news
SCHEMAS.append({"type": "function", "function": {"name": "get_news",
    "description": "Search recent news with publisher, publication date and source URL. Results are headlines/snippets, not verified full articles.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}})

_planner = None

def planner():
    global _planner
    if _planner is None:
        from julius_planning import Planner
        _planner = Planner(jf.STATE)
    return _planner

def list_planner_items(kind=None):
    return planner().list(kind)

def convert_units(value, from_unit, to_unit):
    from julius_conversions import convert_units as convert
    return convert(value, from_unit, to_unit)

def date_calculate(start, operation, timezone_name="America/Los_Angeles", amount=0, end=None, target_timezone=None):
    from julius_conversions import date_calculate as compute
    return compute(start, operation, timezone_name, amount, end, target_timezone)

def _utility_schema(name, description, properties, required):
    return {"type":"function", "function":{"name":name,"description":description,
        "parameters":{"type":"object","properties":{k:{"type":v} for k,v in properties.items()},"required":required}}}

REGISTRY.update(list_planner_items=list_planner_items,convert_units=convert_units,date_calculate=date_calculate)
SCHEMAS += [
    _utility_schema('list_planner_items','Read active local tasks, reminders or events. Only explicit human commands can add, complete or cancel items.',{'kind':'string'},[]),
    _utility_schema('convert_units','Convert dimension-compatible units. Supports length, mass, time, volume and Celsius/Fahrenheit/Kelvin. US volume units require us_gal or us_cup. No currency conversion.',{'value':'number','from_unit':'string','to_unit':'string'},['value','from_unit','to_unit']),
    _utility_schema('date_calculate','Compute explicit ISO dates/times with IANA time zones. Operations: add_days (calendar days), add_hours (elapsed hours), difference (elapsed hours), convert_timezone. Rejects ambiguous/nonexistent local times.',{'start':'string','operation':'string','timezone_name':'string','amount':'number','end':'string','target_timezone':'string'},['start','operation'])
]

def read_document(path, query=None):
    from julius_documents import read_document as extract
    return extract(path, query)

REGISTRY['read_document']=read_document
SCHEMAS.append(_utility_schema('read_document','Extract bounded PDF/DOCX/UTF-8 text with page/paragraph/line citations, or search an exact phrase. No OCR; report partial extraction and unsupported formats honestly. File scope is Documents/Desktop/Downloads.',{'path':'string','query':'string'},['path']))

_dispatcher = None

def dispatcher():
    global _dispatcher
    if _dispatcher is None:
        from julius_dispatcher import Dispatcher
        _dispatcher = Dispatcher(REGISTRY, SCHEMAS, jf.agent())
    return _dispatcher

def call(name, args):
    service = dispatcher()
    if name == "escalate_to_planner":  # runs other tools inside itself, so it cannot hold the dispatcher's lock
        import julius_orchestrator
        return julius_orchestrator.run_escalation(args, service)
    return service.render(service.dispatch(name, args))


# Tool schemas cost ~1.2k prompt tokens. Offer the model only the tools a request could use;
# unmatched requests keep the full catalogue, so this never removes a capability.
_SCHEMA_GROUPS = (
    (r"weather|forecast|rain|temperature|degrees|\bhot\b|\bcold\b|snow|sunny|humid", ("get_weather", "get_forecast")),
    (r"\btime\b|clock|timezone|time zone|what day|\bdate\b|days? (?:until|from|between|ago)|weeks? (?:until|from)|hours? (?:ahead|behind)",
     ("get_datetime", "date_calculate")),
    (r"price|worth|trading|stock|crypto|bitcoin|\bbtc\b|\beth\b|ticker|market", ("get_quote",)),
    (r"score|scores|\bgames?\b|\bwon\b|\bbeat\b|standings|playing", ("get_scores",)),
    (r"calculate|\btimes\b|\bplus\b|\bminus\b|divided|multipl|squared|sqrt|percent|%|\d\s*[-+*/x]\s*\d",
     ("calculate", "convert_units")),
    (r"convert|kilomet|\bmiles?\b|pounds?|kilogram|celsius|fahrenheit|\binch|\bfeet\b|\bmeters?\b|liters?|gallon|ounce",
     ("convert_units",)),
    (r"search|look up|google|\bnews\b|latest|recent|headline", ("web_search", "get_news")),
    (r"\bfiles?\b|folder|director|\blist\b|downloads|desktop", ("list_files",)),
    (r"\bread\b|\bopen\b|contents|\bfile\b", ("read_file",)),
    (r"\bfind\b|where|locate|named|search for", ("find_files",)),
    (r"document|\bpdf\b|\bdocx?\b", ("read_document", "read_file")),
    (r"remind|\btasks?\b|calendar|\bevents?\b|to-?do|schedule", ("list_planner_items",)),
)


def select_schemas(user_text):
    """Schemas relevant to this request, in catalogue order; all of them when nothing matches."""
    import re
    text = (user_text or "").lower()
    names = set()
    for pattern, tools in _SCHEMA_GROUPS:
        if re.search(pattern, text):
            names.update(tools)
    chosen = [s for s in SCHEMAS if s["function"]["name"] in names]
    return chosen or SCHEMAS
