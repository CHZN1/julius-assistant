"""Prevent assistant playback and its acoustic tail becoming microphone turns."""
from contextlib import contextmanager
import threading,time
class MicrophoneGate:
    def __init__(self,settle_seconds=.35,clock=time.monotonic):
        self.clock=clock;self.settle_seconds=settle_seconds
        self._lock=threading.Lock();self._outputs=0;self._resume_at=0;self.quiet=False;self._force=False;self.on_begin=None;self.on_end=None  # optional callbacks around each playback
    def cover_output_latency(self,latency,base=.35):
        """Audio written to the device is heard `latency` seconds later, so the gate must stay shut that much longer after the last write."""
        self.settle_seconds=base+latency
    def set_quiet(self,on):
        """Quiet mode: nothing reaches the detector until it is switched off again."""
        self.quiet=bool(on)
    def force_open(self):
        """the user is talking over Julius and playback has been cut: listen at once instead of waiting out the echo settle time. Cleared by the next playback."""
        with self._lock:self._force=True
    def settling(self):
        """Playback is over but the room echo has not died down yet: the gate is still shut."""
        with self._lock:return self._outputs==0 and not self._force and self.clock()<self._resume_at
    def accepting(self):
        with self._lock:
            if self._force:return not self.quiet
            return not self.quiet and self._outputs==0 and self.clock()>=self._resume_at
    @contextmanager
    def output(self):
        with self._lock:self._outputs+=1;self._force=False
        if self.on_begin:self.on_begin()
        try:yield
        finally:
            with self._lock:
                self._outputs-=1
                self._resume_at=self.clock()+self.settle_seconds
            if self.on_end:self.on_end()
