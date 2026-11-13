"""Live check of "look at things" against the RUNNING Julius service (restart it first so it runs the current code).

    python scripts/vision_live.py            # screen, then a dropped image, then the camera; Enter to continue between steps
    python scripts/vision_live.py --only screen,image,camera

Each step sends one typed request to the service (exactly what typing it in the orb does) and prints Julius's reply from the service log.
What to expect, and what to approve:
  screen   "what's on my screen" looks at the frontmost window that is not Julius's own. The first time, macOS asks for Screen Recording
           permission for the service: allow it (System Settings > Privacy & Security > Screen & System Audio Recording), then restart the
           service and run this step again.
  image    makes a small test picture in your Downloads folder, drops it on the orb's channel and asks "look at this image". The script
           asks before removing the picture.
  camera   "what am I holding" takes ONE frame. The first time, macOS asks for Camera permission: allow it. The green camera indicator
           lights only while the picture is taken. Hold something up first.
In the orb's side panel each look appears as a card with the picture, the answer and its cost. Say "save this card" to keep one; otherwise
nothing is written to disk (the check at the end confirms the panel store stayed empty of look pictures)."""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
LOG = HOME / "Library" / "Logs" / "Julius" / "launchd.out"
BASE = "http://127.0.0.1:8775"


def token() -> str:
    return (HOME / ".julius" / "ui_token").read_text().strip()


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


def say(text: str, wait: float = 60.0) -> str:
    """Type `text` to the service and return what it logged for the turn."""
    start = LOG.stat().st_size
    post("/v1/typed", {"text": text})
    end = time.time() + wait
    out = ""
    while time.time() < end:
        time.sleep(1.0)
        with LOG.open("rb") as f:
            f.seek(start)
            out = f.read().decode("utf-8", "replace")
        if "[timing:" in out:
            break
    lines = [ln.strip() for ln in out.splitlines() if "JULIUS:" in ln or "vision" in ln.lower()]
    return "\n".join(lines) or out[-600:] or "(no reply logged)"


def sample_image() -> Path:
    from PIL import Image, ImageDraw, ImageFont

    path = HOME / "Downloads" / "julius-vision-test.png"
    im = Image.new("RGB", (900, 500), "#fff7e0")
    d = ImageDraw.Draw(im)
    d.ellipse((80, 120, 380, 420), fill="#d62828")
    d.text((440, 150), "Julius vision test", fill="#222", font=ImageFont.load_default(size=52))
    d.text((440, 250), "red circle, 3:45 PM", fill="#555", font=ImageFont.load_default(size=40))
    im.save(path)
    return path


def step_screen() -> None:
    print("\n== SCREEN ==  Put something recognisable in the frontmost window (not Julius).")
    input("Press Enter to ask 'what's on my screen'... ")
    print(say("what's on my screen"))


def step_image() -> None:
    print("\n== DROPPED IMAGE ==")
    p = sample_image()
    print("made", p)
    print("drop result:", post("/v1/drop", {"paths": [str(p)]}))
    time.sleep(3)
    print(say("look at this image"))
    if input(f"Remove {p}? [y/N] ").strip().lower() == "y":
        p.unlink(missing_ok=True)
        print("removed")


def step_camera() -> None:
    print(
        "\n== CAMERA ==  Hold something up to the camera. macOS may ask for Camera permission: allow it."
    )
    input("Press Enter to ask 'what am I holding'... ")
    print(say("what am I holding", wait=90))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default="screen,image,camera")
    a = ap.parse_args()
    panel = HOME / ".julius" / "panel"
    before = {p.name for p in panel.glob("*")} if panel.exists() else set()
    steps = {"screen": step_screen, "image": step_image, "camera": step_camera}
    for name in a.only.split(","):
        steps[name.strip()]()
    new = sorted({p.name for p in panel.glob("*")} - before) if panel.exists() else []
    print(
        f"\nDone. New files in {panel} after these looks: {len(new)} (expected 0: look pictures are held in memory only)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
