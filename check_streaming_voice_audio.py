"""Device-free intelligibility and chunk supply checks; no listener claims."""
import argparse,json,re,time
from pathlib import Path
import soundfile as sf
from scipy.signal import resample_poly
import mlx_whisper
p=argparse.ArgumentParser();p.add_argument('--paired-reminders',action='store_true');p.add_argument('--wording',action='store_true');p.add_argument('--input');p.add_argument('--output');args=p.parse_args()
out=Path(args.output or ('benchmarks/2026-10-02/mlx_audio_checks_wording' if args.wording else 'benchmarks/2026-10-02/mlx_audio_checks_paired' if args.paired_reminders else 'benchmarks/2026-10-02/mlx_audio_checks'));out.mkdir(parents=True,exist_ok=True)
rows=[]
def words(text):return re.findall(r"[a-z]+",text.lower())
def distance(a,b):
    d=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        prev=d;d=[i]
        for j,y in enumerate(b,1):d.append(min(d[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
    return d[-1]
cases=json.loads(Path('benchmarks/2026-10-02/mlx_worker/results.json').read_text())['cases']
if args.paired_reminders:
    cases=[r for r in json.loads(Path('benchmarks/2026-10-02/mlx_decoder_fixed/results.json').read_text())['cases'] if r['text'].startswith('Reminder')]
    cases += [r for r in json.loads(Path('benchmarks/2026-10-02/voice_paired/results.json').read_text())['cases'] if r['text'].startswith('Reminder')]
if args.wording:cases=json.loads(Path('benchmarks/2026-10-02/mlx_wording/results.json').read_text())
if args.input:
    loaded=json.loads(Path(args.input).read_text());cases=loaded['cases'] if isinstance(loaded,dict) else loaded
for row in cases:
    audio,rate=sf.read(row['path'],dtype='float32');started=time.perf_counter()
    result=mlx_whisper.transcribe(resample_poly(audio,16000,rate),
        path_or_hf_repo='mlx-community/whisper-large-v3-turbo',condition_on_previous_text=False,language='en')
    transcript=result['text'].strip();expected=words(row.get('spoken_text',row['text']))
    # Simulated playback starts when the first chunk arrives. Supply gaps require buffering.
    played=0;arrivals=row.get('arrivals',[]);start=arrivals[0]['seconds'] if arrivals else 0;max_deficit=0
    for chunk in arrivals:
        max_deficit=max(max_deficit,chunk['seconds']-start-played)
        played+=chunk['audio_seconds']
    check={'text':row['text'],'transcript':transcript,'word_error_rate':distance(expected,words(transcript))/len(expected),
        'asr_seconds':time.perf_counter()-started,'max_supply_deficit_seconds':max_deficit,'path':row['path']}
    rows.append(check);(out/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(check),flush=True)
