"""Demo mode: a sandboxed Julius for recording the demo video, run through the real pipeline (real model calls, real cards) on demo data only.

    python scripts/demo_mode.py prepare RUN_DIR [--camera-clip FOLDER] [--main-sprite DIR]   build a fresh sandbox in RUN_DIR
    python scripts/demo_mode.py backend RUN_DIR [--mic]                                      run Julius on it (foreground; --mic: microphone and wake word)

What keeps it apart from the real Julius:
- HOME is RUN_DIR/home, so every ~/ path (state, conversations, research, saved cards, Downloads, Documents, the code agent's folders) is the
  sandbox's. JULIUS_STATE_DIR and JULIUS_MEMORY_DIR point inside it too. Your own files, conversations and memory are never opened.
- Its own ports (feed 18765, UI channel 18775, panel files 18785), so it runs beside the real service without touching it.
- No microphone or speaker (JULIUS_VALIDATION_NO_DEVICES): the demo is typed into the orb. Speech is still made (the light kokoro voice), so the orb
  still shows Julius speaking. No Apple Calendar or Reminders connection, so nothing reaches your real calendar or notifications.
- The camera is a demo clip (JULIUS_DEMO_CAMERA_FILE): "what am I holding" looks at a frame of it instead of the camera.
- Spending goes on the real ledger (JULIUS_COSTS_FILE), so the daily cap still applies.

What is seeded, and why (all of it made up for the demo, except the main sprite):
- Downloads: three demo invoices and two other files, for the move preview and undo.
- Documents/Exports: five badly named PNG exports with different dates, for "rename my exports by date".
- dev/export-tools: an empty project for the coding agent (it works on a scratch copy; nothing merges without a yes).
- Desktop/night_drive_loop.wav: a 140 BPM loop in A minor, made by julius.music.testsignals, for the BPM and key card.
- Tomorrow's calendar: three demo events in the sandbox planner.
- A "Julius" project conversation with the decisions it records, for "what did we decide in the Julius project".
- Julius Saved/Roy's Main Sprite: a copy of the folder you pass with --main-sprite (your pinned main sprite), and the pointer to it.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_HOME = Path.home()
PORTS = {"events": 18765, "ui": 18775, "panel": 18785}
PYTHON = REAL_HOME / ".julius" / "env-service" / "bin" / "python"


def env_for(run: Path) -> dict[str, str]:
    home = run / "home"
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL", "TERM")
    }
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{REAL_HOME}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "JULIUS_STATE_DIR": str(home / ".julius"),
            "JULIUS_MEMORY_DIR": str(home / "julius_memory"),
            "JULIUS_LOG_DIR": str(run / "logs"),
            "JULIUS_LOCAL_CONFIG": str(run / "julius.demo.toml"),
            "JULIUS_ENV_FILE": str(
                ROOT / ".env"
            ),  # the API key, read by Julius itself and never printed
            "JULIUS_COSTS_FILE": str(ROOT / "julius_memory" / "costs.jsonl"),
            "JULIUS_VALIDATION_NO_DEVICES": "1",
            "JULIUS_DEMO": "1",  # keeps the orb's feed and channel on without devices
            "JULIUS_TTS": "kokoro",
            "HF_HOME": str(REAL_HOME / ".cache" / "huggingface"),  # the voice's weights, read only
            "HF_HUB_OFFLINE": "1",  # already downloaded: no network check
            "JULIUS_VISIOND": str(REAL_HOME / ".julius" / "bin" / "julius-visiond"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    clip = run / "camera.jpg"
    if clip.exists():
        env["JULIUS_DEMO_CAMERA_FILE"] = str(clip)
    return env


CONFIG = """# Demo mode (scripts/demo_mode.py): replaces julius.local.toml for the sandboxed demo instance.
[profile]
name = "Roy"

[events]
port = {events}

[ui]
port = {ui}
panel_port = {panel}
saved_dir = "~/Julius Saved"
typed_reply = "both"

[art]
folders = ["~/Julius Art", "~/Pictures", "~/Desktop", "~/Downloads"]

[music]
search_folders = ["~/Downloads", "~/Documents", "~/Desktop"]
index_allowlist = []

[code]
projects_root = "~/dev"

[service]
log_transcripts = false
"""

DECISIONS = [
    "In the Full window the conversation list docks beside the orb like a chat app, and the panel opens on the other side.",
    "The main sprite is a pointer to the saved sprite file, never a copy, and body and clothing work start from it.",
    "Deleting a conversation moves it to Trash; nothing is erased without a second yes.",
    "The demo video is vertical, 9:16, with the floating orb above a detached panel window.",
]


def _seed_conversations(home: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from julius.conversations.store import ConversationStore

    db = ConversationStore(home / ".julius" / "conversations.sqlite3")
    day = 86400
    now = time.time()
    other = db.new("Chord ideas for the new beat", "Music", at=now - 3 * day)
    db.add(other, "user", "Give me a dark progression in A minor.", at=now - 3 * day)
    db.add(
        other,
        "assistant",
        "Am, F, C, G works, or Am, Dm, E for more tension.",
        at=now - 3 * day + 5,
    )
    cid = db.new("Orb and demo decisions", "Julius", at=now - day)
    t = now - day
    for q, a in [
        ("How should conversations look in the Full window?", DECISIONS[0]),
        ("Should the main sprite be a copy?", DECISIONS[1]),
        ("What happens when I delete a conversation?", DECISIONS[2]),
        ("What format is the demo video?", DECISIONS[3]),
    ]:
        db.add(cid, "user", q, at=t)
        db.add(cid, "assistant", "Decided: " + a, at=t + 4)
        t += 60
    pid = db.project_id("Julius")
    for d in DECISIONS:
        db.add_memory("project", pid, "Decided: " + d)
    db.new(
        "Demo", None, at=now
    )  # the fresh demo conversation, with one clean title for the whole run: the newest, so Julius resumes into it
    db.close()


def _seed_calendar(home: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from julius.brain.planning import Planner

    p = Planner(home / ".julius")
    tomorrow = (dt.datetime.now() + dt.timedelta(days=1)).replace(second=0, microsecond=0)
    for title, h, m, mins in [
        ("Studio session with Maya", 11, 0, 120),
        ("Mix review call", 15, 30, 30),
        ("Gym", 18, 0, 60),
    ]:
        start = tomorrow.replace(hour=h, minute=m).timestamp()
        p.add("event", title, start, start + mins * 60)


def _demo_files(home: Path) -> None:
    from PIL import Image, ImageDraw

    dl = home / "Downloads"
    for n in (1042, 1043, 1044):
        im = Image.new("RGB", (612, 792), "white")
        ImageDraw.Draw(im).text(
            (60, 60),
            f"INVOICE #{n}\nDemo Studio LLC\nAmount due: ${120 + n % 7 * 15}.00",
            fill="black",
        )
        im.save(dl / f"Invoice_{n}.pdf")
    Image.new("RGB", (640, 400), (40, 60, 90)).save(dl / "IMG_2041.png")
    (dl / "beat_ideas.txt").write_text("808 slide on the 4\nvocal chop in the bridge\n")
    (home / "Documents" / "Invoices").mkdir(parents=True, exist_ok=True)
    ex = home / "Documents" / "Exports"
    ex.mkdir(parents=True, exist_ok=True)
    base = time.time() - 9 * 86400
    for i, name in enumerate(
        [
            "export.png",
            "export (1).png",
            "Untitled-final.png",
            "Untitled-final (3).png",
            "export copy.png",
        ]
    ):
        p = ex / name
        Image.new("RGB", (320, 200), (30 + 40 * i, 80, 160 - 20 * i)).save(p)
        os.utime(p, (base + i * 1.7 * 86400, base + i * 1.7 * 86400))
    proj = (
        home / "dev" / "export-tools"
    )  # the coding segment's project: the agent works on a scratch copy of it
    (proj / "tests").mkdir(parents=True, exist_ok=True)
    (proj / "README.md").write_text(
        "# export-tools\n\nSmall scripts for my design exports in ~/Documents/Exports.\n"
    )
    (proj / "tests" / "__init__.py").write_text("")
    sys.path.insert(0, str(ROOT))
    from julius.music import testsignals

    testsignals.write(home / "Desktop" / "night_drive_loop.wav", 140, "A minor", seconds=24, seed=7)


def prepare(run: Path, clip: Path | None, main: Path | None) -> None:
    if run.exists() and any(run.iterdir()):
        raise SystemExit(
            f"{run} is not empty: use a new folder for each run (nothing is ever deleted here)"
        )
    home = run / "home"
    for d in [
        ".julius",
        "julius_memory",
        "Downloads",
        "Documents",
        "Desktop",
        "Pictures",
        "Julius Art",
        "Julius Saved",
        "dev",
    ]:
        (home / d).mkdir(parents=True, exist_ok=True)
    (run / "logs").mkdir(parents=True, exist_ok=True)
    (run / "julius.demo.toml").write_text(CONFIG.format(**PORTS))
    _demo_files(home)
    _seed_conversations(home)
    _seed_calendar(home)
    if (
        main is not None
    ):  # a copy of the pinned main sprite's folder (read only from the original) and the pointer to it
        dst = (
            home / "Julius Saved" / "Roy's Main Sprite"
        )  # the copy's folder name is the name Julius says
        shutil.copytree(main, dst)
        spr = next(dst.glob("*.julius-sprite"))
        (home / "Julius Saved" / ".julius-main-sprite.json").write_text(
            json.dumps({"path": f"{dst.name}/{spr.name}", "name": dst.name})
        )
    if clip is not None:
        frames = sorted(clip.glob("*.jpg"))
        if frames:
            shutil.copy(frames[len(frames) // 2], run / "camera.jpg")
    print(f"prepared {run}")


def backend(run: Path, mic: bool = False) -> int:
    """Becomes the backend (exec): stopping this process stops Julius, with no child left running."""
    fd = os.open(run / "logs" / "backend.out", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.chdir(ROOT)
    env = env_for(run)
    if mic:  # the hook take: the real microphone and wake word (your detector model from the repo), speaker on
        env.pop("JULIUS_VALIDATION_NO_DEVICES", None)
        env.pop("JULIUS_DEMO", None)
        cfg = run / "julius.demo.toml"
        if "[wake]" not in cfg.read_text():
            cfg.write_text(
                cfg.read_text()
                + '\n[wake]\nmodel = "julius/wake/models/julius.onnx"\nthreshold_low = 0.05\nlearn = false\n'
            )
    os.execve(str(PYTHON), [str(PYTHON), "-u", "-m", "julius"], env)
    return 1  # not reached


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("run", type=Path)
    p.add_argument("--camera-clip", type=Path)
    p.add_argument("--main-sprite", type=Path)
    b = sub.add_parser("backend")
    b.add_argument("run", type=Path)
    b.add_argument(
        "--mic", action="store_true", help="use the microphone and wake word (the hook take)"
    )
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        prepare(a.run.expanduser().resolve(), a.camera_clip, a.main_sprite)
        return 0
    return backend(a.run.expanduser().resolve(), a.mic)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
