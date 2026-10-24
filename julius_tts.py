"""Selectable local speech engines, loaded only when selected."""
import os
from pathlib import Path
import numpy as np
from julius_cancellation import check_cancel

class SpeechEngine:
    def __init__(self):
        self.backend = os.environ.get('JULIUS_TTS', 'kokoro')
        if self.backend == 'kokoro':
            from kokoro import KPipeline
            self.model = KPipeline(lang_code='a')
        elif self.backend == 'qwen-mlx' or (self.backend == 'qwen-clone' and os.environ.get('JULIUS_TTS_WORKER') != '1'):
            if self.backend == 'qwen-mlx' and os.environ.get('JULIUS_VOICE_STRATEGY', 'two-prompt') != 'two-prompt':
                raise ValueError('MLX speech currently requires the selected two-prompt voice strategy')
            self._isolated_worker = True
            self._start_worker()
            import atexit
            atexit.register(self.close)
        elif self.backend == 'qwen-clone':
            cache = Path(__file__).parent / ".cache" / "numba"
            cache.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("NUMBA_CACHE_DIR", str(cache))
            import torch
            if torch.backends.mps.is_available():
                torch.mps.set_per_process_memory_fraction(float(os.environ.get('JULIUS_TTS_MPS_FRACTION', '0.35')))
            self.dtype_name = 'bfloat16'
            self.strategy = os.environ.get('JULIUS_VOICE_STRATEGY', 'two-prompt')
            self._load_qwen(torch.bfloat16)
        else:
            raise ValueError(f'Unknown JULIUS_TTS: {self.backend}')

    def _start_worker(self):
        import json, subprocess
        root = Path(__file__).parent
        mlx = self.backend == 'qwen-mlx'
        executable = root / ('.venv-mlx-tts/bin/python' if mlx else '.venv-qwen-tts/bin/python')
        if not executable.exists():
            raise RuntimeError('Qwen voice environment missing; see JULIUS_TEST_RESULTS.md')
        self._worker_buffer = b''
        self.worker = subprocess.Popen([str(executable), str(root/('julius_mlx_tts_worker.py' if mlx else 'julius_tts_worker.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        line = self._worker_line(timeout=60)
        if not line or not json.loads(line).get('ready'):
            self.close()
            raise RuntimeError('Qwen speech worker failed to start')
        self.worker_metrics = {}

    def suspend(self):
        """Release the isolated voice weights before loading the reasoning model.

        With JULIUS_KEEP_VOICE=1 (cloud brain: nothing else needs the RAM) the voice is never unloaded."""
        if os.environ.get('JULIUS_KEEP_VOICE') == '1':
            return
        if getattr(self, '_isolated_worker', False):
            event=getattr(self,'cancel_event',None)
            if (self.backend=='qwen-mlx' and os.environ.get('JULIUS_MLX_KEEP_WORKER')=='1'
                    and not getattr(self,'_stream_active',False)
                    and not (event is not None and event.is_set())
                    and self.worker is not None and self.worker.poll() is None):
                try:
                    import json
                    self.worker.stdin.write('{"op":"unload"}\n');self.worker.stdin.flush()
                    reply=json.loads(self._worker_line(timeout=10))
                    if (reply.get('unloaded') is True
                            and isinstance(reply.get('active_memory_bytes'),int)
                            and 0 <= reply['active_memory_bytes'] < 100*1024**2):
                        self.unload_metrics=reply
                        return
                except Exception:
                    pass
            if event is not None and event.is_set() and self.worker is not None and self.worker.poll() is None:
                self.worker.terminate()
                self.worker.wait(timeout=5)
            self.close()
            self.worker = None

    def synthesize(self, text, speed=1.05):
        check_cancel(getattr(self,'cancel_event',None))
        if self.backend == 'kokoro':
            for _, _, audio in self.model(text, voice='am_adam', speed=speed):
                yield np.asarray(audio, dtype=np.float32), 24000
        elif getattr(self, '_isolated_worker', False):
            if self.worker is None or self.worker.poll() is not None:
                self._start_worker()
            import json
            self.worker.stdin.write(json.dumps({'text':text,'delivery_mode':getattr(self,'response_delivery_mode',None),'span_words':int(os.environ.get('JULIUS_MLX_SPAN_WORDS','36'))})+'\n')
            self.worker.stdin.flush()
            if self.backend == 'qwen-mlx':
                import time
                deadline = time.monotonic() + 120
                finished = False
                self._stream_active = True
                received = False
                try:
                    while True:
                        line = self._worker_line(timeout=max(0, min(90, deadline-time.monotonic())))
                        if not line: raise RuntimeError('MLX speech worker stopped')
                        result = json.loads(line)
                        if 'error' in result: raise RuntimeError(result['error'])
                        if result.get('done'):
                            if not received: raise RuntimeError('MLX worker produced no speech')
                            self.worker_metrics = result
                            finished = True
                            break
                        if not result.get('chunk'): raise RuntimeError('Invalid MLX worker response')
                        audio = np.load(result['path'], allow_pickle=False)
                        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                            raise FloatingPointError('Invalid MLX worker audio')
                        received = True
                        yield audio, result['sample_rate']
                finally:
                    # A partially consumed stream cannot leave stale messages for another request.
                    if not finished: self.suspend()
                    self._stream_active = False
                return
            line = self._worker_line(timeout=90)
            if not line:
                raise RuntimeError('Qwen speech worker stopped')
            result = json.loads(line)
            if 'error' in result:
                raise RuntimeError(result['error'])
            self.worker_metrics = result
            yield np.load(result['path'], allow_pickle=False), result['sample_rate']
        else:
            import torch
            from julius_voice_identity import delivery_mode, numerical_failure
            self.delivery_mode = (getattr(self,'response_delivery_mode',None) or delivery_mode(text)) if self.strategy == 'two-prompt' else 'composite'
            try:
                audio, sr = self._generate_qwen(text)
            except Exception as exc:
                if self.dtype_name != 'bfloat16' or not numerical_failure(exc):
                    raise
                # Retry numerical failure once; allocation failures do not trigger it.
                exc.__traceback__ = None
                self.model = None
                self.prompts = {}
                import gc
                gc.collect()
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                self.dtype_name = 'float32'
                self._load_qwen(torch.float32)
                audio, sr = self._generate_qwen(text)
            yield audio, sr

    def _load_qwen(self, dtype):
        import torch
        from qwen_tts import Qwen3TTSModel
        from julius_voice_identity import REF_TEXT, BALANCED_REFERENCE, LIFT_REFERENCE
        self.model = Qwen3TTSModel.from_pretrained(
            'Qwen/Qwen3-TTS-12Hz-0.6B-Base',
            device_map='mps' if torch.backends.mps.is_available() else 'cpu',
            dtype=dtype, attn_implementation='sdpa')
        from julius_voice_identity import configure_qwen_decoder
        configure_qwen_decoder(self.model)
        if self.strategy == 'two-prompt':
            balanced = Path(os.environ.get('JULIUS_VOICE_REFERENCE', str(BALANCED_REFERENCE)))
            lift = Path(os.environ.get('JULIUS_VOICE_LIFT_REFERENCE', str(LIFT_REFERENCE)))
            references = {'balanced': (balanced, os.environ.get('JULIUS_VOICE_REFERENCE_TEXT', REF_TEXT)),
                          'lift': (lift, os.environ.get('JULIUS_VOICE_LIFT_REFERENCE_TEXT', REF_TEXT))}
        elif self.strategy == 'composite':
            path = Path(os.environ.get('JULIUS_VOICE_COMPOSITE_REFERENCE',
                str(Path(__file__).parent/'julius_1301_tuning/1301_composite_reference.wav')))
            references = {'composite': (path, os.environ.get('JULIUS_VOICE_COMPOSITE_TEXT', REF_TEXT))}
        else:
            raise ValueError('JULIUS_VOICE_STRATEGY must be two-prompt or composite')
        self.prompts = {mode: self.model.create_voice_clone_prompt(
            ref_audio=str(path), ref_text=text, x_vector_only_mode=False)
            for mode, (path, text) in references.items()}

    def _generate_qwen(self, text):
        import torch
        from julius_voice_identity import speech_chunks
        self.generation_chunks = 0
        self.allocation_retries = 0
        def generate_span(chunk):
            words = chunk.split()
            try:
                with torch.inference_mode():
                    wavs, sr = self.model.generate_voice_clone(
                        text=chunk, language='English',
                        voice_clone_prompt=self.prompts[self.delivery_mode],
                        max_new_tokens=min(192, 24 + 12 * len(words)))
                audio = np.asarray(wavs[0], dtype=np.float32)
                if not len(audio) or not np.isfinite(audio).all():
                    raise FloatingPointError('Nonfinite or empty Qwen audio')
                self.generation_chunks += 1
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                return [(audio, sr)]
            except RuntimeError as exc:
                if 'mps backend out of memory' not in str(exc).lower() or len(words) < 4 or self.allocation_retries >= 3:
                    raise
                exc.__traceback__ = None
                import gc
                gc.collect()
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                self.allocation_retries += 1
                middle = len(words) // 2
                return generate_span(' '.join(words[:middle])) + generate_span(' '.join(words[middle:]))
        parts = []
        for chunk in speech_chunks(text):
            for audio, sr in generate_span(chunk):
                if parts:
                    parts.append(np.zeros(int(sr * .06), dtype=np.float32))
                parts.append(audio)
        if not parts:
            raise ValueError('No speech text')
        return np.concatenate(parts), sr

    def _worker_line(self, timeout):
        import select, time
        check_cancel(getattr(self,'cancel_event',None))
        deadline = time.monotonic() + timeout
        while b'\n' not in self._worker_buffer:
            try:
                check_cancel(getattr(self,'cancel_event',None))
            except Exception:
                self.worker.terminate()
                self.worker.wait(timeout=5)
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.worker.terminate()
                self.worker.wait(timeout=5)
                raise TimeoutError('Speech worker exceeded its response time limit')
            if not select.select([self.worker.stdout], [], [], min(remaining,.1))[0]:continue
            data = os.read(self.worker.stdout.fileno(), 65536)
            if not data: return ''
            self._worker_buffer += data
        line, self._worker_buffer = self._worker_buffer.split(b'\n', 1)
        return line.decode('utf-8')

    def close(self):
        if getattr(self, 'worker', None) is not None and self.worker.poll() is None:
            try:
                self.worker.stdin.write('{"op":"close"}\n')
                self.worker.stdin.flush()
                self.worker.wait(timeout=5)
            except Exception:
                self.worker.terminate()
                self.worker.wait(timeout=5)
        if getattr(self, 'worker', None) is not None:
            self.worker.stdin.close()
            self.worker.stdout.close()
