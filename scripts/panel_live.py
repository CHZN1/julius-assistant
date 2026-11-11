"""Run the real research agent with the side panel's feed, without the microphone, the voice or the rest of the service.

    python scripts/panel_live.py                          # type a question, see its card in the orb app; also type: open source 2, save this card, close the panel
    python scripts/panel_live.py --ask "How tall is ...?"  # one question, then keep serving the card until Ctrl-C

It starts the event feed on ws://127.0.0.1:8766 and the panel's image server on 8786 (not the service's 8765 and 8785, so a running Julius is not disturbed) and keeps its own image
cache in ~/.julius/panel-live. Research uses Haiku with the [research] caps from julius.toml (at most max_usd = 0.10 per question) and needs ANTHROPIC_API_KEY (environment or .env).
Nothing is saved to ~/.julius/research. "save this card" writes to [ui] saved_dir unless --saved-dir is given."""

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from julius import config  # noqa: E402
from julius.agents import research  # noqa: E402
from julius.brain import cloud  # noqa: E402
from julius.ui_bridge import events, panel_emit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ask", help="one question to research now")
    ap.add_argument("--feed-port", type=int, default=8766)
    ap.add_argument("--files-port", type=int, default=8786)
    ap.add_argument("--saved-dir", help="where 'save this card' writes (default: [ui] saved_dir)")
    args = ap.parse_args()

    hub, server = events.start_from_config({"enabled": True, "port": args.feed_port})
    if hub is None:
        print(f"Could not open the feed on port {args.feed_port}.")
        return 1
    ui = {"panel": True, "panel_port": args.files_port, "panel_max_mb": 50}
    started = panel_emit.start_panel(hub, ui, Path.home() / ".julius" / "panel-live")
    if started is None:
        return 1
    panel, files, fserver = started
    research.set_panel_sink(panel_emit.make_sink(panel, files))
    brain = cloud.get_brain()
    brain.config.setdefault("research", {})["save"] = False
    saved = args.saved_dir or config.load_config()["ui"]["saved_dir"]
    print(
        f"Feed ws://127.0.0.1:{server.port}  |  images http://127.0.0.1:{fserver.port}  |  start the orb with --feed ws://127.0.0.1:{server.port}"
    )

    def ask(q: str) -> None:
        t0 = time.time()
        text = research.research_task({"question": q}, brain)
        print(f"\nJULIUS: {text}\n  [{time.time() - t0:.1f} s]", flush=True)

    try:
        if args.ask:
            ask(args.ask)
            time.sleep(6)  # let the images arrive
            print(
                "Serving the card. Type a command (open source N, save this card, close the panel) or Ctrl-C."
            )
        else:
            print("Type a question to research, or a panel command. Ctrl-C to stop.")
        while True:
            line = input("> ").strip()
            if not line:
                continue
            reply = panel_emit.handle(panel, files, line, saved)
            if reply is not None:
                print(f"JULIUS: {reply}")
            else:
                threading.Thread(target=ask, args=(line,), daemon=True).start()
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        fserver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
