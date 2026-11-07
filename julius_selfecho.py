"""Recognise Julius's own voice coming back through the microphone: a transcript heard shortly after he spoke that repeats what he said."""
import difflib, re, time
def _words(t): return re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split()
def overlap(heard, spoken):
    """Share of the heard words that occur as in-order runs inside what Julius said (1.0 = a pure echo)."""
    h, s = _words(heard), _words(spoken)
    if not h or not s: return 0.0
    return sum(b.size for b in difflib.SequenceMatcher(None, h, s, autojunk=False).get_matching_blocks()) / len(h)
class SelfEcho:
    """Remembers what Julius said, sentence by sentence, and when he stopped. His voice comes back through the microphone while he is still speaking and for a few
    seconds after (reverb, speaker tail, Whisper latency), so the window runs from the END of playback. A long reply is checked against all of it, not the last sentence."""
    def __init__(self, window=5.0, threshold=.6, clock=time.monotonic, keep=12, late_window=9.0, late_threshold=.75, late_min_words=4, memory=40.0, short_window=2.0):
        self.window, self.threshold, self.clock, self.keep = window, threshold, clock, keep
        self.late_window, self.late_threshold, self.late_min_words, self.memory, self.short_window = late_window, late_threshold, late_min_words, memory, short_window
        self.said = []; self.ended = 0.0; self.speaking = 0
    def note(self, spoken):
        """A sentence is about to be played."""
        now = self.clock(); self.said = (self.said + [(spoken, now)])[-self.keep:]; self.ended = now
    def begin(self): self.speaking += 1
    def finish(self):
        """Playback of the current output ended."""
        self.speaking = max(0, self.speaking - 1)
        if not self.speaking: self.ended = self.clock()
    def is_echo(self, heard):
        now = self.clock()
        if not self.said: return False
        age = 0.0 if self.speaking else now - self.ended
        if age > self.late_window: return False
        if not self.speaking and age > self.short_window and len(_words(heard)) <= 2: return False   # "yes" / "cancel" after his prompt "say yes to confirm" is an answer: speaker echo is over within ~2 s
        text = " ".join(t for t, at in self.said if now - at <= self.memory)
        if not text: return False
        score = overlap(heard, text)
        if age <= self.window: return score >= self.threshold
        return len(_words(heard)) >= self.late_min_words and score >= self.late_threshold
