"""Check speech completeness and silent/noise rejection without vocabulary bias."""
import json,time,numpy as np,soundfile as sf,mlx_whisper
from scipy.signal import resample_poly
from julius_asr_resources import recognition_resources
from julius_benchmark_paths import BENCHMARK_DIR
import ast,os
from pathlib import Path
from unittest.mock import Mock
fn=next(n for n in ast.parse(Path('julius_candidate.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='transcribe')
ns={'os':os,'tts':Mock(backend='kokoro'),'close_backend':Mock(),'recognition_resources':recognition_resources,'mlx_whisper':mlx_whisper,'ASR':'mlx-community/whisper-large-v3-turbo'}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'runtime','exec'),ns)
rows=[]
inputs=[('silence',np.zeros(16000*3,dtype=np.float32)),('noise',np.random.default_rng(1301).normal(0,.001,16000*3).astype(np.float32))]
for limit in (12,36):
 audio,sr=sf.read(f'benchmarks/2026-10-05/span_audio/speech_span_{limit}_0.wav',dtype='float32')
 inputs.append((f'speech_{limit}',resample_poly(audio,16000,sr)))
for name,audio in inputs:
 started=time.perf_counter()
 text,language,nsp=ns['transcribe'](audio)
 accepted=bool(text) and nsp<=.6
 if name in ('silence','noise'):assert not accepted,(name,text,nsp)
 else:assert accepted and 'overfitting' in text.lower() and 'unseen data' in text.lower(),(name,text)
 row={'input':name,'text':text,'no_speech_probability':nsp,'accepted':accepted,'seconds':time.perf_counter()-started}
 rows.append(row);print(json.dumps(row),flush=True)
 (BENCHMARK_DIR/'recognition_silence.json').write_text(json.dumps(rows,indent=2))
