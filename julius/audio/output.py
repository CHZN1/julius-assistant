"""Retry device startup only; never replay audio that has already started."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import Mock


@contextmanager
def ready_output(
    factory: Mock | Callable, error_type: type[RuntimeError], sleep: Mock | Callable = time.sleep
) -> Iterator[Mock]:
    for attempt in range(4):
        stream = None
        try:
            stream = factory()
            entered = stream.__enter__()
            break
        except error_type:
            if stream is not None:
                stream.close()
            if attempt == 3:
                raise
            sleep(0.25 * 2**attempt)
    try:
        yield entered
    finally:
        stream.__exit__(None, None, None)
