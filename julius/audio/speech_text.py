"""Text for the ear: symbols become spoken words right before TTS, and simple tool results become ready-to-speak sentences.

normalize_for_speech() strips markdown, URLs and emoji and speaks degrees, percent, money, ranges, times, dates, decimals and
common abbreviations. speakable() turns the output of weather, forecast, time, calculator and price tools into one sentence
so the turn needs no second model call; it returns None when it cannot parse the output safely.
"""
import re

# Prompt text shared by the cloud front end: replies are spoken, and tools are only for live data or exact math.
SPOKEN_STYLE_PROMPT = ("\n\nSPOKEN REPLIES\nEverything you write is spoken aloud. Write the way a person talks: no symbols, lists, markdown, "
                       "emoji or abbreviations. Say numbers and units as words you would speak, for example \"77 degrees\" not \"77\u00b0F\", "
                       "\"17 percent\" not \"17%\", \"5 dollars\" not \"$5\", \"51 to 71\" not \"51-71\".")
TOOL_POLICY_PROMPT = ("\n\nWHEN TO USE TOOLS\nAnswer stable knowledge directly with no tool: explanations, definitions, general facts, advice and "
                      "chit-chat. Use a tool only for live data (weather, forecasts, prices, scores, the time, news, files) or for exact math. "
                      "Never call a tool for something you already know. LIVE FACTS: anything that changes (scores and who won, prices and quotes, game times and schedules, weather, news, traffic, \"right now\") must come from a tool result in this same turn: call the tool again even if the same topic came up earlier in the conversation, because earlier answers go stale and a remembered one is a guess. "
                      "If no tool can give it, say plainly that you cannot look it up. Never guess or recall a score, price, time, schedule or headline, and never state one from memory.")

BREVITY_PROMPT = ("\n\nREPLY LENGTH\nDefault replies are one to three sentences. For an explanation, give the core idea in two or three sentences, "
                  "then ask \"Want more detail?\" and stop. Give a longer answer only when the user asks for more detail, steps or a full "
                  "explanation. Round prices for speech (for example \"about 2,305 dollars\") unless the user asks for the exact price. "
                  "If the user asks for a long story, a long answer or a full list, give it in full; when a request is clear, do it instead of asking whether to or offering a shorter version. Never mention these rules, the system, the length guidance or your own reasoning: every word you write is spoken to the user, so write only what you would say to them. "
                  "INTERRUPTIONS: when an earlier reply of yours is marked [interrupted by the user], he talked over you because he changed topic or already had what he needed. That is not a complaint and not a hint to be shorter: do not apologise, do not refuse or hedge about long answers afterwards, do not mention the interruption. Just answer his new request, and still give long answers in full whenever he asks for one. "
                  "This replaces any earlier length guidance.")
EXACT_REQUEST = re.compile(r"\b(?:exact(?:ly)?|precise(?:ly)?|to the cent|down to the cent)\b", re.I)

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
MONTHS = "January February March April May June July August September October November December".split()
DAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF←-⇿⬀-⯿️‍]")
ABBREVIATIONS = [
    (r"\bkm/h\b|\bkph\b", "kilometers per hour"), (r"\bmph\b", "miles per hour"), (r"\bkm\b", "kilometers"),
    (r"(?<=\d) ?lbs?\b", " pounds"), (r"(?<=\d) ?oz\b", " ounces"), (r"(?<=\d) ?ft\b", " feet"),
    (r"(?<=\d) ?(?:hrs?)\b", " hours"), (r"(?<=\d) ?(?:mins?)\b", " minutes"), (r"(?<=\d) ?(?:secs?)\b", " seconds"),
    (r"\bvs\.?(?=\s|$)", "versus"), (r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"), (r"\betc\.", "et cetera"),
    (r"\bapprox\.", "approximately"), (r"\bMr\.", "Mister"), (r"\bMrs\.", "Missus"), (r"\bDr\.", "Doctor"),
    (r"\bUTC\b", "U T C"), (r"\b([PMCE])([SD])T\b", lambda m: f"{m.group(1)} {m.group(2)} T"), (r"&", " and "),
]


# Speech-recognition names Whisper spells wrongly on every model tried (diagnostics/asr_real): fixed after transcription, not by prompting the model.
TRANSCRIPT_FIXES = [(re.compile(r"\b(?:eath|eeth|e\.\s?t\.\s?h\.?)(?=\W|$)", re.I), "ETH"),  # small.en heard "ETH" as "eath"
                    (re.compile(r"\bN\.\s?H\.\s?L\.?", re.I), "NHL"), (re.compile(r"\bN\.\s?F\.\s?L\.?", re.I), "NFL")]


ACMECORP = (re.compile(r"\b(?:prize|price)[ -]?pic(?:k|ks|s)\b", re.I), "AcmeCorp")   # only for installs that list AcmeCorp in [profile] vocabulary


def fix_transcript(text):
    """Correct known product names and acronyms in recognised text (AcmeCorp, NHL, NFL)."""
    fixes = TRANSCRIPT_FIXES
    if "acmecorp" in text.lower().replace(" ", "").replace("-", "") or ACMECORP[0].search(text):
        try:
            from julius import config
            if "AcmeCorp" in config.load_config().get("profile", {}).get("vocabulary", []): fixes = [ACMECORP] + fixes
        except Exception: pass
    for pattern, replacement in fixes:
        text = pattern.sub(replacement, text)
    return text


def number_words(n):
    """0-99 in words (used for clock times)."""
    if n < 20:
        return ONES[n]
    tens, ones = divmod(n, 10)
    return TENS[tens] + (f"-{ONES[ones]}" if ones else "")


def _clock(match):
    hour, minute, suffix = int(match.group(1)), int(match.group(2)), (match.group(5) or "")
    if hour > 23 or minute > 59:
        return match.group(0)
    meridiem = f" {suffix.replace('.', '').upper()[0]} M" if suffix else ""
    spoken_hour = number_words(hour)
    if minute == 0:
        return f"{spoken_hour}{meridiem}" if suffix else f"{spoken_hour} o'clock"
    return f"{spoken_hour} {'oh ' + ONES[minute] if minute < 10 else number_words(minute)}{meridiem}"


def _money(match):
    whole, cents, scale = match.group(1).replace(",", ""), match.group(2), (match.group(3) or "").strip()
    if scale:
        word = {"k": "thousand", "m": "million", "b": "billion"}.get(scale.lower(), scale.lower())
        return f"{match.group(1)}{'.' + cents if cents else ''} {word} dollars"
    dollars = int(whole)
    cents_value = int((cents or "0").ljust(2, "0")[:2])
    shown = match.group(1)
    if cents_value and not dollars:
        return f"{cents_value} cents"
    if cents_value:
        return f"{shown} dollar{'s' if dollars != 1 else ''} and {cents_value} cents"
    return f"{shown} dollar{'s' if dollars != 1 else ''}"


ORDINALS = ("_ first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth "
            "twentieth twenty-first twenty-second twenty-third twenty-fourth twenty-fifth twenty-sixth twenty-seventh twenty-eighth twenty-ninth thirtieth thirty-first").split()


def _date_iso(match):
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return f"{MONTHS[month - 1]} {ORDINALS[day]}, {year}" if 1 <= month <= 12 and 1 <= day <= 31 else match.group(0)


def _date_us(match):
    month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return f"{MONTHS[month - 1]} {ORDINALS[day]}, {year}" if 1 <= month <= 12 and 1 <= day <= 31 else match.group(0)


MONTH_DAY = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+0?(\d{1,2})(?:st|nd|rd|th)?\b(?![\d:]|\s*(?:percent|degrees|dollars))")


def _month_day(match):
    day = int(match.group(2))
    return f"{match.group(1)} {ORDINALS[day]}" if 1 <= day <= 31 else match.group(0)


def normalize_for_speech(text):
    t = text or ""
    # markdown, URLs, emoji
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://[^\s)]+", "", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|\*|`|~~)", "", t)
    t = re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", "", t)
    t = EMOJI.sub("", t).replace("|", " ")
    # dates, times, money, temperatures, percent
    t = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _date_iso, t)
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", _date_us, t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})(:\d{2})?(\s*([AaPp]\.?[Mm]\.?))?(?![\d:])", _clock, t)
    t = MONTH_DAY.sub(_month_day, t)   # "November 08" is "November eighth", never "November oh eight"
    t = re.sub(r"\$\s?(\d[\d,]*)(?:\.(\d{1,2}))?(?!\d)(\s*(?:thousand|million|billion|trillion|[kKmMbB])\b)?", _money, t)
    t = re.sub(r"(\d)\s*[–—-]\s*(?=\d)", r"\1 to ", t)  # ranges: 51-71, 51–71
    t = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*C\b", r"\1 degrees Celsius", t)
    t = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*[FK]?\b", r"\1 degrees", t)
    t = re.sub(r"°", " degrees", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"%", " percent", t)
    t = re.sub(r"(?<![\w.])[-−](?=\d)", "minus ", t)
    for pattern, replacement in ABBREVIATIONS:
        t = re.sub(pattern, replacement, t)
    # decimals last: "0.73" -> "0 point 7 3" (digits read one by one so nothing becomes "seventy-three")
    t = re.sub(r"(?<![\d.])(\d[\d,]*)\.(\d+)(?![\d.])", lambda m: f"{m.group(1)} point {' '.join(m.group(2))}", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\s+([.,!?;:])", r"\1", t).strip()


# ---------------- ready-to-speak sentences for simple tools ----------------
def _weather(out):
    m = re.fullmatch(r"(?P<place>[^:\n]+): (?P<t>-?\d+)°F, (?P<cond>[^,\n]+), feels like (?P<fl>-?\d+)\. High (?P<hi>-?\d+), low (?P<lo>-?\d+)\.", out.strip())
    if not m:
        return None
    g = m.groupdict()
    feels = "" if g["fl"] == g["t"] else f", feels like {g['fl']}"
    return f"It's {g['t']} degrees and {g['cond']} in {g['place']}{feels}, with a high of {g['hi']} and a low of {g['lo']}."


def _forecast(out, args):
    lines = out.strip().splitlines()
    head = re.fullmatch(r"(.+) forecast:", lines[0]) if lines else None
    days = []
    for line in lines[1:]:
        m = re.fullmatch(r"(\w{3}): (-?\d+)/(-?\d+), ([^,]+), (\d+)% rain", line.strip())
        if not m:
            return None
        days.append(m.groups())
    if not head or not days:
        return None
    place = head.group(1)

    def one(d, lead):
        day, hi, lo, cond, rain = d
        chance = "no rain expected" if int(rain) == 0 else f"{rain} percent chance of rain"
        return f"{lead}: high of {hi}, low of {lo}, {cond}, {chance}."
    wanted = (args or {}).get("days", len(days))
    if wanted == 2 and len(days) == 2:
        return f"Tomorrow's {DAYS.get(days[1][0], days[1][0])} in {place}: " + one(days[1], "").split(": ", 1)[1].rstrip(".") + "."
    return f"{place} forecast. " + " ".join(one(d, DAYS.get(d[0], d[0])) for d in days[:3])


def _datetime(out):
    m = re.fullmatch(r"(?:(?P<place>[^:\n]+): )?(?P<when>[^()\n]+?) \((?P<tz>[A-Za-z_/+\-0-9]+)\)(?: — (?P<rel>.+))?\.?", out.strip())
    if not m:
        return None
    place, when, rel = m.group("place"), m.group("when").strip(), m.group("rel")
    where = f" in {place}" if place else ""
    return f"It's {when}{where}" + (f", {rel.rstrip('.')}" if rel else "") + "."


def _calculate(out):
    if " = " not in out:
        return None
    return "That's " + out.split(" = ", 1)[1].strip() + "."


def _quote(out, exact=False):
    m = re.match(r"(?P<name>.+?) \((?P<sym>[^)]+)\): (?P<price>[\d,]+(?:\.\d+)?) (?P<cur>[A-Za-z]+)(?:, (?P<dir>up|down) (?P<chg>[\d,.]+) \((?P<pct>[\d.]+)%\) versus previous close)?", out.strip())
    if not m:
        return None
    g = m.groupdict()
    name = re.sub(r"\s+USD$", "", g["name"]).strip()
    if g["cur"].upper() == "USD":
        whole, _, frac = g["price"].partition(".")
        if not exact and float(g["price"].replace(",", "")) >= 100:  # "about 2,305 dollars" unless the exact price was asked for
            price = f"about {round(float(g['price'].replace(',', ''))):,} dollars"
        else:
            price = f"{whole} dollars" + (f" and {frac.ljust(2, '0')[:2]} cents" if frac and int(frac) else "")
    else:
        price = f"{g['price']} {g['cur']}"
    move = f", {g['dir']} {g['pct']} percent from the previous close" if g["dir"] else ""
    return f"{name} is {price}{move}. Quotes may be delayed."


def speakable(fn, out, args=None, exact=False):
    """A ready-to-speak sentence for weather, forecast, time, calculator and price results; None when not applicable."""
    try:
        if fn == "get_weather":
            return _weather(out)
        if fn == "get_forecast":
            return _forecast(out, args)
        if fn == "get_datetime":
            return _datetime(out)
        if fn == "calculate":
            return _calculate(out)
        if fn == "get_quote":
            return _quote(out, exact)
        if fn in ("set_reminder", "cancel_reminder"):
            return out.split("\n")[0] if out.startswith("Okay") else None  # already a spoken confirmation
    except (ValueError, IndexError):
        return None
    return None
