"""Retry device startup only; never replay audio that has already started."""

import time
from contextlib import contextmanager


@contextmanager
def ready_output(factory, error_type, sleep=time.sleep):
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
