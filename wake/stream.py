"""Streaming wake-word detection: openWakeWord stage 1, optional Whisper stage 2 (the cascade).

WakeStream.push(frame) takes 80 ms of 16 kHz int16 audio and returns the wake events that were decided on that frame:
  {"fire": seconds, "decided": seconds, "accepted": bool, "heard": str, "verify_s": float}
Without a verifier the stage-1 fire is the decision. With one, the stream waits `after` seconds after the fire, hands the last
back+after seconds of audio to verify(clip) -> (accepted, heard, seconds), and decides on that. The ring buffer keeps the last 6 s."""
from collections import deque
import numpy as np

SR, FRAME = 16000, 1280

class WakeStream:
    def __init__(self, model_path, threshold, verify=None, after=1.5, back=2.0, refractory=2.0):
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[str(model_path)], inference_framework="onnx"); self.name = list(self.model.models)[0]
        self.threshold, self.verify, self.after, self.back, self.refractory = threshold, verify, after, back, refractory
        self.reset()

    def reset(self):
        self.model.reset(); self.n = 0; self.ring = deque(maxlen=int(6 * SR / FRAME) + 1); self.pending = None; self.quiet_until = 0; self.fires = 0

    def push(self, frame):
        self.ring.append(frame); self.n += 1; now = self.n * FRAME / SR; events = []
        score = float(self.model.predict(frame)[self.name])
        if self.pending is None and score >= self.threshold and now >= self.quiet_until:
            self.fires += 1; self.quiet_until = now + self.refractory
            if self.verify is None: return [{"fire": now, "decided": now, "accepted": True, "heard": "", "verify_s": 0.0, "score": score}]
            self.pending = {"fire": now, "due": now + self.after, "score": score}
        if self.pending and now >= self.pending["due"]:
            clip = np.concatenate(list(self.ring))[-int((self.back + self.after) * SR):]
            ok, heard, secs = self.verify(clip); p = self.pending; self.pending = None
            events.append({"fire": p["fire"], "decided": now, "accepted": ok, "heard": heard, "verify_s": secs, "score": p["score"]})
        return events

def run_clip(stream, audio, tail=2.5):
    """Feed one float clip (plus trailing silence so a pending verification completes); return every decided event."""
    stream.reset()
    x = (np.clip(np.concatenate([audio, np.zeros(int(tail * SR), np.float32)]), -1, 1) * 32767).astype(np.int16)
    events = []
    for i in range(0, len(x) - FRAME + 1, FRAME): events += stream.push(x[i:i + FRAME])
    return events

def whisper_verify():
    """Stage 2: small.en with the vocabulary hint on the short clip; accepts only a transcript the wake regex matches (Julius / hey Julius)."""
    import mlx_whisper, mlx.core as mx
    import julius_config
    from julius_listening import has_wake
    hint = julius_config.asr_initial_prompt(); repo = "mlx-community/whisper-small.en-mlx"
    import time
    def verify(clip):
        mx.random.seed(1301); t0 = time.perf_counter()
        text = mlx_whisper.transcribe(clip.astype(np.float32), path_or_hf_repo=repo, condition_on_previous_text=False, language="en", no_speech_threshold=0.6,
                                      **({"initial_prompt": hint} if hint else {}))["text"].strip()
        return has_wake(text), text, time.perf_counter() - t0
    verify(np.zeros(SR * 2, np.float32))  # warm-up: model load is not part of a wake
    return verify
