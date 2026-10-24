"""Run a bounded test in its own process group and stop on resource pressure."""
import argparse, json, os, re, shutil, signal, subprocess, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=120);p.add_argument('--log',required=True);p.add_argument('--minimum-free-percent',type=int,default=20);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
if not 20<=a.minimum_free_percent<=80:p.error('Minimum free percentage must be between 20 and 80')
if a.command and a.command[0]=='--':a.command=a.command[1:]
def snapshot():
 s=subprocess.check_output(['/usr/sbin/sysctl','vm.swapusage'],text=True,timeout=5)
 swap=float(re.search(r'used = ([\d.]+)M',s).group(1))
 pressure=subprocess.check_output(['/usr/bin/memory_pressure','-Q'],text=True,timeout=5)
 free=int(re.search(r'System-wide memory free percentage: (\d+)%',pressure).group(1))
 return {'swap_mb':swap,'memory_free_percent':free,'disk_free_gb':shutil.disk_usage('.').free/1024**3}
log=Path(a.log);log.parent.mkdir(parents=True,exist_ok=True);records=[];initial=snapshot();start=time.monotonic();reason=None
with log.open('w') as output:
 child=subprocess.Popen(a.command,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
 try:
  while child.poll() is None:
   state=snapshot();state['elapsed_seconds']=round(time.monotonic()-start,2);records.append(state)
   with log.with_suffix('.resources.jsonl').open('a') as telemetry:
    telemetry.write(json.dumps(state)+'\n');telemetry.flush()
   if state['swap_mb']-initial['swap_mb']>512:reason='Swap grew more than 512 MB'
   elif state['memory_free_percent']<a.minimum_free_percent:reason='Memory pressure threshold reached'
   elif state['disk_free_gb']<10:reason='Less than 10 GB disk headroom'
   elif time.monotonic()-start>a.seconds:reason='Test time limit reached'
   if reason:
    os.killpg(child.pid,signal.SIGINT)
    try:child.wait(timeout=8)
    except subprocess.TimeoutExpired:
     os.killpg(child.pid,signal.SIGTERM)
     try:child.wait(timeout=5)
     except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    break
   time.sleep(2)
 finally:
  if child.poll() is None:os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=10)
summary={'initial':initial,'samples':records,'stop_reason':reason,'exit_code':child.returncode,'command':a.command}
log.with_suffix('.resources.json').write_text(json.dumps(summary,indent=2));print(json.dumps({'exit_code':child.returncode,'stop_reason':reason,'seconds':round(time.monotonic()-start,2),'peak_swap_mb':max([initial['swap_mb']]+[r['swap_mb'] for r in records]),'minimum_free_percent':min([initial['memory_free_percent']]+[r['memory_free_percent'] for r in records]),'log':str(log)}),flush=True)
raise SystemExit(0 if child.returncode==0 and reason is None else 1)
