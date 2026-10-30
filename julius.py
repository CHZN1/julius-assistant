"""Launch Julius with the default cloud profile (same as `./run_julius.sh`).

Usage: python julius.py [-h]
Environment overrides (JULIUS_BACKEND, JULIUS_MODEL, JULIUS_TTS, JULIUS_VISION, ...) are honored;
for a local brain use `JULIUS_PROFILE=balanced ./run_julius.sh` or set JULIUS_BACKEND explicitly."""
import argparse, runpy, sys
from julius_profile import apply_cloud_defaults

def main(argv=None):
    argparse.ArgumentParser(prog="julius.py", description=__doc__.split("\n\n")[0],
                            epilog="Profiles: ./run_julius.sh (JULIUS_PROFILE=cloud|balanced|fast|...). Environment variables override the defaults.").parse_args(argv)
    apply_cloud_defaults()
    runpy.run_module("julius_candidate", run_name="__main__")

if __name__ == "__main__":
    main(sys.argv[1:])
