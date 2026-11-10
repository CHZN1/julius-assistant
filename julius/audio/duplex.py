"""Duplex audio: one sounddevice stream carries the microphone AND the speakers, so echo cancellation gets a sample-aligned reference.

Why: the WebRTC canceller (julius_aec) needs to know exactly what the speakers were playing at the instant each microphone sample was captured. Stamping the
reference when audio is *written* to the device is 0.4 to 1.8 s off, which gave 3 to 6 dB of cancellation. In this module the reference is the very `outdata`
the audio callback hands to the device, in the same callback as the matching `indata`: 28.7 dB in diagnostics/aec_selftest.py.

    DuplexAudio(sd, on_block, rate=48000)    one sd.Stream at `rate` (his voice is played at full quality; only the mic and the reference are reduced to 16 kHz)
      .shim()                                an `sd` look-alike whose OutputStream() feeds the speakers: julius_audio_player.SpeechPlayer runs on it unchanged
      on_block(block, raw, ref, cleaned)       called with 1024-sample blocks (16 kHz): `block` is what to listen to (cleaned unless use_cleaned is off), raw mic, what was playing, cleaned mic
    AecMonitor                               watches the cleaned signal; if echo keeps leaking through it calls on_fallback (the caller puts the microphone gate back)
    CleanTalkOver                            speech detector on the cleaned signal: the user talking over Julius (energy, not echo-correlated)"""

import collections
import contextlib
import math
import threading
import time

import numpy as np

FRAME16 = 160  # 10 ms at 16 kHz: the canceller's frame
BLOCK16 = 1024  # what the rest of Julius consumes


# ---------------------------------------------------------------- resampling (stateful, integer ratios)
class Decimator:
    """rate -> rate/factor with a low-pass in front; chunk boundaries do not matter. The mic and the reference both pass through the same filter, so they stay aligned."""

    def __init__(self, factor):
        from scipy.signal import firwin

        self.f = int(factor)
        self.b = firwin(20 * self.f + 1, 0.9 / self.f)
        self.zi = np.zeros(len(self.b) - 1)
        self.n = 0

    def __call__(self, x):
        from scipy.signal import lfilter

        x = np.asarray(x, dtype=np.float64).reshape(-1)
        if self.f == 1:
            return x.astype(np.float32)
        y, self.zi = lfilter(self.b, 1.0, x, zi=self.zi)
        out = y[(-self.n) % self.f :: self.f]
        self.n += len(y)
        return out.astype(np.float32)


class Upsampler:
    """src rate -> dst rate for integer ratios (24 kHz voice -> 48 kHz stream); other ratios fall back to linear interpolation."""

    def __init__(self, src, dst):
        self.src, self.dst = int(src), int(dst)
        self.f = self.dst // self.src if self.dst % self.src == 0 else 0
        if self.f > 1:
            from scipy.signal import firwin

            self.b = firwin(16 * self.f + 1, 0.9 / self.f)
            self.zi = np.zeros(len(self.b) - 1)

    def __call__(self, x):
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if self.src == self.dst or not x.size:
            return x
        if self.f > 1:
            from scipy.signal import lfilter

            u = np.zeros(len(x) * self.f)
            u[:: self.f] = x * self.f
            y, self.zi = lfilter(self.b, 1.0, u, zi=self.zi)
            return y.astype(np.float32)
        n = int(round(len(x) * self.dst / self.src))
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


# ---------------------------------------------------------------- playback buffer
class PlayBuffer:
    def __init__(self):
        self._lock = threading.Lock()
        self._q = collections.deque()
        self._n = 0
        self._head = 0

    def push(self, a):
        with self._lock:
            self._q.append(a)
            self._n += len(a)

    def seconds(self, rate):
        with self._lock:
            return self._n / rate

    def clear(self):
        with self._lock:
            self._q.clear()
            self._n = 0
            self._head = 0

    def pull(self, n):
        """n samples (zero padded). Returns (samples, starved): starved means the buffer ran out before n."""
        out = np.zeros(n, dtype=np.float32)
        got = 0
        with self._lock:
            while got < n and self._q:
                a = self._q[0]
                take = min(n - got, len(a) - self._head)
                out[got : got + take] = a[self._head : self._head + take]
                got += take
                self._head += take
                self._n -= take
                if self._head >= len(a):
                    self._q.popleft()
                    self._head = 0
        return out, got < n


# ---------------------------------------------------------------- monitor and detector
def _corr(a, b, max_lag):
    """Largest Pearson correlation of two envelopes with `b` shifted by 0..max_lag steps (echo trails the reference)."""
    best = 0.0
    for lag in range(0, max_lag + 1):
        x = a[lag:]
        y = b[: len(b) - lag] if lag else b
        n = min(len(x), len(y))
        if n < 8:
            continue
        x, y = x[-n:], y[-n:]
        sx, sy = x.std(), y.std()
        if sx < 1e-7 or sy < 1e-7:
            continue
        c = float(np.mean((x - x.mean()) * (y - y.mean())) / (sx * sy))
        best = max(best, c)
    return best


class AecMonitor:
    """Is the canceller working? During playback the cleaned microphone should be near the room noise. Residual that is clearly above it AND moves with the
    playback loudness is echo that got through (device change, headphones unplugged, canceller lost the delay); the user's own voice does not follow Julius's envelope.
    When that lasts `fail_seconds` of playback within the last `memory_seconds`, on_fallback(reason) is called once."""

    WINDOW = 0.05

    def __init__(
        self,
        on_fallback=None,
        fail_seconds=3.0,
        memory_seconds=8.0,
        corr_limit=0.6,
        min_residual=0.002,
        noise_k=2.5,
        log=print,
        clock=time.monotonic,
    ):
        (
            self.on_fallback,
            self.fail_seconds,
            self.corr_limit,
            self.min_residual,
            self.noise_k,
            self.log,
            self.clock,
        ) = on_fallback, fail_seconds, corr_limit, min_residual, noise_k, log, clock
        self.fell_back = False
        self.reason = None
        self._acc = [0.0, 0.0, 0.0]
        self._n = 0
        self._clean = collections.deque(maxlen=40)
        self._ref = collections.deque(maxlen=40)  # 2 s of window levels
        self._quiet = collections.deque(
            maxlen=600
        )  # cleaned level while nothing plays: the room noise
        self._bad = collections.deque(maxlen=int(memory_seconds / self.WINDOW))
        self.echo_like = False
        self.play_age = 0.0
        self._silent_for = 99.0
        self.windows = 0
        self.total_play = 0.0  # total_play: seconds of playback so far (the canceller needs a couple of seconds to converge on the first reply)

    @property
    def playing_now(self):
        return self._silent_for < 0.3

    @property
    def noise(self):
        return float(np.percentile(self._quiet, 20)) if len(self._quiet) >= 20 else 0.001

    def feed(self, cleaned, raw, ref):
        """16 kHz samples from one stretch of the callback (any length)."""
        c = np.asarray(cleaned, dtype=np.float32)
        r = np.asarray(raw, dtype=np.float32)
        p = np.asarray(ref, dtype=np.float32)
        i = 0
        step = int(self.WINDOW * 16000)
        while i < len(c):
            take = min(step - self._n, len(c) - i)
            self._acc[0] += float(np.dot(c[i : i + take], c[i : i + take]))
            self._acc[1] += float(np.dot(r[i : i + take], r[i : i + take]))
            self._acc[2] += float(np.dot(p[i : i + take], p[i : i + take]))
            self._n += take
            i += take
            if self._n >= step:
                self._window(*(math.sqrt(a / self._n) for a in self._acc))
                self._acc = [0.0, 0.0, 0.0]
                self._n = 0

    def _window(self, clean, raw, ref):
        self.windows += 1
        playing = ref > 0.002
        if playing:
            if self._silent_for >= 1.5 or self.play_age == 0.0:
                self.play_age = 0.0  # a new reply starts
            self.play_age += self.WINDOW
            self._silent_for = 0.0
            self.total_play += self.WINDOW
        else:
            self._silent_for += self.WINDOW
            if self._silent_for >= 1.5:
                self.play_age = 0.0
            self._quiet.append(clean)
        self._clean.append(clean)
        self._ref.append(ref)
        loud = ref > 0.02
        if len(self._clean) >= 20 and playing:
            self.echo_like = _corr(np.array(self._clean), np.array(self._ref), 8) > self.corr_limit
        elif not playing:
            self.echo_like = False
        if loud:
            bad = self.echo_like and clean > max(self.noise_k * self.noise, self.min_residual)
            self._bad.append(bad)
            if not self.fell_back and sum(self._bad) * self.WINDOW >= self.fail_seconds:
                self._fail(clean, raw, ref)

    def _fail(self, clean, raw, ref):
        self.fell_back = True
        self.reason = f"echo leaking through: residual {clean:.4f} vs room noise {self.noise:.4f} for {sum(self._bad) * self.WINDOW:.1f} s of playback (mic {raw:.4f}, playing {ref:.3f})"
        self.log(
            f"  [echo cancellation not working: {self.reason}; microphone gate back on]", flush=True
        )
        if self.on_fallback:
            with contextlib.suppress(Exception):
                self.on_fallback(self.reason)


class CleanTalkOver:
    """the user talking over Julius, judged on the echo-cancelled signal.
    The canceller's suppressor clamps down on everything while Julius is loud (recorded 11-07: the cleaned signal was exactly 0.0000 for half a second at a time, with only
    short bursts of the user's voice getting through), so this counts BURSTS, not a sustained level: `bursts` 50 ms windows above max(min_rms, k x room noise) within the last
    `span` seconds. It only applies while Julius is audibly speaking (not while he is thinking), not in the first `holdoff` seconds of a reply, and not when the residual
    follows the playback envelope (echo)."""

    WINDOW = 0.05

    def __init__(self, monitor, min_rms=0.0030, k=3.5, bursts=3, span=1.0, holdoff=0.4, warmup=3.0):
        self.mon, self.min_rms, self.k, self.bursts, self.span, self.holdoff, self.warmup = (
            monitor,
            min_rms,
            k,
            bursts,
            span,
            holdoff,
            warmup,
        )
        self._acc = 0.0
        self._n = 0
        self._hits = collections.deque(maxlen=int(span / self.WINDOW))
        self._times = collections.deque(maxlen=int(span / self.WINDOW))
        self._t = 0.0
        self.triggered = False
        self.onset_ago = 0.0  # onset_ago: seconds between the first burst of the speech that caused the trigger and the trigger

    def threshold(self):
        return max(self.min_rms, self.k * self.mon.noise)

    def block(self, cleaned):
        """Feed cleaned microphone samples. True once, when the user's speech has been heard over Julius."""
        c = np.asarray(cleaned, dtype=np.float32).reshape(-1)
        step = int(self.WINDOW * 16000)
        i = 0
        while i < len(c):
            take = min(step - self._n, len(c) - i)
            seg = c[i : i + take]
            self._acc += float(np.dot(seg, seg))
            self._n += take
            i += take
            if self._n >= step:
                rms = math.sqrt(self._acc / self._n)
                self._acc = 0.0
                self._n = 0
                active = (
                    self.mon.playing_now
                    and self.mon.play_age >= self.holdoff
                    and self.mon.total_play >= self.warmup
                    and not self.mon.echo_like
                )
                if self.triggered:  # one stop per reply: stay latched (and forget the bursts that caused it) until the playback is over
                    if not self.mon.playing_now:
                        self.triggered = False
                    self._hits.clear()
                    self._times.clear()
                    continue
                self._t += self.WINDOW
                self._hits.append(1 if (active and rms > self.threshold()) else 0)
                self._times.append(self._t)
                if sum(self._hits) >= self.bursts:
                    first = min(t for h, t in zip(self._hits, self._times, strict=False) if h)
                    self.onset_ago = self._t - first + self.WINDOW
                    self.triggered = True
                    self._hits.clear()
                    self._times.clear()
                    return True
        return False

    def reset(self):
        self._acc = 0.0
        self._n = 0
        self._hits.clear()
        self._times.clear()
        self.triggered = False


def replace_with_raw(q, ring, lead_seconds, block=BLOCK16):
    """Barge-in: the canceller's suppressor eats the first words of the user's speech while Julius is loud ("Stop. What's the weather in Springfield?" reached the recognizer as "In Springfield.").
    The queue `q` holds (block, time) entries that were cleaned; `ring` holds the raw blocks, its last entry being the block being handled right now (not yet queued).
    Swap the last `lead_seconds` of queued blocks for their raw versions (the raw ones contain a little of Julius's voice, quieter than the user's; recognition copes with that, and with
    the words lost it does not). Returns how many were replaced."""
    raw = list(ring)
    n = min(int(math.ceil(lead_seconds * 16000 / block)), len(raw) - 1, len(q))
    done = 0
    for i in range(1, n + 1):
        q[-i] = (raw[-1 - i], q[-i][1])
        done += 1
    return done


# ---------------------------------------------------------------- the stream
class DuplexAudio:
    def __init__(
        self,
        sd,
        on_block,
        rate=48000,
        canceller=None,
        monitor=None,
        blocksize=None,
        block16=BLOCK16,
        log=print,
    ):
        if rate % 16000:
            raise ValueError("duplex rate must be a multiple of 16000")
        self.sd, self.on_block, self.rate, self.canceller, self.monitor, self.log = (
            sd,
            on_block,
            rate,
            canceller,
            monitor,
            log,
        )
        self.blocksize = blocksize or rate // 100
        self.block16 = block16
        self.factor = rate // 16000
        self.dec_in, self.dec_out = Decimator(self.factor), Decimator(self.factor)
        self.play = PlayBuffer()
        self.stream = None
        self.out_latency = 0.1
        self.starved = False
        self.streaming = False
        self.input_overflows = self.output_underflows = 0
        self.use_cleaned = True
        self._raw = np.zeros(0, np.float32)
        self._ref = np.zeros(0, np.float32)
        self._cln = np.zeros(0, np.float32)
        self._pend_mic = np.zeros(0, np.float32)
        self._pend_ref = np.zeros(0, np.float32)
        self._lock = threading.Lock()
        self.callbacks = 0
        self.out_level = (
            0.0  # RMS of the block just handed to the speakers (the UI event feed's level)
        )
        self.ring = collections.deque(
            maxlen=80
        )  # the last ~5 s of raw microphone blocks (barge-in re-reads the start of what the user said from here)
        self.raw_blocks_left = 0  # while > 0, blocks go to the listener raw: the suppressor mangles the user's first words while Julius is still loud

    # ---- lifetime
    def start(self):
        with self._lock:
            if self.stream is not None:
                return
            self.stream = self.sd.Stream(
                samplerate=self.rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                latency="low",
                callback=self._callback,
            )
            self.stream.start()
            lat = getattr(self.stream, "latency", (0.1, 0.1))
            self.out_latency = float(lat[1] if isinstance(lat, (tuple, list)) else lat)
            self.log(
                f"  [duplex audio: {self.rate} Hz, block {self.blocksize}, latency {lat}]",
                flush=True,
            )

    def stop(self):
        with self._lock:
            s, self.stream = self.stream, None
        if s is not None:
            try:
                s.abort()
            finally:
                s.close()

    def running(self):
        """Context for `with microphone_stream():` — starts the stream and leaves it open (playback needs it between listening sessions)."""
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            self.start()
            yield self

        return ctx()

    def shim(self):
        """An object with the part of the sounddevice API SpeechPlayer uses."""
        duplex = self

        class _Shim:
            @staticmethod
            def OutputStream(samplerate=None, channels=1, dtype="float32", latency=None, **kw):
                return _OutputAdapter(duplex, samplerate)

        return _Shim

    # ---- audio thread
    def _callback(self, indata, outdata, frames, time_info, status):
        self.callbacks += 1
        if status:
            if getattr(status, "input_overflow", False):
                self.input_overflows += 1
            if getattr(status, "output_underflow", False):
                self.output_underflows += 1
        data, starved = self.play.pull(frames)
        if starved and self.streaming:
            self.starved = True
        outdata[:, 0] = data
        self.out_level = float(np.sqrt(np.mean(data * data))) if len(data) else 0.0
        if outdata.shape[1] > 1:
            outdata[:, 1:] = data[:, None]
        self.process(indata[:, 0], data)

    def process(self, mic, played):
        """Both arguments are what this callback saw: `mic` captured, `played` handed to the device. Decimated together, cancelled together."""
        m = self.dec_in(mic)
        r = self.dec_out(played)
        self._pend_mic = np.concatenate([self._pend_mic, m])
        self._pend_ref = np.concatenate([self._pend_ref, r])
        n = min(len(self._pend_mic), len(self._pend_ref)) // FRAME16 * FRAME16
        if not n:
            return
        mm, rr = self._pend_mic[:n], self._pend_ref[:n]
        self._pend_mic, self._pend_ref = self._pend_mic[n:], self._pend_ref[n:]
        if self.canceller is not None:
            cc = np.empty(n, np.float32)
            for i in range(0, n, FRAME16):
                self.canceller.reference(rr[i : i + FRAME16])
                cc[i : i + FRAME16] = self.canceller.process(mm[i : i + FRAME16])
        else:
            cc = mm
        if self.monitor is not None:
            self.monitor.feed(cc, mm, rr)
        self._raw = np.concatenate([self._raw, mm])
        self._ref = np.concatenate([self._ref, rr])
        self._cln = np.concatenate([self._cln, cc])
        while len(self._raw) >= self.block16:
            b = self.block16
            raw, ref, cln = self._raw[:b], self._ref[:b], self._cln[:b]
            self._raw, self._ref, self._cln = self._raw[b:], self._ref[b:], self._cln[b:]
            self.ring.append(raw.copy())
            use_raw = (not self.use_cleaned) or self.raw_blocks_left > 0
            if self.raw_blocks_left > 0:
                self.raw_blocks_left -= 1
            try:
                self.on_block(raw if use_raw else cln, raw, ref, cln)
            except Exception as e:
                self.log(f"  [duplex block handler failed: {type(e).__name__}: {e}]", flush=True)


class _OutputAdapter:
    """What SpeechPlayer sees as an OutputStream: write() resamples to the stream rate and queues, blocking while more than `max_ahead` seconds are queued."""

    def __init__(self, duplex, rate, max_ahead=0.8):
        self.d, self.rate, self.max_ahead = duplex, int(rate), max_ahead
        self.up = Upsampler(self.rate, duplex.rate)
        self.aborted = threading.Event()

    def start(self):
        self.d.start()
        self.d.streaming = True
        self.d.starved = False

    def write(self, data):
        if self.aborted.is_set():
            raise RuntimeError("playback aborted")
        self.d.streaming = True
        a = self.up(np.asarray(data, dtype=np.float32).reshape(-1))
        while self.d.play.seconds(self.d.rate) > self.max_ahead:
            if self.aborted.is_set():
                raise RuntimeError("playback aborted")
            time.sleep(0.005)
        if self.aborted.is_set():
            raise RuntimeError("playback aborted")
        self.d.play.push(a)
        flagged, self.d.starved = self.d.starved, False
        return flagged

    def abort(self):
        self.aborted.set()
        self.d.play.clear()

    def stop(self):
        self.d.streaming = False

    def close(self):
        self.d.streaming = False
        if self.aborted.is_set():
            self.d.play.clear()
