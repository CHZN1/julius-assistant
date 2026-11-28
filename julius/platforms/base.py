"""The interfaces of the platform layer. A new platform implements these; macOS is `mac.py`.

Every method returns an argument list (never a shell string) or a plain value. Running it stays with the caller.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class PlatformUnsupported(RuntimeError):
    """This feature has no implementation on this platform yet."""


class LoginServices(ABC):
    """Per-user background services that start at login (launchd LaunchAgents on macOS)."""

    @abstractmethod
    def domain(self) -> str: ...

    @abstractmethod
    def load(self, definition: Path) -> list[str]:
        """Load the service described by this file."""

    @abstractmethod
    def unload(self, label: str) -> list[str]: ...

    @abstractmethod
    def enable(self, label: str) -> list[str]: ...

    @abstractmethod
    def disable(self, label: str) -> list[str]: ...

    @abstractmethod
    def describe(self, label: str) -> list[str]:
        """Print the service's state; exit status 0 when it is loaded."""

    @abstractmethod
    def signal(self, label: str, signal: str) -> list[str]: ...

    @abstractmethod
    def restart(self, label: str) -> list[str]:
        """Kill the running process and start it again."""


class NativeHelpers(ABC):
    """Small native helper programs Julius compiles on first use (global hotkeys, camera and Vision)."""

    @abstractmethod
    def compile(
        self,
        source: Path,
        binary: Path,
        frameworks: tuple[str, ...] = (),
        objc: bool = False,
        info_plist: Path | None = None,
    ) -> list[str]: ...


class Automation(ABC):
    """Scripting other apps on this computer (Calendar and Reminders through JavaScript for Automation on macOS)."""

    @abstractmethod
    def run_script(self, script: Path) -> list[str]:
        """Run a script that reads its request on stdin and writes JSON on stdout."""


class AudioConverter(ABC):
    """The converter that ships with the OS, used when ffmpeg isn't installed (afconvert on macOS)."""

    name: str = ""

    @abstractmethod
    def available(self) -> bool:
        """Found on the PATH."""

    @abstractmethod
    def path(self) -> str | None:
        """Its full path: the PATH first, then its usual location."""

    @abstractmethod
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
        """Write integer PCM: container "wav" (little-endian) or "aiff" (big-endian)."""


class Speech(ABC):
    """Which local speech engines this platform runs (MLX on Apple silicon)."""

    @abstractmethod
    def tts_worker(self, native: bool) -> str:
        """The module the speech worker process runs: the native engine when `native`, else the portable one."""


class Platform(ABC):
    name: str
    services: LoginServices
    helpers: NativeHelpers
    automation: Automation
    audio: AudioConverter
    speech: Speech


class _Missing:
    """Any feature of an unsupported platform: importing works, calling says what's missing."""

    def __init__(self, platform: str, feature: str) -> None:
        self._platform, self._feature = platform, feature
        self.name = ""

    def available(self) -> bool:
        return False

    def path(self) -> None:
        return None

    def __getattr__(self, attr: str):  # type: ignore[no-untyped-def]
        def missing(*a, **k):  # type: ignore[no-untyped-def]
            raise PlatformUnsupported(
                f"{self._feature} is not available on {self._platform} yet (Julius supports macOS)."
            )

        return missing


class Unsupported(Platform):
    def __init__(self, name: str) -> None:
        self.name = name
        self.services = _Missing(name, "Login services")  # type: ignore[assignment]
        self.helpers = _Missing(name, "Native helpers")  # type: ignore[assignment]
        self.automation = _Missing(name, "App automation")  # type: ignore[assignment]
        self.audio = _Missing(name, "The system audio converter")  # type: ignore[assignment]
        self.speech = _Missing(name, "Local speech")  # type: ignore[assignment]
