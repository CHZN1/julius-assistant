"""Optional perception: disabled mode imports and loads no vision dependencies. Julius ships no implementation; an enabled install names a module (JULIUS_VISION_MODULE, default julius_perception) that provides Perception and describe."""

import importlib
import os
from typing import Any


class VisionMode:
    def __init__(self, enabled: bool = False, **options) -> None:
        self.enabled = enabled
        self._perception = None
        self._describe = None
        if enabled:
            module = importlib.import_module(
                os.environ.get("JULIUS_VISION_MODULE", "julius_perception")
            )
            Perception, describe = module.Perception, module.describe
            self._perception = Perception(**options)
            self._describe = describe

    def start(self) -> Any:
        if self._perception is not None:
            self._perception.start()
        return self

    def snapshot(self) -> Any:
        if self._perception is not None:
            return self._perception.snapshot()
        return dict(
            person=None, id_conf=0.0, emotion=None, conf=0.0, held=0.0, present=0.0, seen=False
        )

    def describe(self, state: Any) -> Any:
        return self._describe(state) if self._describe is not None else ""

    def check_nudge(self) -> Any:
        return self._perception.check_nudge() if self._perception is not None else None

    def stop(self) -> None:
        if self._perception is not None:
            self._perception.stop()


def create_vision(**options) -> Any:
    return VisionMode(
        enabled=os.environ.get("JULIUS_VISION", "0").lower() in {"1", "true", "on"}, **options
    )


def vision_capability_prompt(enabled: bool) -> str:
    if enabled:
        return (
            "\nVISION: Enabled. Only describe what the supplied camera observations actually show."
        )
    return "\nVISION: Disabled. You cannot see the user, recognize faces, or observe facial expressions. Do not claim you can. Respond to what he actually says or types; do not treat profanity, tone, or wording as proof of an emotion."
