"""Duplex audio: sample alignment of the echo reference, automatic fallback to the microphone gate, barge-in on the cleaned signal, playback abort."""
import threading, time, unittest

import numpy as np

import julius_duplex as jd
from julius_microphone_gate import MicrophoneGate

RATE = 48000


def voice_like(seconds, seed, rms=0.1, rate=RATE):
    """Band-limited noise with a syllable-rate envelope: something an echo canceller has to treat like speech."""
    from scipy.signal import firwin, lfilter
    r = np.random.default_rng(seed); n = int(seconds * rate)
    x = lfilter(firwin(201, [300, 3400], pass_zero=False, fs=rate), 1, r.standard_normal(n))
    t = np.arange(n) / rate; env = (0.55 + 0.45 * np.sin(2 * np.pi * (3.0 + 1.3 * r.random()) * t + 6 * r.random())) ** 2
    x = x * env; return (x / np.sqrt(np.mean(x * x)) * rms).astype(np.float32)


class FakeStream:
    def __init__(self, **kw): self.kw, self.callback, self.latency, self.started, self.closed = kw, kw["callback"], (0.012, 0.02), False, False
    def start(self): self.started = True
    def abort(self): pass
    def close(self): self.closed = True


class FakeSD:
    def __init__(self): self.streams = []
    def Stream(self, **kw): s = FakeStream(**kw); self.streams.append(s); return s


class Passthrough:
    """A canceller that cancels nothing."""
    def reference(self, c): pass
    def process(self, c): return np.asarray(c, dtype=np.float32)


def room(duplex, played, user=None, echo_gain=0.07, delay=0.03, noise=0.0008, seed=1, on_frame=None):
    """Drive the duplex callback like PortAudio would: 10 ms blocks; the microphone hears the previous output with a delay plus room noise plus optional the user."""
    st = duplex.start() or duplex.stream; r = np.random.default_rng(seed); B = duplex.blocksize; d = int(delay * RATE)
    duplex.play.push(played); duplex.streaming = True
    n = (len(played) // B) * B; out_all = np.zeros(n + B, np.float32); got = []
    for i in range(0, n, B):
        mic = np.zeros(B, np.float32)
        lo = i - d
        if lo >= 0: mic += echo_gain * (out_all[lo:lo + B] + 0.35 * out_all[max(0, lo - 7):max(0, lo - 7) + B])
        mic += (noise * r.standard_normal(B)).astype(np.float32)
        if user is not None: mic += user[i:i + B]
        outdata = np.zeros((B, 1), np.float32); st.callback(mic.reshape(-1, 1).copy(), outdata, B, None, None)
        out_all[i:i + B] = outdata[:, 0]
        if on_frame: on_frame(i / RATE)
    return out_all[:n]


def rms(x): return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


class Resampling(unittest.TestCase):
    def test_decimator_does_not_care_about_chunk_boundaries(self):
        x = voice_like(1.0, 3); one = jd.Decimator(3)(x); d = jd.Decimator(3); parts = []; i = 0
        for k in (480, 17, 960, 1, 3, 4001, 480):
            parts.append(d(x[i:i + k])); i += k
        parts.append(d(x[i:])); many = np.concatenate(parts)
        self.assertEqual(len(one), len(many)); self.assertLess(np.max(np.abs(one - many)), 1e-5)

    def test_decimation_keeps_speech_level_and_removes_aliasing(self):
        t = np.arange(RATE) / RATE; speech = (0.1 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32); alias = (0.1 * np.sin(2 * np.pi * 12000 * t)).astype(np.float32)
        self.assertAlmostEqual(rms(jd.Decimator(3)(speech)[200:]), rms(speech), delta=0.004)
        self.assertLess(rms(jd.Decimator(3)(alias)[200:]), 0.002)

    def test_voice_upsampling_keeps_pitch_and_level(self):
        t = np.arange(24000) / 24000; x = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32); y = jd.Upsampler(24000, 48000)(x)
        self.assertEqual(len(y), 48000); self.assertAlmostEqual(rms(y[500:]), rms(x), delta=0.004)
        zc = np.sum(np.diff(np.signbit(y[500:-500]).astype(int)) != 0) / 2 / (len(y[500:-500]) / 48000)
        self.assertAlmostEqual(zc, 440, delta=4)


class Alignment(unittest.TestCase):
    def setUp(self):
        from julius_aec import EchoCanceller
        self.blocks = []
        self.d = jd.DuplexAudio(FakeSD(), lambda *a: self.blocks.append(a), rate=RATE, canceller=EchoCanceller(lag=0.0), monitor=jd.AecMonitor(log=lambda *a, **k: None))

    def test_reference_is_exactly_what_the_callback_played(self):
        played = voice_like(3.0, 5); out = room(self.d, played)
        self.assertTrue(np.array_equal(out, played[:len(out)]), "playback must reach the device unchanged")   # nothing in the way of his voice at full rate
        ref = np.concatenate([b[2] for b in self.blocks]); expect = jd.Decimator(3)(out)[:len(ref)]
        self.assertLess(np.max(np.abs(ref - expect[:len(ref)])), 1e-5, "the canceller reference is the decimated callback output, sample for sample")

    def test_echo_is_cancelled_with_aligned_reference(self):
        played = voice_like(10.0, 6); room(self.d, played)
        raw = np.concatenate([b[1] for b in self.blocks]); cln = np.concatenate([b[3] for b in self.blocks]); s = 16000 * 4
        erle = 20 * np.log10(rms(raw[s:]) / rms(cln[s:]))
        self.assertGreater(erle, 15.0, f"echo reduction only {erle:.1f} dB")
        self.assertLess(rms(cln[s:]), 0.002)

    def test_blocks_are_1024_samples_at_16k(self):
        room(self.d, voice_like(2.0, 7)); self.assertTrue(self.blocks and all(len(b[0]) == jd.BLOCK16 for b in self.blocks))

    def test_underrun_is_reported_only_while_streaming(self):
        d = self.d; d.start(); st = d.stream; out = np.zeros((480, 1), np.float32); mic = np.zeros((480, 1), np.float32)
        st.callback(mic, out, 480, None, None); self.assertFalse(d.starved)       # idle: silence is not an underflow
        d.streaming = True; d.play.push(np.ones(100, np.float32)); st.callback(mic, out, 480, None, None); self.assertTrue(d.starved)


class Fallback(unittest.TestCase):
    def make(self, canceller):
        self.fired = []
        mon = jd.AecMonitor(on_fallback=self.fired.append, fail_seconds=2.0, log=lambda *a, **k: None)
        self.blocks = []
        return jd.DuplexAudio(FakeSD(), lambda *a: self.blocks.append(a), rate=RATE, canceller=canceller, monitor=mon), mon

    def test_leaking_echo_puts_the_gate_back_after_a_few_seconds(self):
        d, mon = self.make(Passthrough()); gate = MicrophoneGate(); gate.bypass = True
        t_fire = []
        room(d, voice_like(12.0, 8), on_frame=lambda t: (t_fire.append(t) if mon.fell_back and not t_fire else None))
        self.assertTrue(mon.fell_back); self.assertEqual(len(self.fired), 1); self.assertIn("echo leaking", self.fired[0])
        self.assertGreater(t_fire[0], 1.8); self.assertLess(t_fire[0], 7.0, "fallback must come within a few seconds")

    def test_fallback_hands_the_gate_back(self):
        gate = MicrophoneGate(); gate.bypass = True
        with gate.output(): self.assertTrue(gate.accepting(), "bypass: the microphone stays open while he speaks")
        gate.bypass = False
        with gate.output(): self.assertFalse(gate.accepting())
        self.assertFalse(gate.accepting(), "and the settle time applies again")

    def test_working_canceller_does_not_fall_back(self):
        from julius_aec import EchoCanceller
        d, mon = self.make(EchoCanceller(lag=0.0)); room(d, voice_like(12.0, 9)); self.assertFalse(mon.fell_back, mon.reason)

    def test_roys_voice_over_a_working_canceller_is_not_mistaken_for_leaking_echo(self):
        from julius_aec import EchoCanceller
        d, mon = self.make(EchoCanceller(lag=0.0)); user = np.zeros(RATE * 12, np.float32); user[RATE * 3:] = voice_like(9.0, 99, rms=0.012)
        room(d, voice_like(12.0, 10), user=user); self.assertFalse(mon.fell_back, mon.reason)


class BargeIn(unittest.TestCase):
    def run_room(self, roy_from=None, seconds=8.0, roy_rms=0.008, canceller=True):
        from julius_aec import EchoCanceller
        self.hits = []; mon = jd.AecMonitor(log=lambda *a, **k: None); voice = jd.CleanTalkOver(mon)
        def on_block(block, raw, ref, cleaned):
            if voice.block(block): self.hits.append(self.now)
        self.now = 0.0
        d = jd.DuplexAudio(FakeSD(), on_block, rate=RATE, canceller=EchoCanceller(lag=0.0) if canceller else Passthrough(), monitor=mon)
        user = None
        if roy_from is not None: user = np.zeros(int(seconds * RATE), np.float32); user[int(roy_from * RATE):] = voice_like(seconds - roy_from, 77, rms=roy_rms)
        def tick(t): self.now = t
        room(d, voice_like(seconds, 11), user=user, on_frame=tick); return mon

    def test_speaking_over_him_triggers_quickly(self):
        self.run_room(roy_from=4.0)
        self.assertTrue(self.hits, "barge-in never fired"); self.assertGreaterEqual(self.hits[0], 4.0); self.assertLess(self.hits[0], 5.0, f"too slow: {self.hits[0] - 4.0:.2f} s after speech began")

    def test_quiet_voice_over_him_triggers(self):
        self.run_room(roy_from=4.0, roy_rms=0.005)
        self.assertTrue(self.hits and self.hits[0] < 5.2, self.hits)

    def test_his_own_voice_never_triggers_it(self):
        self.run_room(roy_from=None, seconds=14.0); self.assertEqual(self.hits, [], "false interruption by his own echo")

    def test_noise_floor_burst_does_not_trigger(self):
        mon = jd.AecMonitor(log=lambda *a, **k: None); voice = jd.CleanTalkOver(mon); r = np.random.default_rng(3)
        for _ in range(100): mon.feed(0.0008 * r.standard_normal(1600), 0.0008 * r.standard_normal(1600), np.zeros(1600))   # silence builds the noise floor estimate
        hits = [voice.block(0.0010 * r.standard_normal(1024)) for _ in range(40)]; self.assertFalse(any(hits))

    def test_not_in_the_first_moments_of_a_reply(self):
        mon = jd.AecMonitor(log=lambda *a, **k: None); voice = jd.CleanTalkOver(mon, holdoff=0.4)
        loud = (0.02 * np.random.default_rng(4).standard_normal(1024)).astype(np.float32)
        self.assertEqual(mon.play_age, 0.0)
        self.assertFalse(any(voice.block(loud) for _ in range(4)), "residual while the canceller settles must not stop a reply that has just begun")


class AbortAndAdapter(unittest.TestCase):
    def test_abort_unblocks_a_waiting_write_and_clears_the_buffer(self):
        d = jd.DuplexAudio(FakeSD(), lambda *a: None, rate=RATE); ad = d.shim().OutputStream(samplerate=24000, channels=1, dtype="float32", latency=0.4); ad.start()
        errors = []
        def writer():
            try:
                for _ in range(20): ad.write(np.zeros((12000, 1), np.float32) + 0.1)   # nothing consumes the buffer: the writer ends up waiting on back-pressure
            except RuntimeError as e: errors.append(str(e))
        t = threading.Thread(target=writer); t.start(); time.sleep(0.3); self.assertTrue(t.is_alive())
        t0 = time.time(); ad.abort(); t.join(2); self.assertFalse(t.is_alive()); self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(errors, ["playback aborted"]); self.assertEqual(d.play.seconds(RATE), 0.0, "speakers are silent at once")

    def test_speech_player_runs_on_the_duplex_stream(self):
        from julius_audio_player import SpeechPlayer
        d = jd.DuplexAudio(FakeSD(), lambda *a: None, rate=RATE); player = SpeechPlayer(d.shim(), prefill_chunks=1, sleep=lambda s: None)
        stop = threading.Event()
        def device():   # the "sound card": consumes 10 ms at a time
            st = None
            while not stop.is_set():
                if d.stream is not None:
                    st = d.stream; st.callback(np.zeros((480, 1), np.float32), np.zeros((480, 1), np.float32), 480, None, None)
                time.sleep(0.0005)
        th = threading.Thread(target=device, daemon=True); th.start()
        chunks = ((np.full(2400, 0.1, np.float32), 24000) for _ in range(5))
        res = player.play(chunks); stop.set(); th.join(1)
        self.assertEqual(res["chunks"], 5)
        player.close()


class Switch(unittest.TestCase):
    def test_off_by_default_in_defaults_and_toml(self):
        import julius_config
        self.assertFalse(julius_config.DEFAULTS["audio"]["duplex"]); self.assertFalse((julius_config.load_config().get("audio") or {}).get("duplex"), "julius.toml must keep duplex off until the live test passes")

    def test_candidate_wires_fallback_to_the_gate(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent / "julius_candidate.py").read_text()
        fb = src[src.index("def _duplex_fallback"):src.index("def cb(")]
        self.assertIn("microphone_gate.bypass = False", fb); self.assertIn("use_cleaned = False", fb); self.assertIn("duplex_fallback", fb)
        self.assertIn("_talked_over(duplex=True)", src); self.assertIn("microphone_gate.bypass = True", src)


if __name__ == "__main__":
    unittest.main()
