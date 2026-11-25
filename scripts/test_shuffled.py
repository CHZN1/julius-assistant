"""Run the unit tests with the test modules in a random order, to catch tests that only pass after (or before) another.

    python scripts/test_shuffled.py [SEED]

The seed is printed; run again with it to reproduce an order. Exits 1 on any failure or error. CI runs it once per push
with the run number as the seed, after the normal in-order run."""

import random
import sys
import unittest


def main(argv: list[str]) -> int:
    seed = int(argv[1]) if len(argv) > 1 else random.randrange(1_000_000)
    modules = list(unittest.defaultTestLoader.discover("tests", top_level_dir="."))
    random.Random(seed).shuffle(modules)
    print(f"shuffled test order, seed {seed}", flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(modules))
    if not result.wasSuccessful():
        print(f"failed with seed {seed}: python scripts/test_shuffled.py {seed}", flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
