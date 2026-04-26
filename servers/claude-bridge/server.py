"""MCP bridge for Claude Code CLI.

The synchronous ask_claude tool remains available for short requests. Long
requests should use the job API:

1. submit_claude_job(...)
2. poll get_claude_job(...)
3. optionally cancel_claude_job(...)

Large prompts can be uploaded through bridge_attach_prompt. All bridge-owned
state is stored under the bridge state directory, not inside user repositories.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.stdout is None or sys.stderr is None:
    _log_path = Path(__file__).resolve().parent / "bridge.log"
    _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file

from mcp.server.fastmcp import FastMCP


BRIDGE_DIR = Path(__file__).resolve().parent


def _resolve_claude_exe() -> str:
    env = os.environ.get("CLAUDE_EXE")
    if env and Path(env).is_file():
        return env

    parent = Path.home() / "AppData" / "Roaming" / "Claude" / "claude-code"
    if parent.is_dir():
        candidates: list[tuple[tuple[int, ...], Path]] = []
        for sub in parent.iterdir():
            if not sub.is_dir():
                continue
            exe = sub / "claude.exe"
            if not exe.is_file():
                continue
            version_tuple = tuple(int(p) if p.isdigit() else 0 for p in sub.name.split("."))
            candidates.append((version_tuple, exe))
        if candidates:
            candidates.sort()
            return str(candidates[-1][1])

    return env or str(parent / "claude.exe")


CLAUDE_EXE = _resolve_claude_exe()
print(f"[claude-bridge] CLAUDE_EXE resolved to: {CLAUDE_EXE}", file=sys.stderr)

_raw_dirs = os.environ.get("CLAUDE_BRIDGE_ALLOWED_DIRS", r"E:\TwStockAdvisor")
ALLOWED_ROOTS: list[Path] = [Path(d).resolve() for d in _raw_dirs.split(";") if d.strip()]
DEFAULT_CWD = ALLOWED_ROOTS[0] if ALLOWED_ROOTS else Path.cwd()
TIMEOUT_SECS = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "600"))
MAX_READ_BYTES = int(os.environ.get("CLAUDE_BRIDGE_MAX_READ", "200000"))
MAX_PROMPT_BYTES = int(os.environ.get("CLAUDE_BRIDGE_MAX_PROMPT", str(10 * 1024 * 1024)))
MAX_CHUNK_BYTES = int(os.environ.get("CLAUDE_BRIDGE_MAX_CHUNK", "50000"))
MAX_CONCURRENT = int(os.environ.get("CLAUDE_BRIDGE_MAX_CONCURRENT", "2"))
STATE_DIR = Path(os.environ.get("CLAUDE_BRIDGE_STATE_DIR", str(BRIDGE_DIR / "state"))).resolve()
JOBS_DIR = STATE_DIR / "jobs"
ATTACHMENTS_DIR = STATE_DIR / "attachments"
HOST = os.environ.get("CLAUDE_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAUDE_BRIDGE_PORT", "8000"))

JOBS_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("claude-bridge", host=HOST, port=PORT)
_active_processes: dict[str, subprocess.Popen[str]] = {}
_active_lock = threading.Lock()
_semaphore = threading.Semaphore(MAX_CONCURRENT)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _check_allowed(p: Path) -> Path | None:
    try:
        abs_p = p.resolve()
    except OSError:
        return None
    for root in ALLOWED_ROOTS:
        try:
            abs_p.relative_to(root)
            return abs_p
        except ValueError:
            continue
    return None


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _meta_path(job_id: str) -> Path:
    return _job_dir(job_id) / "meta.json"


def _read_meta(job_id: str) -> dict[str, Any] | None:
    path = _meta_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(job_id: str, meta: dict[str, Any]) -> None:
    path = _meta_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(meta), encoding="utf-8")


def _read_chunk(path: Path, cursor: int, max_bytes: int) -> tuple[str, int, bool]:
    max_bytes = max(1, min(max_bytes, MAX_CHUNK_BYTES))
    if not path.is_file():
        return "", cursor, False
    size = path.stat().st_size
    cursor = max(0, min(cursor, size))
    with path.open("rb") as f:
        f.seek(cursor)
        data = f.read(max_bytes)
        next_cursor = f.tell()
    return data.decode("utf-8", errors="replace"), next_cursor, next_cursor < size


def _claude_cmd(prompt: str, resume_last: bool) -> list[str]:
    cmd = [
        CLAUDE_EXE,
        "-p",
        prompt,
        "--model",
        "claude-opus-4-7",
        "--output-format",
        "text",
    ]
    if resume_last:
        cmd.append("--continue")
    return cmd


@mcp.tool()
def ask_claude(prompt: str, working_dir: str | None = None, resume_last: bool = False) -> str:
    """Synchronously ask Claude Code. Use submit_claude_job for long requests."""
    cwd = Path(working_dir) if working_dir else DEFAULT_CWD
    safe = _check_allowed(cwd)
    if safe is None:
        return f"working_dir is outside allowed roots: {cwd}"
    if not safe.is_dir():
        return f"working_dir is not a directory: {safe}"
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        return f"prompt is too large: max {MAX_PROMPT_BYTES} bytes"

    cmd = _claude_cmd(prompt, resume_last)
    try:
        result = subprocess.run(
            cmd,
            cwd=safe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return f"Claude timed out after {TIMEOUT_SECS} seconds. Use submit_claude_job for long work."
    except FileNotFoundError:
        return f"Claude executable not found: {CLAUDE_EXE}"

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return (
            f"Claude failed (exit {result.returncode})\n"
            f"command: {shlex.join(cmd)}\n"
            f"stderr: {err}\n\n"
            f"stdout: {output}"
        )
    return output or "(Claude produced no output)"


@mcp.tool()
def bridge_attach_prompt(
    content: str,
    attachment_id: str | None = None,
    append: bool = False,
) -> str:
    """Upload prompt content to bridge-owned storage and return attachment_id."""
    data = content.encode("utf-8", errors="replace")
    if len(data) > MAX_PROMPT_BYTES:
        return _json({"error": f"content is too large: max {MAX_PROMPT_BYTES} bytes"})

    if attachment_id:
        safe_id = Path(attachment_id).name
    else:
        safe_id = uuid.uuid4().hex
    path = ATTACHMENTS_DIR / f"{safe_id}.md"
    mode = "ab" if append else "wb"
    with path.open(mode) as f:
        f.write(data)

    return _json(
        {
            "attachment_id": safe_id,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )


@mcp.tool()
def submit_claude_job(
    prompt: str = "",
    prompt_attachment: str | None = None,
    working_dir: str | None = None,
    resume_last: bool = False,
    timeout_secs: int | None = None,
) -> str:
    """Submit a Claude Code job and return immediately with job_id."""
    cwd = Path(working_dir) if working_dir else DEFAULT_CWD
    safe = _check_allowed(cwd)
    if safe is None:
        return _json({"error": f"working_dir is outside allowed roots: {cwd}"})
    if not safe.is_dir():
        return _json({"error": f"working_dir is not a directory: {safe}"})

    prompt_parts: list[str] = []
    if prompt:
        prompt_parts.append(prompt)

    attachment_path: Path | None = None
    if prompt_attachment:
        safe_id = Path(prompt_attachment).name
        attachment_path = ATTACHMENTS_DIR / f"{safe_id}.md"
        if not attachment_path.is_file():
            return _json({"error": f"prompt attachment not found: {prompt_attachment}"})
        prompt_parts.append(attachment_path.read_text(encoding="utf-8", errors="replace"))

    final_prompt = "\n\n".join(prompt_parts).strip()
    prompt_bytes = final_prompt.encode("utf-8", errors="replace")
    if not final_prompt:
        return _json({"error": "prompt or prompt_attachment is required"})
    if len(prompt_bytes) > MAX_PROMPT_BYTES:
        return _json({"error": f"prompt is too large: max {MAX_PROMPT_BYTES} bytes"})

    job_id = uuid.uuid4().hex
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=False)
    (job_dir / "prompt.md").write_text(final_prompt, encoding="utf-8")
    (job_dir / "stdout.log").write_text("", encoding="utf-8")
    (job_dir / "stderr.log").write_text("", encoding="utf-8")

    if attachment_path is not None:
        try:
            attachment_path.unlink()
        except OSError:
            pass

    effective_timeout = timeout_secs if timeout_secs and timeout_secs > 0 else TIMEOUT_SECS
    meta = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "ended_at": None,
        "working_dir": str(safe),
        "resume_last": resume_last,
        "timeout_secs": effective_timeout,
        "prompt_bytes": len(prompt_bytes),
        "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "pid": None,
        "exit_code": None,
        "error": None,
    }
    _write_meta(job_id, meta)

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return _json({"job_id": job_id, "status": "queued"})


def _run_job(job_id: str) -> None:
    acquired = _semaphore.acquire(blocking=False)
    meta = _read_meta(job_id)
    if meta is None:
        return
    if not acquired:
        meta["status"] = "rejected"
        meta["ended_at"] = _now()
        meta["error"] = f"max_concurrent reached: {MAX_CONCURRENT}"
        _write_meta(job_id, meta)
        return

    process: subprocess.Popen[str] | None = None
    try:
        job_dir = _job_dir(job_id)
        prompt = (job_dir / "prompt.md").read_text(encoding="utf-8")
        cmd = _claude_cmd(prompt, bool(meta["resume_last"]))
        meta["status"] = "running"
        meta["started_at"] = _now()
        _write_meta(job_id, meta)

        with (job_dir / "stdout.log").open("w", encoding="utf-8", errors="replace") as out, (
            job_dir / "stderr.log"
        ).open("w", encoding="utf-8", errors="replace") as err:
            process = subprocess.Popen(
                cmd,
                cwd=str(meta["working_dir"]),
                stdout=out,
                stderr=err,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            with _active_lock:
                _active_processes[job_id] = process
            meta["pid"] = process.pid
            _write_meta(job_id, meta)
            try:
                exit_code = process.wait(timeout=int(meta["timeout_secs"]))
                meta["exit_code"] = exit_code
                meta["status"] = "succeeded" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired:
                process.kill()
                meta["status"] = "timeout"
                meta["error"] = f"timed out after {meta['timeout_secs']} seconds"
    except FileNotFoundError:
        meta["status"] = "error"
        meta["error"] = f"Claude executable not found: {CLAUDE_EXE}"
    except Exception as e:  # noqa: BLE001 - keep bridge alive and record job failure.
        meta["status"] = "error"
        meta["error"] = str(e)
    finally:
        with _active_lock:
            _active_processes.pop(job_id, None)
        meta["ended_at"] = _now()
        _write_meta(job_id, meta)
        _semaphore.release()


@mcp.tool()
def get_claude_job(
    job_id: str,
    wait_ms: int = 0,
    stdout_cursor: int = 0,
    stderr_cursor: int = 0,
    max_bytes: int = 20000,
) -> str:
    """Read job status and output chunks. wait_ms supports long polling."""
    wait_ms = max(0, min(wait_ms, 90000))
    deadline = time.time() + (wait_ms / 1000)
    while True:
        meta = _read_meta(job_id)
        if meta is None:
            return _json({"error": "job not found", "job_id": job_id})
        if meta["status"] not in ("queued", "running") or wait_ms == 0 or time.time() >= deadline:
            break
        time.sleep(0.25)

    job_dir = _job_dir(job_id)
    stdout, stdout_next, stdout_more = _read_chunk(job_dir / "stdout.log", stdout_cursor, max_bytes)
    stderr, stderr_next, stderr_more = _read_chunk(job_dir / "stderr.log", stderr_cursor, max_bytes)
    done = meta["status"] not in ("queued", "running")
    return _json(
        {
            "job_id": job_id,
            "status": meta["status"],
            "done": done,
            "exit_code": meta.get("exit_code"),
            "error": meta.get("error"),
            "stdout_chunk": stdout,
            "stderr_chunk": stderr,
            "stdout_next_cursor": stdout_next,
            "stderr_next_cursor": stderr_next,
            "stdout_more": stdout_more,
            "stderr_more": stderr_more,
            "meta": meta,
        }
    )


@mcp.tool()
def cancel_claude_job(job_id: str) -> str:
    """Cancel a running Claude job."""
    meta = _read_meta(job_id)
    if meta is None:
        return _json({"job_id": job_id, "status": "not_found"})

    with _active_lock:
        process = _active_processes.get(job_id)
    if process and process.poll() is None:
        process.kill()
        meta["status"] = "cancelled"
        meta["ended_at"] = _now()
        meta["error"] = "cancelled by user"
        _write_meta(job_id, meta)
        return _json({"job_id": job_id, "status": "cancelled"})

    return _json({"job_id": job_id, "status": meta["status"]})


@mcp.tool()
def list_claude_jobs(status: str | None = None) -> str:
    """List known Claude jobs."""
    rows: list[dict[str, Any]] = []
    for meta_file in sorted(JOBS_DIR.glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if status and meta.get("status") != status:
            continue
        rows.append(
            {
                "job_id": meta.get("job_id"),
                "status": meta.get("status"),
                "created_at": meta.get("created_at"),
                "started_at": meta.get("started_at"),
                "ended_at": meta.get("ended_at"),
                "working_dir": meta.get("working_dir"),
                "prompt_bytes": meta.get("prompt_bytes"),
                "pid": meta.get("pid"),
            }
        )
    return _json(rows[:100])


@mcp.tool()
def read_file(path: str, max_bytes: int | None = None) -> str:
    """Read a whitelisted file without launching Claude."""
    target = _check_allowed(Path(path))
    if target is None:
        return f"path is outside allowed roots: {path}"
    if not target.is_file():
        return f"not a file or does not exist: {target}"

    limit = max_bytes if (max_bytes and max_bytes > 0) else MAX_READ_BYTES
    size = target.stat().st_size
    try:
        data = target.read_bytes()[:limit]
    except OSError as e:
        return f"read failed: {e}"
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text += f"\n\n(truncated: file is {size} bytes, showing first {limit})"
    return text


@mcp.tool()
def list_dir(path: str | None = None) -> str:
    """List a whitelisted directory."""
    raw = Path(path) if path else DEFAULT_CWD
    target = _check_allowed(raw)
    if target is None:
        return f"path is outside allowed roots: {raw}"
    if not target.is_dir():
        return f"not a directory or does not exist: {target}"

    rows: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        kind = "DIR " if entry.is_dir() else "FILE"
        try:
            size = entry.stat().st_size if entry.is_file() else "-"
        except OSError:
            size = "?"
        rows.append(f"{kind}  {entry.name}  ({size})")
    return "\n".join(rows) or "(empty directory)"


@mcp.tool()
def claude_version() -> str:
    """Return Claude Code version and bridge settings."""
    try:
        result = subprocess.run(
            [CLAUDE_EXE, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        ver = (result.stdout or result.stderr).strip()
    except FileNotFoundError:
        ver = f"Claude executable not found: {CLAUDE_EXE}"
    roots = [str(r) for r in ALLOWED_ROOTS]
    return (
        f"{ver}\n"
        f"allowed_roots: {roots}\n"
        f"default_cwd: {DEFAULT_CWD}\n"
        f"state_dir: {STATE_DIR}\n"
        f"max_concurrent: {MAX_CONCURRENT}"
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
