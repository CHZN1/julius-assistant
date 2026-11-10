"""Streaming wake-word detection, cascade v2: openWakeWord stage 1 -> optional speaker verifier -> Whisper on the whole utterance.

WakeStream.push(frame) takes 80 ms of 16 kHz int16 audio and returns the wake events decided on that frame:
  {"fire", "decided", "accepted", "heard", "request", "verify_s", "score", "speaker", "reason": "" | "speaker" | "whisper", "clip": audio of a rejected fire}   (times in seconds of audio)
Stage 1 fires at a LOW threshold. The stream then keeps capturing until the speaker stops (`end_silence` s of quiet, at most `max_after`
s), cuts the utterance back to where that speech began, and hands the whole thing - wake word plus request - to verify(clip) ->
(accepted, text, seconds). One Whisper run serves the wake check and the request: `request` is the transcript with the wake word removed.
With verify=None the stage-1 fire is the decision (detector alone). An optional speaker verifier (wake/verifier.py) rejects a fire whose
audio features do not match the owner's voice before any Whisper run is spent. The ring buffer keeps the last 12 s."""

from collections import deque

import numpy as np

SR, FRAME = 16000, 1280


class WakeStream:
    def __init__(
        self,
        model_path,
        threshold,
        verify=None,
        speaker=None,
        speaker_threshold=0.5,
        end_silence=0.6,
        max_after=12.0,
        refractory=2.0,
        gate=True,
        level_floor=0.0015,
    ):
        from openwakeword.model import Model

        self.model = Model(wakeword_models=[str(model_path)], inference_framework="onnx")
        self.name = list(self.model.models)[0]
        self.threshold, self.verify, self.speaker, self.speaker_threshold = (
            threshold,
            verify,
            speaker,
            speaker_threshold,
        )
        self.end_silence, self.max_after, self.refractory = end_silence, max_after, refractory
        self.level_floor = level_floor
        self.speaker_calls = 0
        self.speaker_s = 0.0
        self.gate = gate
        self.scored = 0
        self.frames = 0
        self.reset()

    def reset(self):
        self.model.reset()
        self.n = 0
        self.ring = deque(maxlen=int(12 * SR / FRAME) + 1)
        self.level = deque(maxlen=int(12 * SR / FRAME) + 1)
        self.pending = None
        self.quiet_until = 0.0
        self.fires = 0
        self.speaker_rejects = 0
        self.hang = 99
        self.gated = True

    def _threshold(self):
        quiet = np.percentile(np.array(self.level), 20) if len(self.level) > 10 else 0.002
        return min(0.02, max(self.level_floor, quiet * 2.2))

    def _utterance_start(self):
        """(ring index where the speech nearest the fire begins, frames of silence since it ended). Gaps up to 0.4 s stay inside the utterance;
        the fire often lands after the word, so first look back (up to 3 s) for the most recent speech."""
        th = self._threshold()
        lv = list(self.level)
        i = len(lv) - 1
        while i >= 0 and lv[i] <= th and len(lv) - 1 - i < 38:
            i -= 1
        trailing = len(lv) - 1 - i
        if i < 0 or lv[i] <= th:
            return len(lv) - 1, trailing
        start = i
        gap = 0
        while i >= 0:
            if lv[i] > th:
                start = i
                gap = 0
            else:
                gap += 1
                if gap > 5:
                    break
            i -= 1
        return max(0, start - 2), trailing

    def push(self, frame):
        x = frame.astype(np.float32) / 32768
        self.ring.append(frame)
        self.level.append(float(np.abs(x).mean()))
        self.n += 1
        now = self.n * FRAME / SR
        events = []
        self.frames += 1
        if self.gate and self.pending is None:
            # Noise gate: only score audio above the noise floor. After 1.6 s of quiet (longer than the detector's 1.28 s window) frames are skipped;
            # when sound returns, the detector restarts on the last 1.3 s so a wake word that begins the sound is not cut.
            quiet = np.percentile(np.array(self.level), 20) if len(self.level) > 10 else 0.002
            loud = self.level[-1] > max(0.0015, 1.5 * quiet)
            self.hang = 0 if loud else self.hang + 1
            if self.hang > 20:
                self.gated = True
                return []
            if self.gated:
                self.gated = False
                self.model.reset()
                for f in list(self.ring)[-17:-1]:
                    self.model.predict(f)
        self.scored += 1
        score = float(self.model.predict(frame)[self.name])
        if self.pending is None and score >= self.threshold and now >= self.quiet_until:
            self.fires += 1
            self.quiet_until = now + self.refractory
            if self.speaker is not None:
                import time

                t0 = time.perf_counter()
                feats = self.model.preprocessor.get_features(self.model.model_inputs[self.name])
                sp = float(
                    self.speaker.predict_proba(np.asarray(feats, dtype=np.float32).reshape(1, -1))[
                        0
                    ][1]
                )
                self.speaker_calls += 1
                self.speaker_s += time.perf_counter() - t0
                if sp < self.speaker_threshold:
                    self.speaker_rejects += 1
                    return [
                        {
                            "fire": now,
                            "decided": now,
                            "accepted": False,
                            "reason": "speaker",
                            "heard": "",
                            "request": "",
                            "verify_s": 0.0,
                            "score": score,
                            "speaker": sp,
                            "clip": np.concatenate(list(self.ring)[-38:]).astype(np.float32)
                            / 32768,
                        }
                    ]
            else:
                sp = None
            if self.verify is None:
                return [
                    {
                        "fire": now,
                        "decided": now,
                        "accepted": True,
                        "reason": "",
                        "heard": "",
                        "request": "",
                        "verify_s": 0.0,
                        "score": score,
                        "speaker": sp,
                    }
                ]
            idx, trailing = self._utterance_start()
            self.pending = {
                "fire": now,
                "start_n": self.n - (len(self.level) - 1 - idx),
                "score": score,
                "speaker": sp,
                "silent": trailing,
            }
        if self.pending:
            self.pending["silent"] = (
                self.pending["silent"] + 1 if self.level[-1] <= self._threshold() else 0
            )
            done = (
                self.pending["silent"] * FRAME / SR >= self.end_silence
                or now - self.pending["fire"] >= self.max_after
            )
            if done:
                p = self.pending
                self.pending = None
                frames = list(self.ring)
                first = max(0, len(frames) - 1 - (self.n - p["start_n"]))
                clip = np.concatenate(frames[first:]).astype(np.float32) / 32768
                ok, heard, secs = self.verify(clip)
                from julius.audio.listening import strip_wake

                events.append(
                    {
                        "fire": p["fire"],
                        "decided": now,
                        "accepted": ok,
                        "reason": "" if ok else "whisper",
                        "heard": heard,
                        "request": strip_wake(heard) if ok else "",
                        "verify_s": secs,
                        "score": p["score"],
                        "speaker": p["speaker"],
                        "clip": clip,
                    }
                )
        return events


def run_clip(stream, audio, tail=2.5):
    """Feed one float clip (plus trailing silence so a pending utterance completes); return every decided event."""
    stream.reset()
    x = (
        np.clip(
            np.concatenate(
                [np.zeros(int(0.5 * SR), np.float32), audio, np.zeros(int(tail * SR), np.float32)]
            ),
            -1,
            1,
        )
        * 32767
    ).astype(np.int16)
    events = []
    for i in range(0, len(x) - FRAME + 1, FRAME):
        events += stream.push(x[i : i + FRAME])
    return events


def whisper_verify():
    """Stage 2: small.en on the whole utterance with the production hint (wake word next to its sound-alikes, then the vocabulary);
    accepts only a transcript the wake regex matches at the start (Julius / hey Julius, not Julia or Julian)."""
    import time

    import mlx.core as mx
    import mlx_whisper

    from julius import config
    from julius.audio.listening import has_wake
    from julius.audio.speech_text import fix_transcript

    hint = config.asr_initial_prompt(wake=True)
    repo = "mlx-community/whisper-small.en-mlx"

    def verify(clip):
        mx.random.seed(1301)
        t0 = time.perf_counter()
        text = fix_transcript(
            mlx_whisper.transcribe(
                clip.astype(np.float32),
                path_or_hf_repo=repo,
                condition_on_previous_text=False,
                language="en",
                no_speech_threshold=0.6,
                **({"initial_prompt": hint} if hint else {}),
            )["text"].strip()
        )
        return has_wake(text), text, time.perf_counter() - t0

    verify(np.zeros(SR * 2, np.float32))  # warm-up: model load is not part of a wake
    return verify
