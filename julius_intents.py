"""Conservative routing for live requests, retaining tool context across follow-ups."""
import re
from julius_listening import normalize_request
from julius_speech_text import speakable

CRYPTO_NAMES = {'bitcoin': 'BTC', 'ethereum': 'ETH', 'ether': 'ETH', 'solana': 'SOL'}

class LiveIntentRouter:
    def __init__(self):
        self.last = None
        self.relative_only = False

    def resolve(self, text):
        self.relative_only = False
        t = re.sub(r'\s*\[you can see:.*', '', normalize_request(text), flags=re.S).strip().rstrip('?.!')
        t_and = re.sub(r'\s*\[you can see:.*', '', normalize_request(text, keep_and=True), flags=re.S).strip().rstrip('?.!')  # follow-ups that start with "and"
        low = t.lower()
        # Compound requests (plans, comparisons, several lookups in one sentence) belong to the brain, not a single-tool shortcut.
        if re.search(r'\b(?:plan|trip|itinerary|compare|organi[sz]e|research|step by step|and then)\b', low) or (
                re.search(r'\b(?:weather|forecast)\b', low) and re.search(r'\b(?:what time|current time|what day|date)\b', low)) or (
                re.search(r'\b(?:weather|forecast|time)\b', low) and re.search(r'\b(?:in|for)\s+[^,]+?\s+and\s+\w', low)):  # "weather in Springfield and Tokyo"
            self.last = None
            return None
        loc = re.search(r'\bin\s+(.+)$', t, re.I) or re.search(r'\bfor\s+(.+)$', t, re.I)
        location = loc.group(1).strip() if loc else None
        if location and location.lower() in {'today', 'tomorrow', 'this week', 'right now', 'the week'}:
            location = None
        if location:
            location = re.sub(r'\s+(?:for\s+)?(?:today|tomorrow|right now|this week)$', '', location, flags=re.I)
        if re.search(r'\b(?:what time|current time|what day|current date|today.s date|what(?:.s| is) (?:the )?date)\b', low):
            return 'get_datetime', {'location': location} if location else {}
        if re.search(r'\b(?:weather|forecast)\b', low) and not re.match(r'^(?:explain|why|teach|what is (?:weather|a forecast)|i |we )', low):
            fn = 'get_forecast' if re.search(r'forecast|tomorrow|week', low) else 'get_weather'
            args = {'location': location or 'Springfield, Illinois'}
            if fn == 'get_forecast': args['days'] = 2 if 'tomorrow' in low else 7
            return fn, args
        conversion=re.fullmatch(r'(?:convert )?(-?\d+(?:\.\d+)?) ([A-Za-z_°]+) (?:to|in) ([A-Za-z_°]+)',t,re.I)
        if conversion:
            value,a,b=conversion.groups()
            return 'convert_units', {'value':float(value),'from_unit':a,'to_unit':b}
        document=re.fullmatch(r'(?:read document|extract text from) "([^"\n]+)"',t,re.I)
        if document:return 'read_document', {'path':document.group(1)}
        search_doc=re.fullmatch(r'search document "([^"\n]+)" for "([^"\n]+)"',t,re.I)
        if search_doc:return 'read_document', {'path':search_doc.group(1),'query':search_doc.group(2)}
        news = re.fullmatch(r'(?:what(?: is|.s| are)(?: the)? |show me |tell me |get )?(?:latest |today.s |recent )?(?:news|headlines)(?: (?:about|on|for) (.+))?', t, re.I)
        if news:
            return 'get_news', {'query': news.group(1) or 'top news'}
        sports = re.fullmatch(r'(?:what(?: are|.s)(?: the)? |show me |get )?(mlb|nba|nfl|nhl|wnba) (?:scores|results)(?: (today|yesterday))?', t, re.I)
        if sports:
            return 'get_scores', {'league': sports.group(1).lower(), 'day': (sports.group(2) or 'today').lower()}
        quote = re.fullmatch(r'(?:what(?: is|.s)(?: the)? |get |show me )?(?:price|quote|stock price) (?:of|for) ([A-Za-z^.=-]{1,24})', t, re.I)
        if quote:
            return 'get_quote', {'symbol': quote.group(1).upper()}
        if self.last:
            fn, args, out = self.last
            about = re.sub(r'^and\s+(?=(?:what|how) about\b)', '', t, flags=re.I)  # "And what about tomorrow?"
            if re.fullmatch(r'(?:what|how) about (.+)', about, re.I):
                target = re.sub(r'^(?:in|for)\s+', '', re.fullmatch(r'(?:what|how) about (.+)', about, re.I).group(1), flags=re.I)
                if fn in ('get_weather', 'get_forecast') and re.fullmatch(r'tomorrow(?: in .+)?', target, re.I):
                    return 'get_forecast', {'location': location or args.get('location', 'Springfield, Illinois'), 'days': 2}
                if fn in ('get_datetime', 'get_weather', 'get_forecast'):
                    return fn, dict(args, location=target)
            # "and in Shelbyville?" / "and for Tokyo?" repeat the last lookup somewhere else.
            place = re.fullmatch(r'(?:and )?(?:what about |how about )?(?:in|for) (.+)', t, re.I)
            if place and fn in ('get_datetime', 'get_weather', 'get_forecast'):
                return fn, dict(args, location=place.group(1).strip())
            # "and ETH?" / "what about Tesla?" repeats the last price lookup for another symbol.
            other = re.fullmatch(r'(?:and |what about |how about )(?:and )?(?:for )?([A-Za-z^.=-]{1,12})', t_and, re.I)
            if other and fn == 'get_quote' and other.group(1).lower() not in {'tomorrow', 'today', 'yesterday', 'that', 'this', 'it'}:
                name = other.group(1)
                return 'get_quote', {'symbol': CRYPTO_NAMES.get(name.lower(), name.upper())}
            if fn in ('get_weather', 'get_forecast') and re.fullmatch(r'is that for .+', t, re.I):
                return fn, dict(args, location=re.fullmatch(r'is that for (.+)', t, re.I).group(1))
            if fn == 'get_datetime' and re.search(r'how many hours (?:ahead|behind)', low):
                self.relative_only = True
                return fn, args.copy()
        from julius_spoken_math import arithmetic_expression
        expression=arithmetic_expression(low)
        if expression is not None:
            return 'calculate', {'expression':expression}
        if re.search(r'\bbitcoin\b.*\b(?:trading|price|worth)\b', low):
            return 'get_quote', {'symbol':'BTC-USD'}
        if not re.fullmatch(r'(?:thanks|thank you|okay|ok|got it)', low):
            self.last = None
        return None

    def record(self, fn, args, out):
        self.last = (fn, dict(args), out)

    def spoken_result(self, fn, out, args=None):
        if fn == 'get_datetime' and self.relative_only and ' — ' in out:
            return out.split(' — ', 1)[1].capitalize()
        ready = speakable(fn, out, args)  # ready-to-speak sentence for weather, forecast, time, calculator, price
        if ready:
            return ready
        if fn == 'get_quote' and '. 52-week range' in out:
            return out.split('. 52-week range', 1)[0] + '.'
        if fn == 'read_document':
            lines=out.splitlines()
            return ' '.join(lines[:2])[:500] + (' The remaining extracted text and citations are on screen.' if len(lines)>2 else '')
        if fn == 'get_news':
            return ' '.join(line for line in out.splitlines() if not line.startswith(('https://', 'http://')))
        lines = out.splitlines()
        if fn == 'calculate' and ' = ' in out:
            return "That's " + out.split(' = ', 1)[1] + '.'
        if fn == 'get_forecast' and (args or {}).get('days') == 2 and len(lines) == 3:
            return lines[0] + ' Tomorrow, ' + lines[2]
        if fn == 'get_forecast' and len(lines) > 3:
            return ' '.join(lines[:3])
        return out
