"""Device-free, bounded comparison using the actual selected Julius references."""
import argparse, hashlib, json, time, wave
from pathlib import Path
import mlx.core as mx
import numpy as np
from mlx_audio.tts.utils import load_model
from mlx_audio.utils import load_audio
from julius_voice_identity import BALANCED_REFERENCE, LIFT_REFERENCE, REF_TEXT, delivery_mode

p = argparse.ArgumentParser()
p.add_argument('--output', default='benchmarks/2026-10-02/mlx_voice')
p.add_argument('--public-defaults', action='store_true')
p.add_argument('--cached-codes', action='store_true')
a = p.parse_args()
out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
mx.set_cache_limit(256 * 1024**2)
mx.set_memory_limit(5 * 1024**3)
started = time.perf_counter()
from julius_mlx_voice_support import load_voice_model
model = load_voice_model()
load_seconds = time.perf_counter() - started
refs = {mode: load_audio(str(path), sample_rate=24000) for mode, path in
        [('balanced', BALANCED_REFERENCE), ('lift', LIFT_REFERENCE)]}
metadata = {mode: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'seconds': len(refs[mode])/24000} for mode, path in
            [('balanced', BALANCED_REFERENCE), ('lift', LIFT_REFERENCE)]}
config = model.generate_config
if a.cached_codes:
    for mode, path in [('balanced',BALANCED_REFERENCE),('lift',LIFT_REFERENCE)]:
        cache=Path('.cache/julius-mlx-prompts')
        info=json.loads((cache/f'{mode}.json').read_text())
        assert info['sha256']==metadata[mode]['sha256'] and info['text']==REF_TEXT
        # Official prompt codec shape [time,16]; MLX ICL expects [1,16,time].
        codes=mx.array(np.load(cache/f'{mode}.npy',allow_pickle=False).T[None])
        chat=f'<|im_start|>assistant\n{REF_TEXT}<|im_end|>\n'
        ids=mx.array(model.tokenizer.encode(chat))[None,3:-2]
        model._icl_cache[(REF_TEXT,(refs[mode].size,float(refs[mode].sum())))]=(codes,ids)
rows = []
def save():
    (out/'results.json').write_text(json.dumps({'load_seconds':load_seconds,
        'references':metadata, 'generation_config':config, 'public_defaults':a.public_defaults,
        'cases':rows}, indent=2))
save()
texts = ['Hey the user, I am here.', 'Great, that worked! I am excited to see what comes next.',
         'Reminder: the user, this is your Julius reminder test.']
for repetition in range(2):
    for stream, interval in [(True, .32), (True, .8), (False, 2.0)]:
        for index, text in enumerate(texts):
            mode = delivery_mode(text); mx.random.seed(1301+index)
            start = time.perf_counter(); cpu = time.process_time(); chunks=[]; arrivals=[]
            kwargs = dict(text=text, ref_audio=refs[mode], ref_text=REF_TEXT,
                          max_tokens=192, stream=stream, streaming_interval=interval)
            # Public generate silently raises the checkpoint's 1.05 penalty to 1.5.
            # Explicit ICL invocation preserves the selected checkpoint sampling defaults.
            generator = (model.generate(lang_code='English', **kwargs) if a.public_defaults else
                model._generate_icl(language='English', temperature=config['temperature'],
                    top_k=config['top_k'], top_p=config['top_p'],
                    repetition_penalty=config['repetition_penalty'], **kwargs))
            for result in generator:
                audio=np.asarray(result.audio, dtype=np.float32)
                if not audio.size or not np.isfinite(audio).all():
                    raise FloatingPointError('Invalid generated audio')
                arrivals.append({'seconds':time.perf_counter()-start,'samples':len(audio)})
                chunks.append(audio)
            if not chunks: raise RuntimeError('No speech generated')
            elapsed=time.perf_counter()-start; audio=np.concatenate(chunks)
            path=out/f'{repetition}_{index}_{"stream" if stream else "full"}_{interval}.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                wav.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
            row={'repetition':repetition,'text':text,'mode':mode,'stream':stream,
                 'interval':interval,'first_audio_seconds':arrivals[0]['seconds'],
                 'total_seconds':elapsed,'cpu_seconds':time.process_time()-cpu,
                 'audio_seconds':len(audio)/24000,'chunks':arrivals,
                 'peak_memory_bytes':mx.get_peak_memory(), 'path':str(path)}
            rows.append(row); save(); print(json.dumps(row),flush=True)
            mx.clear_cache()
