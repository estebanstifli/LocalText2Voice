from __future__ import annotations

import codecs
import json
import queue
import re
import shlex
import subprocess
import threading
from collections.abc import Callable


INSTALL_DETAIL_PREFIX = "\x1eLTV_INSTALL_DETAIL:"
ProcessOutputCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]

_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1d\x1f]")


def install_detail(message: str) -> str:
    return f"{INSTALL_DETAIL_PREFIX}{message}"


def is_install_detail(message: str) -> bool:
    return str(message).startswith(INSTALL_DETAIL_PREFIX)


def install_detail_text(message: str) -> str:
    value = str(message)
    return value[len(INSTALL_DETAIL_PREFIX) :] if is_install_detail(value) else value


def progress_output_callback(
    progress: ProgressCallback,
    current: int,
    component: str,
) -> ProcessOutputCallback:
    """Adapt live process output to a detail-only installation progress event."""

    def emit(source: str, line: str) -> None:
        text = readable_process_line(line)
        if not text:
            return
        progress(
            current,
            100,
            install_detail(f"[{component}/{source}] {text}"),
        )

    return emit


def detailed_pip_args(args: list[str]) -> list[str]:
    """Enable verbose package and download progress for pip install commands."""

    detailed = list(args)
    if detailed and detailed[0] == "install":
        detailed[1:1] = ["--verbose", "--progress-bar", "on"]
    return detailed


def report_process_command(
    progress: ProgressCallback,
    current: int,
    component: str,
    args: list[str],
) -> None:
    progress(
        current,
        100,
        install_detail(f"[{component}] Command: {shlex.join(args)}"),
    )


def run_python_with_live_output(
    runtime,
    args: list[str],
    cancel_token,
    output_callback: ProcessOutputCallback | None,
) -> str:
    """Use live output when supported while preserving runtime test doubles."""

    try:
        return runtime.run_python(
            args,
            cancel_token,
            output_callback=output_callback,
        )
    except TypeError as exc:
        if "output_callback" not in str(exc):
            raise
        return runtime.run_python(args, cancel_token)


def readable_process_line(line: str) -> str:
    """Remove terminal controls and make structured worker events readable."""

    value = _ANSI_ESCAPE.sub("", str(line)).replace("\b", "")
    value = _CONTROL_CHARACTERS.sub("", value).strip()
    if not value:
        return ""
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(payload, dict):
        return value
    event_type = str(payload.get("type", "")).strip()
    message = str(payload.get("message", "")).strip()
    if message:
        return f"{event_type}: {message}" if event_type else message
    if event_type == "timing":
        label = str(payload.get("label", "operation")).strip()
        try:
            elapsed = float(payload.get("elapsed", 0))
        except (TypeError, ValueError):
            elapsed = 0.0
        return f"{label}: {elapsed:.2f} s"
    return value


def communicate_with_live_output(
    process: subprocess.Popen[bytes],
    check_cancelled: Callable[[], None],
    output_callback: ProcessOutputCallback | None = None,
) -> tuple[bytes, bytes]:
    """Read stdout/stderr concurrently while forwarding newline or CR updates."""

    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    captured = {"stdout": bytearray(), "stderr": bytearray()}

    def read_stream(source: str, stream) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        try:
            read_chunk = getattr(stream, "read1", stream.read)
            while True:
                chunk = read_chunk(4096)
                if not chunk:
                    break
                captured[source].extend(chunk)
                pending += decoder.decode(chunk)
                parts = re.split(r"[\r\n]+", pending)
                pending = parts.pop()
                for part in parts:
                    events.put((source, part))
            pending += decoder.decode(b"", final=True)
            if pending:
                events.put((source, pending))
        finally:
            events.put((source, None))

    threads = [
        threading.Thread(
            target=read_stream,
            args=(source, stream),
            daemon=True,
            name=f"ltv-install-{source}",
        )
        for source, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        )
        if stream is not None
    ]
    for thread in threads:
        thread.start()

    completed_streams = 0
    while completed_streams < len(threads):
        check_cancelled()
        try:
            source, line = events.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            completed_streams += 1
            continue
        if output_callback is not None:
            try:
                output_callback(source, line)
            except Exception:
                # Progress reporting must never break an installation process.
                pass

    for thread in threads:
        thread.join(timeout=1)
    process.wait()
    return bytes(captured["stdout"]), bytes(captured["stderr"])
