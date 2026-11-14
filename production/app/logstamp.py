"""Timestamp every line the service writes to stdout/stderr.

NSSM captures the backend's stdout/stderr into logs\\service.out.log /
service.err.log, but most print() lines carried no time, so they could not be
lined up with the network watchdog, UptimeRobot or the AnyDesk trace during an
outage. install() wraps both streams so each line starts with a WIB stamp:

    [2026-11-14 14:03:07] Backend -> Node 1C:DB:D4:C4:66:14: restore:normal

Lines that already start with "[20" (the MQTT connection lines stamp
themselves) are left alone, so nothing is double-stamped.

Installed only by the service entry points (run.py / python -m app) — tests
and scripts that build the app with create_app() keep their plain output.
"""

import sys

from app import config


class _StampedStream:
    def __init__(self, stream):
        self._stream = stream
        self._at_line_start = True

    def write(self, text):
        # Behave exactly like a text stream: bytes are a TypeError. (Libraries
        # such as click probe with write(b"") to decide whether a stream is
        # binary; accepting it made them send bytes here.)
        if not isinstance(text, str):
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")
        if not text:
            return 0
        out = []
        for chunk in text.splitlines(keepends=True):
            if self._at_line_start and chunk.strip() and not chunk.startswith("[20"):
                out.append(config.now_wib().strftime("[%Y-%m-%d %H:%M:%S] "))
            out.append(chunk)
            self._at_line_start = chunk.endswith("\n")
        self._stream.write("".join(out))
        return len(text)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        # isatty(), encoding, fileno(), ... pass through to the real stream.
        return getattr(self._stream, name)


def install():
    """Wrap sys.stdout and sys.stderr once (idempotent)."""
    if not isinstance(sys.stdout, _StampedStream) and sys.stdout is not None:
        sys.stdout = _StampedStream(sys.stdout)
    if not isinstance(sys.stderr, _StampedStream) and sys.stderr is not None:
        sys.stderr = _StampedStream(sys.stderr)
