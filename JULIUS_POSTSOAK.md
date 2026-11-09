# After the soak (ends 2026-11-09 00:13)

1. `python julius_soak.py report --since 2026-11-08T00:13` and `python julius_logscan.py --since 2026-11-08T00:13` (read the soak first).
2. `python julius_service.py smoke --restart` (the feed and the log_transcripts window start with this restart).
3. Feed live: `python julius_events_client.py` shows `hello`, then states and levels as you talk. Port 8765 must be free of the synthetic source (it uses 8766).
4. UI: `python julius_orb.py build`, then `python julius_orb.py run` to try it (Ctrl+Option+O hides/shows), then `python julius_orb.py on` for login start.

## Voice test (about 5 minutes; keep the orb visible and the events client running if you like)
- **No made-up live data:** "What's the price of gas in Springfield right now?" Expect: he says he can't look it up (no figure). "Who won the Sharks game last night?" Expect: a tool call, then a real answer.
- **"On it.":** "Research the history of the Springfield Grizzlies in detail." (or any request with a slow tool). Expect "On it." about 1.5 s after you stop, then the answer.
- **Low-confidence confirm:** whisper or mumble "trash the old reports in the sandbox". Expect "I'm not sure I heard that right. Did you say: …?" then say "yes" (runs) or "no" (drops).
- **Cross-agent undo:** "Remember that my sister's name is Dana." then "Forget that." then trash a sandbox file, then say "undo" twice. Expect first "Undid the last file change…", then "Undid your last memory change…".
- **UI end to end:** orb listening while you speak, thinking, then pulsing and scattering with his voice; transcript lines in the bubble; say "go to sleep" (asleep look), "stop listening" (quiet look, still); Ctrl+Option+J wakes; click the orb to shake it; brush it for the flares; stop the service (`julius_service.py off`) and the orb shows "Julius is offline", then `julius_service.py on` and it comes back by itself.

## Post-soak results (automatic run, 2026-11-09 00:15)

**All five steps passed.**

### PASS: Soak report (since the soak start)
```
Julius soak report
1192 samples over 24.02 h (2026-11-08T00:13:42 to 2026-11-09T00:14:45), longest sampler gap 1392s
restarts seen: 0 (launchd run counter moved by 0); samples with the service down: 0
CPU: mean 2.3%, p95 12.1%, max 26.5% of one core
memory: 35.9 to 3382.9 MB (now 158.3), trend -2.07 MB/hour within one run; threads max 60, open files max 21; disk free min 45.3 GB

Julius log scan: 18 lines, 2026-11-08T00:13:14 to 2026-11-08T22:50:09

category                   sev       count  first -> last
service_start              info          1  11-08T00:13:23 -> 11-08T00:13:23

wake detector: 221 fires, 0 accepted, 221 rejected (121.4s of Whisper on rejects)

problems: 0
```

### PASS: Log scan (since the soak start)
```
Julius log scan: 18 lines, 2026-11-08T00:13:14 to 2026-11-08T22:50:09

category                   sev       count  first -> last
service_start              info          1  11-08T00:13:23 -> 11-08T00:13:23

wake detector: 221 fires, 0 accepted, 221 rejected (121.4s of Whisper on rejects)

problems: 0
```

### PASS: Service smoke check with restart
```
ok   service running - state=active pid=96383
ok   julius ready
ok   hotkey +s
ok   hotkey +j
ok   duplex audio started
ok   stable for 20 s - pid 96383->96383, runs 5->5
ok   no traceback at startup
SMOKE PASS
```

### PASS: Event feed check
```
hello received on ws://127.0.0.1:8765: state=asleep, 0 recent lines
```

### PASS: Orb app build
```
331 |             if let path = args.snapshot, let web = self?.web {
```

**Stopped here.** Waiting for the user: the voice script above, the first live orb run (`python julius_orb.py run`), and `python julius_orb.py on`. Nothing of those was started.
