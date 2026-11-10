"""Command-line entry point: `python -m julius` starts the assistant with the cloud profile.

Environment overrides (JULIUS_BACKEND, JULIUS_MODEL, JULIUS_TTS, JULIUS_VISION, ...) are honored;
for a local brain use `JULIUS_PROFILE=balanced ./run_julius.sh` or set JULIUS_BACKEND explicitly.
"""
import argparse
import runpy
import sys

from julius.profile import apply_cloud_defaults


def main(argv=None):
    """Parse arguments, apply the default profile and run the runtime module."""
    argparse.ArgumentParser(
        prog="julius",
        description="Launch Julius with the default cloud profile (same as ./run_julius.sh).",
        epilog="Profiles: ./run_julius.sh (JULIUS_PROFILE=cloud|balanced|fast|...). "
        "Environment variables override the defaults.",
    ).parse_args(argv)
    apply_cloud_defaults()
    runpy.run_module("julius.runtime", run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
