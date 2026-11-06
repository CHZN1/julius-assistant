"""Recognise Julius's own voice coming back through the microphone: a transcript heard shortly after he spoke that repeats what he said."""
import difflib, re, time
def _words(t): return re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split()
def overlap(heard, spoken):
    """Share of the heard words that occur as in-order runs inside what Julius said (1.0 = a pure echo)."""
    h, s = _words(heard), _words(spoken)
    if not h or not s: return 0.0
    return sum(b.size for b in difflib.SequenceMatcher(None, h, s, autojunk=False).get_matching_blocks()) / len(h)
class SelfEcho:
    def __init__(self, window=5.0, threshold=.6, clock=time.monotonic, keep=2):
        self.window, self.threshold, self.clock, self.keep = window, threshold, clock, keep; self.said = []; self.ended = 0.0
    def note(self, spoken): self.said = (self.said + [spoken])[-self.keep:]; self.ended = self.clock()
    def is_echo(self, heard):
        return bool(self.said) and self.clock() - self.ended <= self.window and overlap(heard, " ".join(self.said)) >= self.threshold
