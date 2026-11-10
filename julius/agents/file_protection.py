"""Which files the agent refuses to touch (music, code projects, system folders, recent work)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from julius.agents.file_common import (
    BUNDLE_EXT,
    CODE_EXT,
    INSTALLER_EXT,
    INSTALLER_SECONDS,
    MUSIC_EXT,
    MUSIC_NAME,
    PROJECT_MARKERS,
    RECENT_SECONDS,
)


class ProtectionMixin:
    """Which files the agent refuses to touch (music, code projects, system folders, recent work)."""

    # ---- trash: recoverable deletion ------------------------------------------------------------------------------
    def protection(self, path: str | Path, origin: str = "human") -> str | None:
        """Why this file must not be moved to the trash, or None. Music, app bundles and project folders are protected for everyone; the planner
        (origin='planner') also never proposes anything touched in the last 7 days, installers used in the last 30 days, or source-code files."""
        p = Path(path)
        ext = p.suffix.casefold()
        base = next((a for a in self.allowed if p.is_relative_to(a)), self.root)
        parts = p.relative_to(base).parts if p.is_relative_to(base) else p.parts
        reason = self._kind_protection(p, ext, parts) or self._folder_protection(p)
        if reason or origin != "planner":
            return reason
        return self._planner_protection(p, ext)

    @staticmethod
    def _kind_protection(
        p: Path, ext: str, parts: tuple[str, str] | tuple[str, str, str] | tuple[str]
    ) -> str | None:
        if p.is_symlink():
            return "it is a link"
        if not p.is_file():
            return "it is a folder, not a loose file"
        if any(part.casefold().endswith(BUNDLE_EXT) for part in parts):
            return "it is part of an app or package bundle"
        if ext in MUSIC_EXT or any(MUSIC_NAME.search(part) for part in parts):
            return "it looks music-related (Logic, GarageBand, plug-ins, samples, stems or audio)"
        return None

    @staticmethod
    def _folder_protection(p: Path) -> str | None:
        try:
            siblings = {e.name for e in os.scandir(p.parent)}
        except OSError:
            return "its folder could not be checked"
        if siblings & PROJECT_MARKERS or any(
            n.endswith((".xcodeproj", ".xcworkspace")) for n in siblings
        ):
            return "its folder contains a code or project file"
        return None

    def _planner_protection(self, p: Path, ext: str) -> str | None:
        """Extra caution for files the planner (not the human) proposes to trash."""
        info = p.stat()
        stamps = [info.st_mtime] + (
            [info.st_birthtime] if self.use_birthtime and hasattr(info, "st_birthtime") else []
        )  # birth time catches a download that kept the server's old modified time
        if time.time() - max(stamps) < RECENT_SECONDS:
            return "it changed in the last 7 days"
        if ext in CODE_EXT:
            return "it is a source-code or project file"
        if ext in INSTALLER_EXT and time.time() - max(stamps + [info.st_atime]) < INSTALLER_SECONDS:
            return "it is an installer that was used in the last 30 days"
        return None
