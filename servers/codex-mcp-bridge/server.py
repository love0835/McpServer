"""Conservative MCP bridge for a portable local Codex CLI install."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.stdout is None or sys.stderr is None:
    _log_path = Path(__file__).resolve().parent / "logs" / "bridge.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file

from mcp.server.fastmcp import FastMCP


BRIDGE_DIR = Path(__file__).resolve().parent
BRIDGE_TEMP_DIR = BRIDGE_DIR / "temp"
BRIDGE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

NODE_EXE = os.environ.get("CODEX_NODE_EXE", "")
CODEX_JS_ENTRY = os.environ.get("CODEX_JS_ENTRY", "")
HOST = os.environ.get("CODEX_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODEX_BRIDGE_PORT", "8001"))
TIMEOUT_SECS = int(os.environ.get("CODEX_BRIDGE_TIMEOUT", "300"))
ENABLE_ASK = os.environ.get("CODEX_BRIDGE_ENABLE_ASK", "0") == "1"
MAX_DEPTH = int(os.environ.get("CODEX_BRIDGE_MAX_DEPTH", "1"))
MAX_READ_BYTES = int(os.environ.get("CODEX_BRIDGE_MAX_READ", "200000"))
MAX_PROMPT_BYTES = int(os.environ.get("CODEX_BRIDGE_MAX_PROMPT", str(10 * 1024 * 1024)))
MAX_CHUNK_BYTES = int(os.environ.get("CODEX_BRIDGE_MAX_CHUNK", "50000"))
MAX_CONCURRENT = int(os.environ.get("CODEX_BRIDGE_MAX_CONCURRENT", "1"))
STATE_DIR = Path(os.environ.get("CODEX_BRIDGE_STATE_DIR", str(BRIDGE_DIR / "state"))).resolve()
JOBS_DIR = STATE_DIR / "jobs"
ATTACHMENTS_DIR = STATE_DIR / "attachments"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

_raw_dirs = os.environ.get("CODEX_BRIDGE_ALLOWED_DIRS", str(BRIDGE_DIR.parent.parent))
ALLOWED_ROOTS: list[Path] = [
    Path(d).resolve() for d in _raw_dirs.split(";") if d.strip()
]
DEFAULT_CWD = ALLOWED_ROOTS[0] if ALLOWED_ROOTS else BRIDGE_DIR

_ask_lock = threading.Semaphore(1)
_job_semaphore = threading.Semaphore(MAX_CONCURRENT)
_active_processes: dict[str, subprocess.Popen[str]] = {}
_active_lock = threading.Lock()

print(f"[codex-bridge] CODEX_NODE_EXE={NODE_EXE}", file=sys.stderr)
print(f"[codex-bridge] CODEX_JS_ENTRY={CODEX_JS_ENTRY}", file=sys.stderr)

mcp = FastMCP("codex-bridge", host=HOST, port=PORT)


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


def _current_depth() -> int:
    raw = os.environ.get("MCP_CALL_DEPTH", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def _codex_command(*args: str) -> list[str]:
    if not NODE_EXE or not Path(NODE_EXE).is_file():
        raise FileNotFoundError(
            f"CODEX_NODE_EXE is missing or invalid: {NODE_EXE}. Run scripts/setup-codex-cli.ps1."
        )
    if not CODEX_JS_ENTRY or not Path(CODEX_JS_ENTRY).is_file():
        raise FileNotFoundError(
            f"CODEX_JS_ENTRY is missing or invalid: {CODEX_JS_ENTRY}. Run scripts/setup-codex-cli.ps1."
        )
    return [NODE_EXE, CODEX_JS_ENTRY, *args]


def _child_env(depth: int) -> dict[str, str]:
    keep = [
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    ]
    env = {k: v for k, v in os.environ.items() if k in keep and v}
    node_dir = str(Path(NODE_EXE).resolve().parent) if NODE_EXE else ""
    system32 = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32")
    env["PATH"] = os.pathsep.join([p for p in [node_dir, system32] if p])
    env["MCP_CALL_DEPTH"] = str(depth + 1)
    env["CODEX_BRIDGE_CHILD"] = "1"
    return env


def _audit(message: str) -> None:
    audit_path = BRIDGE_DIR / "logs" / "audit.log"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(message + "\n")


@mcp.tool()
def codex_version() -> str:
    """Return Codex CLI version, bridge settings, and safety state."""
    try:
        cmd = _codex_command("--version")
        result = subprocess.run(
            cmd,
            cwd=BRIDGE_DIR,
            env=_child_env(0),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        version = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            version = f"Codex version check failed (exit {result.returncode}): {version}"
    except (FileNotFoundError, OSError) as e:
        version = str(e)

    return (
        f"{version}\n"
        f"ask_enabled: {ENABLE_ASK}\n"
        f"allowed_roots: {[str(r) for r in ALLOWED_ROOTS]}\n"
        f"default_cwd: {DEFAULT_CWD}\n"
        f"max_depth: {MAX_DEPTH}\n"
        f"max_prompt_bytes: {MAX_PROMPT_BYTES}\n"
        f"state_dir: {STATE_DIR}"
    )


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


def _codex_exec_command(safe: Path, output_file: Path, extra_args: list[str] | None = None) -> list[str]:
    cmd = _codex_command(
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--cd",
        str(safe),
        "--output-last-message",
        str(output_file),
    )
    if extra_args:
        cmd.extend(extra_args)
    return cmd


@mcp.tool()
def ask_codex(
    prompt: str,
    working_dir: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    """Ask local Codex CLI through `codex exec`.

    Disabled by default. Enable with CODEX_BRIDGE_ENABLE_ASK=1 only after the
    portable CLI setup and security policy have been reviewed.
    """
    if not ENABLE_ASK:
        return "ask_codex is disabled. Set CODEX_BRIDGE_ENABLE_ASK=1 only after review."

    prompt_bytes = prompt.encode("utf-8", errors="replace")
    if len(prompt_bytes) > MAX_PROMPT_BYTES:
        return f"Prompt too large: {len(prompt_bytes)} bytes exceeds {MAX_PROMPT_BYTES}. Use codex_attach_prompt and submit_codex_job."

    if not _ask_lock.acquire(blocking=False):
        return "ask_codex is busy. Only one Codex child process is allowed at a time."

    try:
        depth = _current_depth()
        if depth >= MAX_DEPTH:
            return f"Refusing nested call: MCP_CALL_DEPTH={depth}, max_depth={MAX_DEPTH}."

        cwd = Path(working_dir) if working_dir else DEFAULT_CWD
        safe = _check_allowed(cwd)
        if safe is None:
            return f"working_dir is outside allowed roots: {cwd}. allowed_roots={[str(r) for r in ALLOWED_ROOTS]}"
        if not safe.is_dir():
            return f"working_dir does not exist or is not a directory: {safe}"

        output_fd, output_path = tempfile.mkstemp(prefix="codex-output-", suffix=".txt", dir=BRIDGE_TEMP_DIR)
        os.close(output_fd)
        output_file = Path(output_path)

        cmd = _codex_exec_command(safe, output_file, extra_args)

        _audit(f"ask_codex cwd={safe} bytes={len(prompt_bytes)} cmd={shlex.join(cmd)} <stdin>")

        result = subprocess.run(
            cmd,
            cwd=safe,
            env=_child_env(depth),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return f"Codex timed out after {TIMEOUT_SECS} seconds."
    except (FileNotFoundError, OSError) as e:
        return f"Codex execution failed: {e}"
    finally:
        try:
            _ask_lock.release()
        except ValueError:
            pass

    output = (result.stdout or "").strip()
    if output_file.is_file():
        try:
            final_message = output_file.read_text(encoding="utf-8", errors="replace").strip()
            if final_message:
                output = final_message
        except OSError:
            pass
        try:
            output_file.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return (
            f"Codex failed (exit {result.returncode})\n"
            f"command: {shlex.join(cmd)} <stdin>\n"
            f"stderr: {err}\n\n"
            f"stdout: {output}"
        )
    return output or "(Codex produced no output)"


@mcp.tool()
def codex_attach_prompt(content: str, attachment_id: str | None = None, append: bool = False) -> str:
    """Upload large Codex prompt content to bridge-owned storage."""
    data = content.encode("utf-8", errors="replace")
    if len(data) > MAX_PROMPT_BYTES:
        return _json({"error": f"content is too large: max {MAX_PROMPT_BYTES} bytes"})

    safe_id = Path(attachment_id).name if attachment_id else uuid.uuid4().hex
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
def submit_codex_job(
    prompt: str = "",
    prompt_attachment: str | None = None,
    working_dir: str | None = None,
    extra_args: list[str] | None = None,
    timeout_secs: int | None = None,
) -> str:
    """Submit a Codex CLI job and return immediately with job_id."""
    if not ENABLE_ASK:
        return _json({"error": "submit_codex_job is disabled. Set CODEX_BRIDGE_ENABLE_ASK=1 only after review."})

    depth = _current_depth()
    if depth >= MAX_DEPTH:
        return _json({"error": f"Refusing nested call: MCP_CALL_DEPTH={depth}, max_depth={MAX_DEPTH}."})

    cwd = Path(working_dir) if working_dir else DEFAULT_CWD
    safe = _check_allowed(cwd)
    if safe is None:
        return _json({"error": f"working_dir is outside allowed roots: {cwd}. allowed_roots={[str(r) for r in ALLOWED_ROOTS]}"})
    if not safe.is_dir():
        return _json({"error": f"working_dir does not exist or is not a directory: {safe}"})

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
    (job_dir / "final_message.txt").write_text("", encoding="utf-8")

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
        "timeout_secs": effective_timeout,
        "prompt_bytes": len(prompt_bytes),
        "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "extra_args": extra_args or [],
        "pid": None,
        "exit_code": None,
        "error": None,
    }
    _write_meta(job_id, meta)
    thread = threading.Thread(target=_run_codex_job, args=(job_id, depth), daemon=True)
    thread.start()
    return _json({"job_id": job_id, "status": "queued"})


def _run_codex_job(job_id: str, depth: int) -> None:
    acquired = _job_semaphore.acquire(blocking=False)
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
        prompt_path = job_dir / "prompt.md"
        final_message_path = job_dir / "final_message.txt"
        cmd = _codex_exec_command(Path(meta["working_dir"]), final_message_path, meta.get("extra_args") or [])
        meta["status"] = "running"
        meta["started_at"] = _now()
        _write_meta(job_id, meta)
        _audit(f"submit_codex_job job_id={job_id} cwd={meta['working_dir']} bytes={meta['prompt_bytes']} cmd={shlex.join(cmd)} <stdin>")

        with prompt_path.open("r", encoding="utf-8", errors="replace") as prompt_in, (
            job_dir / "stdout.log"
        ).open("w", encoding="utf-8", errors="replace") as out, (job_dir / "stderr.log").open(
            "w", encoding="utf-8", errors="replace"
        ) as err:
            process = subprocess.Popen(
                cmd,
                cwd=str(meta["working_dir"]),
                env=_child_env(depth),
                stdin=prompt_in,
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
    except (FileNotFoundError, OSError) as e:
        meta["status"] = "error"
        meta["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - keep bridge alive and record job failure.
        meta["status"] = "error"
        meta["error"] = str(e)
    finally:
        with _active_lock:
            _active_processes.pop(job_id, None)
        meta["ended_at"] = _now()
        _write_meta(job_id, meta)
        _job_semaphore.release()


@mcp.tool()
def get_codex_job(
    job_id: str,
    wait_ms: int = 0,
    stdout_cursor: int = 0,
    stderr_cursor: int = 0,
    max_bytes: int = 20000,
) -> str:
    """Read Codex job status and output chunks. wait_ms supports long polling."""
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
    final_message = ""
    final_path = job_dir / "final_message.txt"
    if final_path.is_file():
        try:
            final_message = final_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            final_message = ""
    done = meta["status"] not in ("queued", "running")
    return _json(
        {
            "job_id": job_id,
            "status": meta["status"],
            "done": done,
            "exit_code": meta.get("exit_code"),
            "error": meta.get("error"),
            "final_message": final_message,
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
def cancel_codex_job(job_id: str) -> str:
    """Cancel a running Codex job."""
    meta = _read_meta(job_id)
    if meta is None:
        return _json({"job_id": job_id, "status": "not_found"})

    with _active_lock:
        process = _active_processes.get(job_id)
    if process is None:
        return _json({"job_id": job_id, "status": meta["status"], "cancelled": False})

    process.kill()
    meta["status"] = "cancelled"
    meta["ended_at"] = _now()
    meta["error"] = "cancelled by request"
    _write_meta(job_id, meta)
    return _json({"job_id": job_id, "status": "cancelled", "cancelled": True})


@mcp.tool()
def list_codex_jobs(status: str | None = None) -> str:
    """List known Codex jobs."""
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
    """Read a whitelisted file without launching Codex."""
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
