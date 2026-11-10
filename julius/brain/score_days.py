"""Which dates a scores request means: today, yesterday, last night, a weekday, a calendar date, or last week."""
import re
from datetime import date, datetime, timedelta

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
MAX_RANGE_DAYS = 8


def resolve_days(phrase, today=None):
    """(first_date, last_date, label) for a spoken day phrase; ValueError with a speakable reason when it can't be read."""
    today = today or datetime.now().date()
    text = re.sub(r'\s+', ' ', str(phrase or 'today').lower().strip().strip('.,!?'))
    text = re.sub(r'^(?:on|for|from|the)\s+', '', text)
    if text in ('today', 'tonight', 'now', 'right now', 'this evening', 'this afternoon'):
        return today, today, 'today'
    if text in ('yesterday', 'last night', 'yesterday evening', 'yesterday night'):
        return today - timedelta(days=1), today - timedelta(days=1), 'yesterday'
    if text == 'tomorrow':
        return today + timedelta(days=1), today + timedelta(days=1), 'tomorrow'
    if text in ('last week', 'past week', 'this past week', 'the past week', 'past 7 days', 'last 7 days'):
        return today - timedelta(days=7), today - timedelta(days=1), 'the last 7 days'
    if text == 'this week':
        return today - timedelta(days=today.weekday()), today, 'this week'
    weekday = re.fullmatch(r'(?:(last|this past|past|this|next)\s+)?(' + '|'.join(WEEKDAYS) + r')(?: night| evening| afternoon)?', text)
    if weekday:
        which, name = weekday.groups()
        target = WEEKDAYS.index(name)
        if which == 'next':
            ahead = (target - today.weekday()) % 7 or 7
            day = today + timedelta(days=ahead)
        else:
            back = (today.weekday() - target) % 7
            if which in ('last', 'this past', 'past') and back == 0:
                back = 7
            day = today - timedelta(days=back)
        return day, day, day.strftime('%A, %B %d').replace(' 0', ' ')
    iso = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', text)
    if iso:
        day = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    else:
        named = re.fullmatch(r'(?:(' + '|'.join(m[:3] for m in MONTHS) + r')[a-z]*\.? (\d{1,2})|(\d{1,2}) (' + '|'.join(m[:3] for m in MONTHS) + r')[a-z]*)(?:st|nd|rd|th)?(?:,? (\d{4}))?', re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', text))
        if not named:
            raise ValueError("I couldn't tell which day you meant. Try today, yesterday, a weekday like Sunday, or a date.")
        mon, d1, d2, mon2, year = named.groups()
        month = [m[:3] for m in MONTHS].index(mon or mon2) + 1
        day = date(int(year or today.year), month, int(d1 or d2))
        if not year and day > today + timedelta(days=14):
            day = date(day.year - 1, month, day.day)
    if day > today + timedelta(days=14):
        raise ValueError('Scores only go about two weeks ahead.')
    if day < today - timedelta(days=366 * 3):
        raise ValueError('That is too far back for me to look up.')
    return day, day, day.strftime('%A, %B %d').replace(' 0', ' ')
