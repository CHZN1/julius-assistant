"""SpeechPlayer: prefill, persistent stream, underflow logging, adaptive prefill and clean cancellation."""
import unittest
import numpy as np
from julius_audio_player import SpeechPlayer

class FakeStream:
    def __init__(self,owner,**kw):self.owner,self.kw,self.writes,self.state=owner,kw,[],'new'
    def start(self):self.state='started';self.owner.log.append('start')
    def write(self,data):
        self.owner.log.append(('write',len(data),len(self.owner.pending_chunks)))
        self.writes.append(data);index=len(self.writes)-1
        return index in self.owner.underflow_on.get(id(self),self.owner.underflow_at)
    def stop(self):self.state='stopped';self.owner.log.append('stop')
    def abort(self):self.state='aborted';self.owner.log.append('abort')
    def close(self):self.owner.log.append('close')
class FakeSD:
    def __init__(self,underflow_at=()):
        self.log,self.streams,self.underflow_at,self.underflow_on,self.pending_chunks=[],[],set(underflow_at),{},[]
    def OutputStream(self,**kw):
        s=FakeStream(self,**kw);self.streams.append(s);return s
def chunks(n,rate=24000,size=7680,pulled=None):
    for i in range(n):
        if pulled is not None:pulled.append(i)
        yield np.full(size,0.1,dtype=np.float32),rate
def player(sd,**kw):
    kw.setdefault('idle_seconds',60);logged=[];p=SpeechPlayer(sd,log=lambda m,**k:logged.append(m),**kw);p.logged=logged;return p
class PlayerTests(unittest.TestCase):
    def test_playback_starts_only_after_one_extra_chunk_has_arrived(self):
        sd=FakeSD();p=player(sd);pulled=[]
        gen=chunks(4,pulled=pulled);order=[]
        # the first write must happen after chunk 1 (the second chunk) was pulled from the generator
        sd.pending_chunks=pulled
        p.play(gen);first_write=[e for e in sd.log if isinstance(e,tuple)][0]
        self.assertEqual(first_write[2],2);p.close()
    def test_device_gets_a_large_buffer_and_the_sentence_ends_on_a_silent_tail(self):
        sd=FakeSD();p=player(sd);p.play(chunks(3));s=sd.streams[0]
        self.assertEqual(s.kw['latency'],0.4);self.assertEqual(s.kw['samplerate'],24000)
        self.assertEqual(len(s.writes),4);self.assertTrue((s.writes[-1]==0).all());self.assertEqual(len(s.writes[-1]),480);p.close()
    def test_one_stream_serves_several_sentences_without_restarting(self):
        sd=FakeSD();p=player(sd);p.play(chunks(2));p.play(chunks(2));p.play(chunks(2))
        self.assertEqual(len(sd.streams),1);self.assertEqual(sd.log.count('start'),1);p.close();self.assertIn('stop',sd.log)
    def test_stream_closes_by_itself_when_idle(self):
        import time
        sd=FakeSD();p=player(sd,idle_seconds=0.05);p.play(chunks(2));time.sleep(0.2);self.assertIn('close',sd.log);self.assertIsNone(p.stream)
    def test_a_new_sample_rate_reopens_the_stream(self):
        sd=FakeSD();p=player(sd);p.play(chunks(2,rate=24000));p.play(chunks(2,rate=16000));self.assertEqual(len(sd.streams),2);p.close()
    def test_underflows_inside_a_sentence_are_logged_and_counted(self):
        sd=FakeSD(underflow_at={3});p=player(sd);stats=p.play(chunks(6))
        self.assertEqual(stats['underflows'],1);events=p.pop_events();self.assertEqual(events[0]['chunk'],3);self.assertEqual(p.pop_events(),[])
        self.assertTrue(any('audio underflow at chunk 3' in m for m in p.logged));self.assertEqual(p.total_underflows,1);p.close()
    def test_on_start_runs_right_before_the_first_audio_is_written(self):
        sd=FakeSD();p=player(sd);seen=[]
        p.play(chunks(3),on_start=lambda:seen.append(len(sd.streams[0].writes)));self.assertEqual(seen,[0]);p.close()
    def test_the_first_write_of_a_sentence_is_not_an_underflow(self):
        sd=FakeSD(underflow_at={0});p=player(sd);self.assertEqual(p.play(chunks(3))['underflows'],0);self.assertEqual(p.events,[]);p.close()
    def test_prefill_grows_after_an_underflow_and_relaxes_after_clean_sentences(self):
        sd=FakeSD(underflow_at={2});p=player(sd,prefill_chunks=1)
        p.play(chunks(5));self.assertEqual(p.prefill_chunks,2)
        sd.underflow_at=set()
        for _ in range(5):p.play(chunks(3))
        self.assertEqual(p.prefill_chunks,1);p.close()
    def test_prefill_is_capped(self):
        sd=FakeSD(underflow_at={2});p=player(sd,prefill_chunks=3,max_prefill=4)
        for _ in range(4):p.play(chunks(8))
        self.assertEqual(p.prefill_chunks,4);p.close()
    def test_short_sentences_play_even_when_fewer_chunks_exist_than_the_prefill(self):
        sd=FakeSD();p=player(sd,prefill_chunks=3);stats=p.play(chunks(1));self.assertEqual(stats['chunks'],1);self.assertEqual(len(sd.streams[0].writes),2);p.close()
    def test_cancellation_aborts_the_stream_and_the_next_sentence_reopens_it(self):
        sd=FakeSD();p=player(sd)
        def cancel():raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):p.play(chunks(6),cancel_check=cancel)
        self.assertIn('abort',sd.log);self.assertIsNone(p.stream);p.play(chunks(2));self.assertEqual(len(sd.streams),2);p.close()
    def test_changing_sample_rate_inside_a_sentence_is_an_error(self):
        def mixed():
            yield np.zeros(100,dtype=np.float32),24000;yield np.zeros(100,dtype=np.float32),24000;yield np.zeros(100,dtype=np.float32),16000
        sd=FakeSD();p=player(sd)
        with self.assertRaises(RuntimeError):p.play(mixed())
        self.assertIn('abort',sd.log)
    def test_empty_generator_plays_nothing(self):
        sd=FakeSD();p=player(sd);self.assertEqual(p.play(iter([]))['chunks'],0);self.assertEqual(sd.streams,[])
if __name__=='__main__':unittest.main()
