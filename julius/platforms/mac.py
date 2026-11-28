"""macOS: the behaviour Julius has always had. Every command here is the same, argument for argument, as the one each
caller used to build itself (tests/test_platform.py pins each one)."""

import os
import shutil
from pathlib import Path

from julius.platforms import base


class Launchd(base.LoginServices):
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    def load(self, definition: Path) -> list[str]:
        return ["launchctl", "bootstrap", self.domain(), str(definition)]

    def unload(self, label: str) -> list[str]:
        return ["launchctl", "bootout", f"{self.domain()}/{label}"]

    def enable(self, label: str) -> list[str]:
        return ["launchctl", "enable", f"{self.domain()}/{label}"]

    def disable(self, label: str) -> list[str]:
        return ["launchctl", "disable", f"{self.domain()}/{label}"]

    def describe(self, label: str) -> list[str]:
        return ["launchctl", "print", f"{self.domain()}/{label}"]

    def signal(self, label: str, signal: str) -> list[str]:
        return ["launchctl", "kill", signal, f"{self.domain()}/{label}"]

    def restart(self, label: str) -> list[str]:
        return ["launchctl", "kickstart", "-k", f"{self.domain()}/{label}"]


class Clang(base.NativeHelpers):
    def compile(
        self,
        source: Path,
        binary: Path,
        frameworks: tuple[str, ...] = (),
        objc: bool = False,
        info_plist: Path | None = None,
    ) -> list[str]:
        cmd = ["/usr/bin/clang", "-O2"] + (["-fobjc-arc"] if objc else []) + [str(source)]
        for f in frameworks:
            cmd += ["-framework", f]
        if info_plist is not None:  # the camera usage text macOS shows
            cmd += ["-sectcreate", "__TEXT", "__info_plist", str(info_plist)]
        return cmd + ["-o", str(binary)]


class Osascript(base.Automation):
    def run_script(self, script: Path) -> list[str]:
        return ["/usr/bin/osascript", "-l", "JavaScript", str(script)]


class Afconvert(base.AudioConverter):
    name = "afconvert"

    def available(self) -> bool:
        return bool(shutil.which("afconvert"))

    def path(self) -> str | None:
        return shutil.which("afconvert") or (
            "/usr/bin/afconvert" if Path("/usr/bin/afconvert").exists() else None
        )

    def pcm(
        self,
        exe: str,
        src: str,
        dst: str,
        container: str,
        bits: int,
        rate: int | None = None,
        channels: int | None = None,
    ) -> list[str]:
        fileformat = "WAVE" if container == "wav" else "AIFF"
        order = "LE" if container == "wav" else "BE"
        data = f"{order}I{bits}" + (f"@{rate}" if rate else "")
        return (
            [exe, "-f", fileformat, "-d", data]
            + (["-c", str(channels)] if channels else [])
            + [str(src), str(dst)]
        )


class MlxSpeech(base.Speech):
    def tts_worker(self, native: bool) -> str:
        return "julius.audio.mlx_tts_worker" if native else "julius.audio.tts_worker"


class Mac(base.Platform):
    name = "darwin"

    def __init__(self) -> None:
        self.services = Launchd()
        self.helpers = Clang()
        self.automation = Osascript()
        self.audio = Afconvert()
        self.speech = MlxSpeech()
