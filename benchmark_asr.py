from julius_benchmark_paths import BENCHMARK_DIR
import json, time
from pathlib import Path
import soundfile as sf
from scipy.signal import resample_poly
import mlx_whisper
rows=[]
paths=[Path('julius_1301_tuning/1301_a_balanced.wav')]+sorted(BENCHMARK_DIR.glob('benchmark_*.wav'))
for path in paths:
 audio,sr=sf.read(path,dtype='float32');audio=resample_poly(audio,16000,sr)
 t=time.perf_counter();r=mlx_whisper.transcribe(audio,path_or_hf_repo='mlx-community/whisper-large-v3-turbo',condition_on_previous_text=False)
 row={'file':str(path),'seconds':time.perf_counter()-t,'text':r['text'].strip()};rows.append(row); print(json.dumps(row),flush=True)
 (BENCHMARK_DIR/'benchmark_asr.json').write_text(json.dumps(rows,indent=2))
