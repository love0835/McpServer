from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="claude_bridge_allowed_"))
STATE_ROOT = Path(tempfile.mkdtemp(prefix="claude_bridge_state_"))
os.environ["CLAUDE_BRIDGE_ALLOWED_DIRS"] = str(TEST_ROOT)
os.environ["CLAUDE_BRIDGE_STATE_DIR"] = str(STATE_ROOT)
os.environ["CLAUDE_BRIDGE_HOST"] = "127.0.0.1"
os.environ["CLAUDE_BRIDGE_PORT"] = "18999"
os.environ["CLAUDE_BRIDGE_MAX_CONCURRENT"] = "2"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class FakePopen:
    next_pid = 9000
    last_cmd = []
    last_stdin_name = ""

    def __init__(self, cmd, cwd=None, stdout=None, stderr=None, **kwargs):
        self.cmd = list(cmd)
        self.cwd = str(cwd)
        self.stdin = kwargs.get("stdin")
        FakePopen.last_cmd = self.cmd
        FakePopen.last_stdin_name = getattr(self.stdin, "name", "")
        self.stdout = stdout
        self.stderr = stderr
        self.pid = FakePopen.next_pid
        FakePopen.next_pid += 1
        self._killed = False
        if stdout:
            stdout.write("fake stdout\n")
            stdout.flush()
        if stderr:
            stderr.write("fake stderr\n")
            stderr.flush()

    def wait(self, timeout=None):
        return -9 if self._killed else 0

    def kill(self):
        self._killed = True

    def poll(self):
        return None if not self._killed else -9


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"PASS {name}")


def parse(data: str):
    return json.loads(data)


def wait_done(job_id: str):
    deadline = time.time() + 5
    while time.time() < deadline:
        payload = parse(server.get_claude_job(job_id))
        if payload["done"]:
            return payload
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def main() -> int:
    original_popen = server.subprocess.Popen
    server.subprocess.Popen = FakePopen
    try:
        attach = parse(server.bridge_attach_prompt("large prompt body"))
        check("attachment created", "attachment_id" in attach)
        attachment_path = server.ATTACHMENTS_DIR / f"{attach['attachment_id']}.md"
        check("attachment stored in bridge state", attachment_path.is_file(), str(attachment_path))

        submitted = parse(
            server.submit_claude_job(
                prompt="prefix",
                prompt_attachment=attach["attachment_id"],
                working_dir=str(TEST_ROOT),
            )
        )
        job_id = submitted["job_id"]
        check("submit returns queued job", submitted["status"] == "queued", submitted)
        check("attachment consumed", not attachment_path.exists(), str(attachment_path))
        check("prompt stored under job", (server.JOBS_DIR / job_id / "prompt.md").is_file())

        done = wait_done(job_id)
        check("job succeeded", done["status"] == "succeeded", done)
        check("stdout cursor advances", done["stdout_next_cursor"] > 0, done)
        check("stderr cursor advances", done["stderr_next_cursor"] > 0, done)
        check("stdout content returned", "fake stdout" in done["stdout_chunk"], done["stdout_chunk"])
        check("prompt not passed as command argument", "large prompt body" not in FakePopen.last_cmd, FakePopen.last_cmd)
        check("prompt passed through stdin", FakePopen.last_stdin_name.endswith("prompt.md"), FakePopen.last_stdin_name)

        listed = parse(server.list_claude_jobs())
        check("list includes job", any(row["job_id"] == job_id for row in listed), listed)

        bad = parse(server.submit_claude_job(prompt="x", working_dir=r"C:\Windows\Temp"))
        check("rejects non-allowed cwd", "error" in bad, bad)

        missing = parse(server.get_claude_job("missing"))
        check("missing job returns error", missing.get("error") == "job not found", missing)

        print("ALL JOB TESTS PASSED")
        return 0
    finally:
        server.subprocess.Popen = original_popen
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        shutil.rmtree(STATE_ROOT, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
