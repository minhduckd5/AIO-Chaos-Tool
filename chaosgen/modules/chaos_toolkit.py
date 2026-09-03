"""
Chaos Toolkit integration — real ``chaos validate`` / ``chaos run``.

Canonical executor for ChaosGen CTK experiments (see docs/adr-ctk-canonical-runtime.md).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseChaosModule

logger = logging.getLogger(__name__)


def resolve_chaos_binary() -> Optional[str]:
    """Locate ``chaos`` CLI (PATH or common Windows Scripts dirs)."""
    found = shutil.which("chaos")
    if found:
        return found
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "Python" / "Python312" / "Scripts" / "chaos.exe",
        Path(os.environ.get("APPDATA", "")) / "Python" / "Python311" / "Scripts" / "chaos.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Python"
        / "Python312"
        / "Scripts"
        / "chaos.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _kill_process_tree(pid: int) -> None:
    """Terminate a subprocess and its children (Windows + POSIX)."""
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return
    time.sleep(0.15)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class ChaosToolkitModule(BaseChaosModule):
    """Run Chaos Toolkit experiments via subprocess."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.experiment_path = self.config.get("experiment_path", "")
        self.rollback_enabled = self.config.get("rollback_enabled", True)
        self.dry_run = bool(self.config.get("dry_run", False))
        self.timeout_s = int(self.config.get("timeout_s", 300))
        self.chaos_bin = self.config.get("chaos_bin") or resolve_chaos_binary()
        self.journal_dir = self.config.get("journal_dir") or str(
            Path("scratch") / "ctk" / "journals"
        )
        # --- START MODIFICATION ---
        # HALT: track long-running ``chaos run`` for abort_run()
        # --- END MODIFICATION ---
        self._proc: Optional[subprocess.Popen] = None
        self._abort_requested = False
        self._run_lock = threading.Lock()

    def validate_config(self) -> bool:
        return True

    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        actions = {
            "run_experiment": self._run_experiment,
            "validate": self._validate_experiment,
            "discover": self._discover_capabilities,
            "abort_run": self._abort_run_action,
        }
        handler = actions.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}
        return handler(params)

    def _require_bin(self) -> Optional[str]:
        binary = self.chaos_bin or resolve_chaos_binary()
        if not binary:
            return None
        self.chaos_bin = binary
        return binary

    def is_run_active(self) -> bool:
        with self._run_lock:
            if self._proc is None:
                return False
            return self._proc.poll() is None

    def abort_run(self) -> bool:
        """
        Request abort of in-flight ``chaos run`` and kill the process tree.

        Safe to call from GUI thread while worker is blocked on the run.
        """
        with self._run_lock:
            self._abort_requested = True
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        logger.warning("HALT: killing chaos run pid=%s", proc.pid)
        _kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc.pid)
        with self._run_lock:
            if self._proc is proc:
                self._proc = None
        return True

    def _abort_run_action(self, params: Dict[str, Any]) -> Dict[str, Any]:
        aborted = self.abort_run()
        return {
            "success": True,
            "module": "chaos-toolkit",
            "action": "abort_run",
            "aborted": aborted,
            "message": "chaos run aborted" if aborted else "no active chaos run",
        }

    def _popen_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return kwargs

    def _run_cmd(self, args: List[str], *, timeout: Optional[int] = None) -> Dict[str, Any]:
        binary = self._require_bin()
        if not binary:
            return {
                "success": False,
                "module": "chaos-toolkit",
                "error": "chaos CLI not found (pip install chaostoolkit; ensure Scripts on PATH)",
            }
        cmd = [binary] + args
        t = timeout if timeout is not None else self.timeout_s
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                check=False,
                env=env,
                encoding="utf-8",
                errors="replace",
            )
            ok = completed.returncode == 0
            combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
            if (
                not ok
                and "Experiment ended with status: completed" in combined
                and "UnicodeEncodeError" in combined
            ):
                ok = True
            return {
                "success": ok,
                "module": "chaos-toolkit",
                "cmd": cmd,
                "returncode": completed.returncode,
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "message": (completed.stdout or completed.stderr or "").strip(),
                "error": None
                if ok
                else (completed.stderr or completed.stdout or f"exit {completed.returncode}"),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "success": False,
                "module": "chaos-toolkit",
                "cmd": cmd,
                "timeout": True,
                "error": f"timeout after {t}s",
                "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            }
        except FileNotFoundError:
            return {
                "success": False,
                "module": "chaos-toolkit",
                "error": f"chaos binary not executable: {binary}",
                "cmd": cmd,
            }

    def _run_cmd_long(self, args: List[str], *, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Run ``chaos run`` with Popen so HALT can kill the process tree."""
        binary = self._require_bin()
        if not binary:
            return {
                "success": False,
                "module": "chaos-toolkit",
                "error": "chaos CLI not found (pip install chaostoolkit; ensure Scripts on PATH)",
            }
        cmd = [binary] + args
        t = timeout if timeout is not None else self.timeout_s
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        with self._run_lock:
            self._abort_requested = False
            try:
                proc = subprocess.Popen(cmd, env=env, **self._popen_kwargs())
            except FileNotFoundError:
                return {
                    "success": False,
                    "module": "chaos-toolkit",
                    "error": f"chaos binary not executable: {binary}",
                    "cmd": cmd,
                }
            self._proc = proc

        deadline = time.time() + t if t else None
        aborted = False
        timed_out = False

        while True:
            with self._run_lock:
                if self._abort_requested:
                    aborted = True
                    if self._proc and self._proc.poll() is None:
                        _kill_process_tree(self._proc.pid)
                proc = self._proc
            if proc is None:
                break
            rc = proc.poll()
            if rc is not None:
                break
            if deadline is not None and time.time() > deadline:
                timed_out = True
                self.abort_run()
                break
            time.sleep(0.1)

        stdout = ""
        stderr = ""
        returncode = -1
        with self._run_lock:
            proc = self._proc
            if proc is not None:
                try:
                    stdout, stderr = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(proc.pid)
                    stdout, stderr = proc.communicate()
                returncode = proc.returncode if proc.returncode is not None else -1
                self._proc = None

        if aborted:
            return {
                "success": False,
                "module": "chaos-toolkit",
                "cmd": cmd,
                "aborted": True,
                "returncode": returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "error": "experiment aborted (HALT)",
                "message": "chaos run aborted by operator",
            }
        if timed_out:
            return {
                "success": False,
                "module": "chaos-toolkit",
                "cmd": cmd,
                "timeout": True,
                "returncode": returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "error": f"timeout after {t}s",
            }

        ok = returncode == 0
        combined = f"{stdout or ''}\n{stderr or ''}"
        if (
            not ok
            and "Experiment ended with status: completed" in combined
            and "UnicodeEncodeError" in combined
        ):
            ok = True
        return {
            "success": ok,
            "module": "chaos-toolkit",
            "cmd": cmd,
            "returncode": returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "message": (stdout or stderr or "").strip(),
            "error": None
            if ok
            else (stderr or stdout or f"exit {returncode}"),
        }

    def _validate_experiment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        experiment_file = params.get("experiment_file") or self.experiment_path
        if not experiment_file or not Path(experiment_file).is_file():
            return {"success": False, "error": f"experiment file not found: {experiment_file}"}
        result = self._run_cmd(["validate", str(experiment_file)], timeout=min(60, self.timeout_s))
        result["action"] = "validate"
        result["experiment_file"] = str(experiment_file)
        return result

    def _run_experiment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        experiment_file = params.get("experiment_file") or self.experiment_path
        if not experiment_file or not Path(experiment_file).is_file():
            return {"success": False, "error": f"experiment file not found: {experiment_file}"}

        dry = params.get("dry_run", self.dry_run)
        if dry:
            result = self._validate_experiment({"experiment_file": experiment_file})
            result["action"] = "run_experiment"
            result["dry_run"] = True
            result["message"] = result.get("message") or "dry-run: chaos validate only"
            return result

        journal_dir = Path(params.get("journal_dir") or self.journal_dir)
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = params.get("journal_path") or str(
            journal_dir / f"journal-{Path(experiment_file).stem}.json"
        )

        result = self._run_cmd_long(
            ["run", str(experiment_file), "--journal-path", str(journal_path)],
            timeout=int(params.get("timeout_s", self.timeout_s)),
        )
        result["action"] = "run_experiment"
        result["dry_run"] = False
        result["experiment_file"] = str(experiment_file)
        result["journal_path"] = str(journal_path)

        if Path(journal_path).is_file():
            try:
                journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
                result["journal_status"] = journal.get("status")
                result["deviated"] = journal.get("deviated")
            except Exception as exc:
                logger.debug("journal parse skipped: %s", exc)
        return result

    def _discover_capabilities(self, params: Dict[str, Any]) -> Dict[str, Any]:
        package = params.get("package") or "chaostoolkit-kubernetes"
        result = self._run_cmd(
            ["discover", "--no-install", str(package)],
            timeout=min(120, self.timeout_s),
        )
        result["action"] = "discover"
        return result

    def get_available_actions(self) -> List[str]:
        return ["run_experiment", "validate", "discover", "abort_run"]

    def get_status(self) -> Dict[str, Any]:
        binary = self._require_bin()
        return {
            "module": "chaos-toolkit",
            "configured": bool(binary),
            "chaos_bin": binary,
            "experiment_path": self.experiment_path,
            "rollback_enabled": self.rollback_enabled,
            "dry_run": self.dry_run,
            "run_active": self.is_run_active(),
        }
