"""Command-line entry point: `python -m julius` starts the assistant with the cloud profile.

Environment overrides (JULIUS_BACKEND, JULIUS_MODEL, JULIUS_TTS, JULIUS_VISION, ...) are honored;
for a local brain use `JULIUS_PROFILE=balanced ./run_julius.sh` or set JULIUS_BACKEND explicitly.
"""

import argparse
import os
import sys

from julius.profile import apply_cloud_defaults


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, install service logging, apply the default profile and run the assistant."""
    argparse.ArgumentParser(
        prog="julius",
        description="Launch Julius with the default cloud profile (same as ./run_julius.sh).",
        epilog="Profiles: ./run_julius.sh (JULIUS_PROFILE=cloud|balanced|fast|...). "
        "Environment variables override the defaults.",
    ).parse_args(argv)
    if os.environ.get("JULIUS_SERVICE") == "1":
        from julius import config, log

        # What he heard and said is logged only while [service] log_transcripts is on.
        log.install(config.transcripts_logging_cached)
    apply_cloud_defaults()  # before the backend module reads its environment at import
    from julius.runtime.app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
