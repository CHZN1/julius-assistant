"""Natural yes/no answers for every pending question (file plans, coding tasks, calendar drafts).

"yeah", "sure, go for it", "do it please", "nah", "no thanks", "leave it", "never mind" all count. An answer only counts when EVERY word is
confirmation vocabulary, so "yes but only the old ones" or "no wait, I meant..." is not mistaken for a bare yes/no."""
import re

_YES_KEY = {"yes", "yeah", "yep", "yup", "yea", "ya", "sure", "okay", "ok", "alright", "absolutely", "definitely", "proceed", "confirm", "confirmed", "approve", "approved", "execute", "ahead", "fine", "affirmative", "certainly", "totally", "sounds"}
_YES_FILL = {"please", "do", "it", "go", "for", "thing", "of", "course", "sounds", "good", "thats", "that", "works", "and", "them", "move", "copy", "um", "uh", "well", "now", "then", "yes", "is", "its", "right", "ill", "i", "am", "the", "sure", "lets", "let's"}
_NO_KEY = {"no", "nope", "nah", "nay", "cancel", "nevermind", "never", "dont", "stop", "discard", "forget", "skip", "wait", "hold", "leave", "negative", "abort"}
_NO_FILL = {"thanks", "thank", "you", "please", "do", "not", "it", "that", "them", "alone", "mind", "actually", "on", "off", "all", "thats", "its", "fine", "keep", "the", "plan", "and", "um", "uh", "well", "changed", "my", "i", "a", "second", "minute", "wait"}


def _words(text):
    t = (text or "").lower().replace("’", "'").replace("don't", "dont").replace("never mind", "nevermind").replace("let's", "lets")
    return re.sub(r"[^a-z' ]", " ", t).replace("'", "").split()


def is_yes(text):
    w = _words(text)
    return bool(w) and len(w) <= 8 and all(x in _YES_KEY or x in _YES_FILL for x in w) and any(x in _YES_KEY for x in w)


def is_no(text):
    w = _words(text)
    return bool(w) and len(w) <= 8 and all(x in _NO_KEY or x in _NO_FILL for x in w) and any(x in _NO_KEY for x in w) and not is_yes(text)
