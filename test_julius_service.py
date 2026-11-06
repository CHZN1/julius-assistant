import json, os, plistlib, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
import julius_service, julius_log, julius_state
from julius_microphone_gate import MicrophoneGate
class Result:
    def __init__(self,rc=0,out='',err=''):self.returncode=rc;self.stdout=out;self.stderr=err
class ServiceTests(unittest.TestCase):
    def test_plist_starts_at_login_restarts_on_crash_and_holds_no_secrets(self):
        d=julius_service.plist_dict(Path('/repo'),'/tmp/logs');self.assertTrue(d['RunAtLoad']);self.assertEqual(d['KeepAlive'],{'SuccessfulExit':False})
        self.assertEqual(d['ProgramArguments'][-1],'/repo/run_julius.sh');self.assertEqual(d['EnvironmentVariables']['JULIUS_SERVICE'],'1')
        text=json.dumps(d).upper();self.assertNotIn('API_KEY',text);self.assertNotIn('SK-',text)
    def test_on_writes_the_plist_and_bootstraps_it(self):
        calls=[];tmp=Path(tempfile.mkdtemp())/'x.plist'
        def runner(args,**k):calls.append(args);return Result()
        ok,_=julius_service.on(runner,tmp);self.assertTrue(ok);self.assertEqual(plistlib.loads(tmp.read_bytes())['Label'],julius_service.LABEL)
        self.assertTrue(any(a[1]=='bootstrap' for a in calls));self.assertTrue(any(a[1]=='enable' for a in calls))
    def test_off_boots_out_disables_and_removes_the_plist(self):
        calls=[];tmp=Path(tempfile.mkdtemp())/'x.plist';tmp.write_text('x')
        def runner(args,**k):calls.append(args);return Result()
        julius_service.off(runner,tmp);self.assertFalse(tmp.exists());self.assertTrue(any(a[1]=='bootout' for a in calls));self.assertTrue(any(a[1]=='disable' for a in calls))
    def test_status_parses_launchctl(self):
        out="\tstate = running\n\tpid = 123\n\tlast exit code = (never exited)\n\truns = 2\n"
        st=julius_service.status(lambda args,**k:Result(0,out));self.assertEqual(st['state'],'running');self.assertEqual(st['pid'],'123')
        self.assertFalse(julius_service.status(lambda args,**k:Result(113))['loaded'])
class LogsCommandTests(unittest.TestCase):
    def test_recent_lines_are_shown_readably_then_new_ones_follow(self):
        d=Path(tempfile.mkdtemp());f=d/'j.jsonl';f.write_text(julius_log.record('info','console','[asleep]')+"\n"+julius_log.record('info','quiet','',on=True)+"\n")
        out=[];n=[0]
        def sleep(_):
            n[0]+=1
            if n[0]==1:f.write_text(f.read_text()+julius_log.record('error','console','boom')+"\n")
        julius_service.show_logs(10,path=f,out=out.append,sleep=sleep,stop=lambda:n[0]>=2)
        self.assertEqual(len(out),3);self.assertIn('[asleep]',out[0]);self.assertIn('quiet',out[1]);self.assertIn('on',out[1]);self.assertTrue(out[2].endswith('boom'))
    def test_missing_log_says_so(self):
        out=[];julius_service.show_logs(path=Path(tempfile.mkdtemp())/'none.jsonl',out=out.append);self.assertIn('no log yet',out[0])
    def test_doctor_reports_helper_verdict(self):
        d=tempfile.mkdtemp()
        def runner(args,**k):
            if args[1]=='bootstrap':(Path(d)/'julius_doctor.out').write_text('ready stop wake\n')
            return Result()
        v,_=julius_service.doctor(runner,lambda s:None,d,binary='/x/h');self.assertEqual(v,'ready stop wake')
    def test_doctor_reports_a_failed_combo(self):
        d=tempfile.mkdtemp()
        def runner(args,**k):
            if args[1]=='bootstrap':(Path(d)/'julius_doctor.out').write_text('failed wake status=-9878\nready stop\n')
            return Result()
        v,_=julius_service.doctor(runner,lambda s:None,d,binary='/x/h');self.assertIn('failed wake',v)
class QuietTests(unittest.TestCase):
    def test_quiet_gate_drops_all_audio_until_switched_off(self):
        g=MicrophoneGate(clock=lambda:100.);self.assertTrue(g.accepting());g.set_quiet(True);self.assertFalse(g.accepting());g.set_quiet(False);self.assertTrue(g.accepting())
    def test_quiet_survives_a_restart(self):
        os.environ['JULIUS_STATE_DIR']=tempfile.mkdtemp();self.assertFalse(julius_state.is_quiet());julius_state.set_quiet(True);self.assertTrue(julius_state.is_quiet());julius_state.set_quiet(False);self.assertFalse(julius_state.is_quiet())
class LogTests(unittest.TestCase):
    def test_lines_are_json_rotated_and_redacted(self):
        d=Path(tempfile.mkdtemp());log=julius_log.make_logger(d/'j.jsonl',max_bytes=2000,backups=2)
        class Out:
            def write(self,s):pass
            def flush(self):pass
        tee=julius_log.Tee(Out(),log,'info')
        tee.write("  YOU [en]: my secret question\n  [heard 'hello' lang=en]\n  key sk-ant-TEST-NOT-A-KEY\n  [asleep]\nFetching 4 files: 100%|█| 4/4\n")
        rows=[json.loads(l) for l in (d/'j.jsonl').read_text().splitlines()]
        msgs=[r['msg'] for r in rows];self.assertEqual(msgs[0],'[transcript redacted]');self.assertEqual(msgs[1],'[transcript redacted]');self.assertIn('[redacted]',msgs[2]);self.assertEqual(msgs[3],'[asleep]');self.assertEqual(len(rows),4)
        for i in range(200):tee.write(f"  line number {i} with padding padding padding\n")
        self.assertTrue((d/'j.jsonl.1').exists());self.assertFalse((d/'j.jsonl.3').exists())
    def test_transcripts_can_be_kept_when_enabled(self):
        d=Path(tempfile.mkdtemp());log=julius_log.make_logger(d/'j.jsonl')
        class Out:
            def write(self,s):pass
            def flush(self):pass
        julius_log.Tee(Out(),log,'info',True).write("  YOU [en]: hi\n");self.assertIn('hi',(d/'j.jsonl').read_text())
class LiveIssueTests(unittest.TestCase):
    def test_the_thinking_remnant_is_erased_not_overprinted(self):
        """Regression: '\\r  JULIUS: ' left the tail of '...thinking' on screen, which looked like a spoken 'ing' before tool calls."""
        src=Path('julius_candidate.py').read_text();self.assertIn('print("\\r\\033[K  JULIUS: "',src)
    def test_cloud_profile_keeps_whisper_resident(self):
        self.assertIn('JULIUS_ASR_RESIDENT="${JULIUS_ASR_RESIDENT:-1}"',Path('run_julius.sh').read_text())
        self.assertIn("recognition_resources(_os.environ.get('JULIUS_ASR_RESIDENT')!='1')",Path('julius_candidate.py').read_text())
    def test_log_strips_terminal_escapes(self):
        d=Path(tempfile.mkdtemp());log=julius_log.make_logger(d/'j.jsonl')
        class Out:
            def write(self,s):pass
            def flush(self):pass
        julius_log.Tee(Out(),log,'info').write("\r\x1b[K  [asleep]\n");self.assertIn('"msg": "[asleep]"',(d/'j.jsonl').read_text())
if __name__=='__main__':unittest.main()
