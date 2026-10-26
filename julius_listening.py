"""Explicit assistant listening controls; these never put the computer to sleep."""
import re
import math

class CaptureClock:
    """Detect breaks in captured audio, independent of when it is consumed."""
    def __init__(self,max_gap_seconds=2.):
        if not math.isfinite(max_gap_seconds) or max_gap_seconds<=0:
            raise ValueError('Capture gap must be positive and finite')
        self.max_gap_seconds=max_gap_seconds
        self.previous=None
    def discontinuous(self,captured_at):
        if not math.isfinite(captured_at):raise ValueError('Capture timestamp must be finite')
        previous=self.previous
        self.previous=captured_at
        return previous is not None and (captured_at<previous or captured_at-previous>self.max_gap_seconds)
WAKE_PREFIX=re.compile(r'^\s*(?:(?:hey|okay|ok)[\s,]+)?(?:julius|juliet)\b[\s,.:!?-]*',re.I)
def has_wake(text):return bool(WAKE_PREFIX.match(text))
def strip_wake(text):return WAKE_PREFIX.sub('',text,count=1).strip()
FILLER=re.compile(r'^\s*(?:thanks|thank you|okay|ok|alright|well|so|hey|hi|yeah|yep)\b[\s,.!:;-]*',re.I)
def normalize_request(text):
    """Drop leading courtesy words and a wake word ("Thanks, Julius. What's ...") so routing sees the request itself."""
    current=text
    for _ in range(3):
        step=FILLER.sub('',current,count=1) if FILLER.match(current) else current
        step=strip_leading_wake(step)
        if step==current or not step.strip():break
        current=step
    if not current.strip() or (has_wake(current) and not strip_wake(current)):return text  # only courtesy/wake words: leave as spoken
    return current
def strip_leading_wake(text):
    """Drop a leading wake word from a request spoken while already awake; a bare wake word is left as is."""
    return strip_wake(text) if has_wake(text) and strip_wake(text) else text
def incomplete_fragment(text):
    return re.sub(r'[^\w\s]','',text.lower()).strip() in {'you','the','and','but','because','so i','i was','can you','could you'}
def listening_control(text):
    command=re.sub(r'[^\w\s]','',strip_wake(text).lower()).strip()
    if command in {'sleep','go to sleep','go back to sleep','stop listening'}:return 'sleep'
    if command in {'wake','wake up'}:return 'wake'
    return None


def has_voice_activity(audio, sample_rate=16000, threshold=.0045):
    """Require sustained signal before ASR; its silence confidence is unreliable."""
    import numpy as np
    samples=np.asarray(audio)
    if samples.ndim!=1 or not samples.size or not np.isfinite(samples).all():return False
    width=max(1,round(sample_rate*.064))
    run=0;active_samples=0
    for start in range(0,len(samples),width):
        frame=samples[start:start+width]
        if len(frame)<width:break
        magnitude=np.abs(frame)
        active_samples+=int(np.count_nonzero(magnitude>threshold))
        run=run+1 if float(magnitude.mean())>threshold else 0
        if run>=2 and active_samples>=round(sample_rate*.08):return True
    return False
