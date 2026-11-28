"""The platform layer: every place Julius depends on macOS goes through here (started 2026-11-28).

    from julius import platforms
    argv = platforms.current().services.restart("com.julius.assistant")

`current()` is the Mac implementation on macOS (`mac.py`, the behaviour Julius always had, argument for argument) and a
placeholder elsewhere whose features say they are unavailable instead of failing at import. The interfaces are in
`base.py`. Each feature hands back the command to run (argv) rather than running it, so callers keep their own runner
(tests inject one) and their own subprocess options.

What goes through it so far, and what doesn't yet: docs/PLATFORM.md.
"""

import sys
from functools import cache

from julius.platforms.base import Platform, PlatformUnsupported

__all__ = ["Platform", "PlatformUnsupported", "current"]


@cache
def current() -> Platform:
    if sys.platform == "darwin":
        from julius.platforms.mac import Mac

        return Mac()
    from julius.platforms.base import Unsupported

    return Unsupported(sys.platform)
