"""Echo cancellation: removes Julius's own voice from the microphone signal so the user's voice stands out while Julius talks (WebRTC AEC3 via the livekit package).

The canceller needs the far-end signal (what is being played) and the near-end signal (what the microphone hears), both 16 kHz mono, in 10 ms frames.
The speaker output reaches the microphone later than it is written to the audio device (device buffer, acoustic path), so the reference is delayed by `lag` seconds
before it is given to the canceller; `estimate_lag` finds it from a recording by correlating the two signals' loudness envelopes.

    EchoCanceller(lag).reference(chunk)        feed what is being played (any chunk size)
    EchoCanceller(lag).process(mic_chunk)      returns the same number of samples with the echo removed (a short startup delay is pre-filled with the unprocessed signal)
    cancel(mic, out)                           the same for whole arrays (used by the eval)"""
import collections

import numpy as np

RATE, FRAME = 16000, 160


def estimate_lag(mic, out, max_lag=4.0, step=0.01):
    """Seconds by which the echo in `mic` lags `out`, from the correlation of 10 ms loudness envelopes (0.0 when there is no playback to correlate)."""
    n = min(len(mic), len(out)) // FRAME
    if n < 100: return 0.0
    env = lambda x: np.sqrt(np.mean(np.asarray(x[:n * FRAME], dtype=np.float32).reshape(n, FRAME) ** 2, axis=1))
    m, o = env(mic), env(out)
    if o.std() < 1e-5: return 0.0
    m, o = m - m.mean(), o - o.mean(); best, best_c = 0, -1.0
    for lag in range(0, int(max_lag / step) + 1):
        if lag >= n - 10: break
        c = float(np.dot(m[lag:], o[:n - lag])) / (np.linalg.norm(m[lag:]) * np.linalg.norm(o[:n - lag]) + 1e-9)
        if c > best_c: best, best_c = lag, c
    return best * step


class EchoCanceller:
    def __init__(self, lag=0.5, echo_cancellation=True, noise_suppression=False, high_pass_filter=True, auto_gain_control=False):
        from livekit import rtc
        self._rtc = rtc
        self.apm = rtc.AudioProcessingModule(echo_cancellation=echo_cancellation, noise_suppression=noise_suppression, high_pass_filter=high_pass_filter, auto_gain_control=auto_gain_control)
        self.lag = lag; self._ref = collections.deque([np.zeros(FRAME, dtype=np.int16)] * int(round(lag * RATE / FRAME)))   # the delay line is itself the lag
        self._ref_buf = np.zeros(0, dtype=np.int16); self._mic_buf = np.zeros(0, dtype=np.int16); self._out = np.zeros(0, dtype=np.int16)

    @staticmethod
    def _pcm(x):
        return (np.clip(np.asarray(x, dtype=np.float32).reshape(-1), -1, 1) * 32767).astype(np.int16)

    def reference(self, chunk):
        """What is being played (16 kHz mono float). Queued 10 ms at a time; it is released to the canceller `lag` seconds late."""
        self._ref_buf = np.concatenate([self._ref_buf, self._pcm(chunk)])
        while len(self._ref_buf) >= FRAME:
            self._ref.append(self._ref_buf[:FRAME].copy()); self._ref_buf = self._ref_buf[FRAME:]

    def process(self, chunk):
        """Microphone audio in, echo-free audio out (same length)."""
        rtc = self._rtc
        self._mic_buf = np.concatenate([self._mic_buf, self._pcm(chunk)]); want = len(np.asarray(chunk).reshape(-1))
        while len(self._mic_buf) >= FRAME:
            mic = self._mic_buf[:FRAME].copy(); self._mic_buf = self._mic_buf[FRAME:]
            ref = self._ref.popleft() if self._ref else np.zeros(FRAME, dtype=np.int16)
            self._ref.append(np.zeros(FRAME, dtype=np.int16)) if not self._ref else None
            rf = rtc.AudioFrame(ref.tobytes(), RATE, 1, FRAME); self.apm.process_reverse_stream(rf)
            mf = rtc.AudioFrame(mic.tobytes(), RATE, 1, FRAME); self.apm.process_stream(mf)
            self._out = np.concatenate([self._out, np.frombuffer(bytes(mf.data), dtype=np.int16)])
        if len(self._out) < want:   # the first call: not enough processed yet
            pad = np.zeros(want - len(self._out), dtype=np.int16); res = np.concatenate([pad, self._out]); self._out = np.zeros(0, dtype=np.int16)
        else:
            res, self._out = self._out[:want], self._out[want:]
        return res.astype(np.float32) / 32767


def cancel(mic, out, lag=None, **kw):
    """Whole-array version for the eval: returns `mic` with the echo of `out` removed. `lag` defaults to the estimate from the signals themselves."""
    mic = np.asarray(mic, dtype=np.float32); out = np.asarray(out, dtype=np.float32)[:len(mic)]
    if len(out) < len(mic): out = np.concatenate([out, np.zeros(len(mic) - len(out), dtype=np.float32)])
    lag = estimate_lag(mic, out) if lag is None else lag
    ec = EchoCanceller(lag=max(0.0, lag - 0.0), **kw)
    # the delay line pre-fills with `lag` of silence, so reference frame k reaches the canceller at the same instant as microphone frame k+lag: feed both from the start
    res = []
    for i in range(0, len(mic) - len(mic) % FRAME, FRAME):
        ec.reference(out[i:i + FRAME]); res.append(ec.process(mic[i:i + FRAME]))
    tail = mic[len(mic) - len(mic) % FRAME:]
    return np.concatenate(res + [tail]) if res else mic
