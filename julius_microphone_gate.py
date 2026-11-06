"""Prevent assistant playback and its acoustic tail becoming microphone turns."""
from contextlib import contextmanager
import threading,time
class MicrophoneGate:
    def __init__(self,settle_seconds=.35,clock=time.monotonic):
        self.clock=clock;self.settle_seconds=settle_seconds
        self._lock=threading.Lock();self._outputs=0;self._resume_at=0;self.quiet=False;self.on_begin=None;self.on_end=None  # optional callbacks around each playback
    def cover_output_latency(self,latency,base=.35):
        """Audio written to the device is heard `latency` seconds later, so the gate must stay shut that much longer after the last write."""
        self.settle_seconds=base+latency
    def set_quiet(self,on):
        """Quiet mode: nothing reaches the detector until it is switched off again."""
        self.quiet=bool(on)
    def accepting(self):
        with self._lock:return not self.quiet and self._outputs==0 and self.clock()>=self._resume_at
    @contextmanager
    def output(self):
        with self._lock:self._outputs+=1
        if self.on_begin:self.on_begin()
        try:yield
        finally:
            with self._lock:
                self._outputs-=1
                self._resume_at=self.clock()+self.settle_seconds
            if self.on_end:self.on_end()
