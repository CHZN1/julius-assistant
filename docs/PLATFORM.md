# Platform layer

Julius runs on macOS today. Everything that depends on macOS is moving behind one layer, `julius/platforms/`, so a
second platform can be added by writing one implementation rather than editing code all over the tree. The move is
being done in slices, and each slice keeps the Mac behaviour exactly as it was.

```python
from julius import platforms

argv = platforms.current().services.restart("com.julius.assistant")
subprocess.run(argv, capture_output=True, text=True)
```

- `base.py` holds the interfaces. Each feature returns the command to run (an argument list, never a shell string) or
  a plain value, and running it stays with the caller. Callers therefore keep their own runner, which the tests inject,
  and their own subprocess options.
- `mac.py` is the macOS implementation.
- On any other OS, `current()` returns a placeholder:
  - importing still works;
  - `available()` answers False;
  - anything else raises `PlatformUnsupported` naming the feature.

## Behind the layer (slice 1, 2026-11-28)

| Interface | macOS | Used by |
|---|---|---|
| `services` (login services) | `launchctl` bootstrap, bootout, enable, disable, print, kill, kickstart | `service/service.py`, `service/soak.py`, `service/postsoak.py` |
| `helpers` (compiling the native helpers) | `/usr/bin/clang` with `-framework …` | `service/hotkey.py` (Carbon hotkeys), `agents/vision.py` (camera and Vision helper) |
| `automation` (scripting other apps) | `/usr/bin/osascript -l JavaScript` | `brain/apple.py` (Calendar and Reminders) |
| `audio` (the OS's own converter, when ffmpeg is missing) | `afconvert` | `music/convert.py`, `music/analysis.py` |
| `speech` (which speech worker runs) | the MLX worker, or the portable one | `audio/tts.py` |

How this is checked:
- `tests/test_platform.py` pins every Mac command, argument for argument, copied from the callers before the move.
- Its `Boundary` test fails if `launchctl`, `afconvert`, `/usr/bin/osascript`, `/usr/bin/clang` or `-framework` shows
  up as a string anywhere in `julius/` outside the layer.
- Before the commit, the old modules (from git) and the new ones were run side by side with recording runners. All 73
  traced commands and results were identical: every convert format, rate and depth, plus the service, soak, hotkey,
  Vision, Apple and analysis paths.

## Not behind it yet (next slices)

- **MLX speech and models.** `runtime/asr.py`, `wake/stream.py`, `audio/asr_resources.py`, `brain/backend.py`,
  `audio/mlx_tts_worker.py` and `audio/mlx_voice_support.py` import `mlx` directly. They need a speech-engine
  interface (load, transcribe, synthesize), not just a command.
- **Other Mac commands:**
  - `/usr/bin/sandbox-exec` (`agents/code_agent.py`): the seatbelt profile. Another OS needs its own sandbox, and
    the escape tests come with it.
  - `/usr/bin/security` (`keys.py`): Keychain.
  - `/usr/sbin/screencapture` (`agents/vision.py`).
  - `diskutil` (`music/library.py`).
  - `open` (`ui_bridge/panel_emit.py`).
- **The helper sources themselves:** `hotkeyd/hotkeyd.c` (Carbon) and `visiond/visiond.m` (AVFoundation and
  Vision). Another platform needs its own helpers that speak the same stdin/stdout protocol.
- **The orb app's Swift shell bridges** (in the orb app repository).
- **Paths:** `~/Library/LaunchAgents` and `~/Library/Logs/Julius` are still written in the service modules.
