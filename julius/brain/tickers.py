"""Company and crypto names -> ticker symbols, ahead of the price tool.

    resolve("Apple")            -> Resolution(symbol="AAPL", how="table")
    resolve("bitcoin")          -> Resolution(symbol="BTC-USD", how="table")
    resolve("AAPL")             -> Resolution(symbol="AAPL", how="ticker")     already a ticker: untouched
    resolve("Cheesecake Factory") -> looked up (Yahoo search); one clear match -> that symbol
    resolve("Delta")            -> ambiguous: several different companies -> Resolution(ambiguous=[...]), and the tool tells Julius to ask the user which one

Order: the built-in table (names, common spellings, and the symbols themselves) -> something that already looks like a ticker (upper case, or with ^ . = -) -> a lookup.
The lookup is injectable (`search=`) so tests never touch the network; when the network fails the answer is "not found", never a guess."""

import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

STOCKS = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "netflix": "NFLX",
    "intel": "INTC",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "ibm": "IBM",
    "international business machines": "IBM",
    "cisco": "CSCO",
    "paypal": "PYPL",
    "uber": "UBER",
    "lyft": "LYFT",
    "airbnb": "ABNB",
    "spotify": "SPOT",
    "snap": "SNAP",
    "snapchat": "SNAP",
    "pinterest": "PINS",
    "palantir": "PLTR",
    "snowflake": "SNOW",
    "shopify": "SHOP",
    "zoom": "ZM",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "roblox": "RBLX",
    "disney": "DIS",
    "walt disney": "DIS",
    "comcast": "CMCSA",
    "warner bros discovery": "WBD",
    "walmart": "WMT",
    "target": "TGT",
    "costco": "COST",
    "home depot": "HD",
    "lowes": "LOW",
    "starbucks": "SBUX",
    "mcdonalds": "MCD",
    "chipotle": "CMG",
    "coca cola": "KO",
    "coke": "KO",
    "pepsi": "PEP",
    "pepsico": "PEP",
    "nike": "NKE",
    "visa": "V",
    "mastercard": "MA",
    "american express": "AXP",
    "amex": "AXP",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "jpmorgan chase": "JPM",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "citigroup": "C",
    "citi": "C",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "berkshire hathaway": "BRK-B",
    "berkshire": "BRK-B",
    "johnson & johnson": "JNJ",
    "johnson and johnson": "JNJ",
    "pfizer": "PFE",
    "moderna": "MRNA",
    "eli lilly": "LLY",
    "lilly": "LLY",
    "unitedhealth": "UNH",
    "merck": "MRK",
    "abbvie": "ABBV",
    "exxon": "XOM",
    "exxonmobil": "XOM",
    "exxon mobil": "XOM",
    "chevron": "CVX",
    "boeing": "BA",
    "lockheed martin": "LMT",
    "general electric": "GE",
    "ge": "GE",
    "ford": "F",
    "general motors": "GM",
    "gm": "GM",
    "rivian": "RIVN",
    "lucid": "LCID",
    "toyota": "TM",
    "ups": "UPS",
    "fedex": "FDX",
    "at&t": "T",
    "att": "T",
    "verizon": "VZ",
    "t-mobile": "TMUS",
    "t mobile": "TMUS",
    "gamestop": "GME",
    "amc": "AMC",
    "intuitive surgical": "ISRG",
    "s&p 500": "SPY",
    "s&p": "SPY",
    "spy": "SPY",
    "the s&p 500": "SPY",
    "nasdaq": "QQQ",
    "qqq": "QQQ",
    "dow": "DIA",
    "dow jones": "DIA",
    "vanguard total market": "VTI",
}
CRYPTO = {
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "ether": "ETH",
    "eth": "ETH",
    "solana": "SOL",
    "ripple": "XRP",
    "xrp": "XRP",
    "dogecoin": "DOGE",
    "doge": "DOGE",
    "cardano": "ADA",
    "avalanche": "AVAX",
    "chainlink": "LINK",
    "polygon": "MATIC",
    "polkadot": "DOT",
    "litecoin": "LTC",
    "binance coin": "BNB",
    "bnb": "BNB",
    "shiba inu": "SHIB",
    "shiba": "SHIB",
    "tron": "TRX",
    "cosmos": "ATOM",
    "uniswap": "UNI",
    "stellar": "XLM",
}
CRYPTO_CODES = frozenset(CRYPTO.values())

_NOISE = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings|the|stock|stocks|share|shares|price|of|class [a-c]|common)\b"
)


def normalize(raw: str) -> str:
    s = str(raw).lower().replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9& ]+", " ", s)
    s = _NOISE.sub(" ", s)  # hyphens and punctuation are spaces: "Coca-Cola" = "coca cola"
    return " ".join(s.split())


def _table() -> dict[str, str]:
    t = {normalize(k): v for k, v in STOCKS.items()}
    t.update({normalize(k): f"{v}-USD" for k, v in CRYPTO.items()})
    t.update({normalize(v): v for v in STOCKS.values()})  # "aapl" typed in lower case
    t.update({normalize(v): f"{v}-USD" for v in CRYPTO_CODES})
    return t


TABLE = _table()
TICKER_LIKE = re.compile(r"[A-Z0-9^.=-]{1,10}")


def yahoo_search(query: str) -> list[Any]:
    """[{symbol, name, type}] from Yahoo's search; [] when the network or the answer is unusable."""
    try:
        import requests

        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 8, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        ).json()
        return [
            {
                "symbol": q.get("symbol", ""),
                "name": q.get("longname") or q.get("shortname") or "",
                "type": q.get("quoteType", ""),
            }
            for q in r.get("quotes", [])
            if q.get("symbol")
        ]
    except Exception:
        return []


def _pick(raw: str, found: list[dict[str, str] | Any]) -> tuple[Any, ...]:
    """One symbol when the lookup is clear, otherwise the (up to three) different companies it could be."""
    ok = [c for c in found if c["type"] in ("EQUITY", "ETF", "CRYPTOCURRENCY")]
    distinct, seen = [], set()
    for c in ok:  # the same company listed on several exchanges (AAPL, AAPL.MX) is one company
        key = normalize(c["name"]) or c["symbol"].split(".")[0].lower()
        if key not in seen:
            seen.add(key)
            distinct.append(c)
    if not distinct:
        return None, []
    if len(distinct) == 1:
        return distinct[0]["symbol"], []
    # Several different companies: never pick one for him. (A name that is common enough to have one clear answer, like Apple, is in the table above.)
    distinct.sort(key=lambda c: "." in c["symbol"])  # US listings first when offering the choices
    return None, distinct[:3]


def resolve(raw: str, search: Callable | None = None) -> SimpleNamespace:
    """Resolution(symbol, how, ambiguous). symbol None with ambiguous [] means not found."""
    raw = str(raw or "").strip()
    n = normalize(raw)
    if n in TABLE:
        return SimpleNamespace(symbol=TABLE[n], how="table", ambiguous=[])
    if TICKER_LIKE.fullmatch(raw) and (
        raw.isupper() or any(c in raw for c in "^.=-") or raw.isdigit()
    ):
        return SimpleNamespace(symbol=raw.upper(), how="ticker", ambiguous=[])
    found = (search or yahoo_search)(n or raw)  # "Delta stock" is searched as "delta"
    symbol, many = _pick(raw, found)
    if symbol:
        return SimpleNamespace(symbol=symbol, how="lookup", ambiguous=[])
    return SimpleNamespace(symbol=None, how="lookup", ambiguous=many)


def ambiguity_message(raw: str, candidates: list[dict[str, str]]) -> str:
    opts = " or ".join(f"{c['name'] or c['symbol']} ({c['symbol']})" for c in candidates)
    return f"AMBIGUOUS: '{raw}' could be {opts}. Ask the user which one he means. Do not pick one for him and do not state a price yet."
