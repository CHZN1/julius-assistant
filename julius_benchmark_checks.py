"""Check tool arguments as well as names; a plausible-looking call can still be wrong."""
def passed_case(text, calls, content):
    if text.startswith('Explain overfitting'):
        return not calls and bool(content.strip()) and len(content.split()) <= 60 and '</think>' not in content and not content.startswith('Okay, the user')
    expected = ('calculate' if '4839' in text else 'get_forecast' if 'forecast' in text else 'get_weather' if 'weather' in text else 'get_quote' if 'Bitcoin' in text else 'get_datetime')
    matching = [c for c in calls if c['name']==expected]
    if len(matching)!=1: return False
    a=matching[0]['arguments']
    location=a.get('location')
    for place in ['Tokyo','Egypt','New York']:
        if place in text: return isinstance(location,str) and location.lower()==place.lower()
    if expected in ('get_weather','get_forecast'):
        if location and 'springfield' not in location.lower(): return False
        if expected=='get_forecast': return int(a.get('days',5))==7
    if expected=='get_datetime': return not location
    if expected=='calculate': return a.get('expression','').replace(' ','')=='17*4839'
    if expected=='get_quote': return a.get('symbol','').upper() in {'BTC','BTC-USD'}
    return True
