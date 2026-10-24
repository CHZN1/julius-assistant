"""Guarded two-model experiment: idle MLX voice alongside transcription."""
import argparse,ast,json,os,time
from pathlib import Path
os.environ['JULIUS_TTS']='qwen-mlx';os.environ['JULIUS_MLX_ASR_KEEP_VOICE']='1'
import mlx_whisper,numpy as np,soundfile as sf
from scipy.signal import resample_poly
from julius_tts import SpeechEngine
from julius_backend import close_backend
from julius_asr_resources import recognition_resources
p=argparse.ArgumentParser();p.add_argument('--cycles',type=int,default=1);p.add_argument('--output',default='benchmarks/2026-10-02/mlx_asr_keep_voice_bounded');p.add_argument('--sequential',action='store_true');args=p.parse_args()
if args.sequential:os.environ['JULIUS_MLX_ASR_KEEP_VOICE']='0'
if not 1<=args.cycles<=10:p.error('Use 1 to 10 cycles')
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
engine=SpeechEngine();worker_pid=engine.worker.pid
tree=ast.parse(Path('julius_candidate.py').read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='transcribe')
scope={'tts':engine,'os':os,'mlx_whisper':mlx_whisper,'ASR':'mlx-community/whisper-large-v3-turbo','close_backend':close_backend,'recognition_resources':recognition_resources}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'transcribe','exec'),scope)
rows=[]
try:
    for index in range(args.cycles*3):
        label=['balanced','lift','serious'][index%3]
        audio,rate=sf.read(f'benchmarks/2026-10-02/supervised_audio/{label}.wav',dtype='float32')
        started=time.perf_counter();text,lang,nsp=scope['transcribe'](resample_poly(audio,16000,rate));asr_seconds=time.perf_counter()-started
        if not args.sequential:assert engine.worker.pid==worker_pid and engine.worker.poll() is None
        started=time.perf_counter();first=None
        for speech,rate in engine.synthesize('I heard you, the user.'):
            if first is None:first=time.perf_counter()-started
        row={'cycle':index//3+1,'input':label,'text':text,'asr_seconds':asr_seconds,'speech_first_audio_seconds':first,
             'speech_total_seconds':time.perf_counter()-started,'worker_preserved':engine.worker.pid==worker_pid,'sequential':args.sequential}
        rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
finally:engine.close()
