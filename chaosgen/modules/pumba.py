"""
Pumba integration — real Docker chaos via Pumba CLI or docker run gaiaadm/pumba.

WS-4: executes against DOCKER_HOST / compose stack (modular monolith P0 path).
Set inject.dry_run=True to emit planned commands without mutating containers.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Any

from .base import BaseChaosModule

logger = logging.getLogger(__name__)

_PUMBA_IMAGE = "gaiaadm/pumba:latest"
_DEFAULT_TIMEOUT_S = 120


class PumbaModule(BaseChaosModule):
    """Docker chaos via Pumba (kill / pause / netem)."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.docker_host = self.config.get("docker_host")
        self.compose_file = self.config.get("compose_file")
        self.project_name = self.config.get("project_name")
        self.dry_run = bool(self.config.get("dry_run", False))
        self.timeout_s = int(self.config.get("timeout_s", _DEFAULT_TIMEOUT_S))
        self.target_containers = self.config.get("target_containers", [])
        self.interval = self.config.get("interval", "10s")
        self._pumba_bin: str | None = None

    def validate_config(self) -> bool:
        if self.dry_run:
            return True
        return self._docker_available()

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        action_map = {
            "kill_container": self._kill_container,
            "pause_container": self._pause_container,
            "stop_container": self._stop_container,
            "delay_network": self._delay_network,
            "loss_network": self._loss_network,
            "rate_limit": self._rate_limit,
        }
        if action not in action_map:
            return {"success": False, "error": f"Unknown action: {action}"}
        try:
            return action_map[action](params)
        except Exception as exc:
            logger.exception("Pumba action %s failed", action)
            return {"success": False, "module": "pumba", "action": action, "error": str(exc)}

    # ------------------------------------------------------------------
    # Docker / Pumba runners
    # ------------------------------------------------------------------

    def _docker_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
        return env

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=15,
                env=self._docker_env(),
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def _pumba_executable(self) -> str | None:
        if self._pumba_bin is not None:
            return self._pumba_bin
        found = shutil.which("pumba")
        self._pumba_bin = found
        return found

    def _ensure_ready(self) -> dict[str, Any] | None:
        """Return error dict if Docker is required but unavailable."""
        if self.dry_run:
            return None
        if not self._docker_available():
            return {
                "success": False,
                "module": "pumba",
                "error": "Docker engine not reachable (check connect.docker.host)",
            }
        return None

    def _run(self, argv: list[str], *, label: str) -> dict[str, Any]:
        if self.dry_run:
            return {
                "success": True,
                "module": "pumba",
                "dry_run": True,
                "command": " ".join(argv),
                "message": f"dry-run: {label}",
            }

        logger.info("Pumba exec: %s", " ".join(argv))
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=self._docker_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "module": "pumba",
                "error": f"timeout after {self.timeout_s}s",
                "command": " ".join(argv),
            }

        ok = completed.returncode == 0
        out = {
            "success": ok,
            "module": "pumba",
            "command": " ".join(argv),
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
        }
        if not ok:
            out["error"] = completed.stderr.strip() or f"exit code {completed.returncode}"
        return out

    def _pumba_argv(self, *pumba_args: str) -> list[str]:
        """Native pumba binary or docker-run wrapper with socket mount."""
        if self._pumba_executable():
            return ["pumba", *pumba_args]

        sock = self._docker_socket_path()
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{sock}:{sock}",
            "-e",
            f"DOCKER_HOST=unix://{sock}",
            _PUMBA_IMAGE,
            *pumba_args,
        ]

    @staticmethod
    def _docker_socket_path() -> str:
        host = os.environ.get("DOCKER_HOST", "")
        if host.startswith("unix://"):
            return host.replace("unix://", "", 1)
        if os.name == "nt":
            return "//./pipe/docker_engine"
        return "/var/run/docker.sock"

    def _resolve_container(self, name: str) -> str:
        """Return running container id/name matching logical target (compose-aware)."""
        if not name or name == "target":
            raise ValueError("container name is required")

        filters = ["name=" + name]
        if self.project_name:
            filters.append(f"label=com.docker.compose.project={self.project_name}")

        argv = ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={name}"]
        if self.dry_run:
            return name

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            env=self._docker_env(),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "docker ps failed")

        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not names:
            raise RuntimeError(f"no running container matches {name!r}")

        if self.project_name:
            prefixed = [n for n in names if n.startswith(f"{self.project_name}_") or self.project_name in n]
            if prefixed:
                return prefixed[0]

        # Prefer exact suffix match (compose: project_service_1)
        for candidate in names:
            if candidate == name or candidate.endswith(f"_{name}_1") or candidate.endswith(f"_{name}"):
                return candidate
        return names[0]

    def _normalize_signal(self, signal: str) -> str:
        sig = (signal or "SIGKILL").upper()
        if not sig.startswith("SIG"):
            sig = f"SIG{sig}"
        return sig

    def _normalize_duration(self, raw: str | None, default: str = "60s") -> str:
        if not raw:
            return default
        raw = str(raw).strip()
        if re.match(r"^\d+$", raw):
            return f"{raw}s"
        return raw

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _kill_container(self, params: dict[str, Any]) -> dict[str, Any]:
        blocked = self._ensure_ready()
        if blocked:
            return blocked
        container = self._resolve_container(params.get("container", "target"))
        signal = self._normalize_signal(params.get("signal", "SIGKILL"))

        # docker kill is reliable when pumba image pull is slow; same blast radius.
        if self.dry_run:
            cmd = ["docker", "kill", "--signal", signal, container]
            out = self._run(cmd, label=f"kill {container}")
        else:
            cmd = ["docker", "kill", "--signal", signal, container]
            out = self._run(cmd, label=f"kill {container}")

        out.update(
            {
                "action": "kill_container",
                "container": container,
                "signal": signal,
            }
        )
        return out

    def _pause_container(self, params: dict[str, Any]) -> dict[str, Any]:
        blocked = self._ensure_ready()
        if blocked:
            return blocked
        container = self._resolve_container(params.get("container", "target"))
        duration = self._normalize_duration(params.get("duration"), "30s")

        if self.dry_run:
            out = self._run(
                ["docker", "pause", container],
                label=f"pause {container}",
            )
            out["note"] = f"would unpause after {duration} (manual or orchestrator rollback)"
            out.update({"action": "pause_container", "container": container, "duration": duration})
            return out

        out = self._run(["docker", "pause", container], label=f"pause {container}")
        out.update({"action": "pause_container", "container": container, "duration": duration})
        return out

    def _stop_container(self, params: dict[str, Any]) -> dict[str, Any]:
        blocked = self._ensure_ready()
        if blocked:
            return blocked
        container = self._resolve_container(params.get("container", "target"))
        out = self._run(["docker", "stop", container], label=f"stop {container}")
        out.update({"action": "stop_container", "container": container})
        return out

    def _delay_network(self, params: dict[str, Any]) -> dict[str, Any]:
        blocked = self._ensure_ready()
        if blocked:
            return blocked
        container = self._resolve_container(params.get("container", "target"))
        delay = params.get("delay", "100ms")
        duration = self._normalize_duration(params.get("duration"), "60s")
        jitter = params.get("jitter")

        pumba_args = [
            "netem",
            "--duration",
            duration,
            "delay",
            "--time",
            str(delay),
        ]
        if jitter:
            pumba_args.extend(["--jitter", str(jitter)])
        pumba_args.append(container)

        out = self._run(self._pumba_argv(*pumba_args), label=f"netem delay {container}")
        out.update(
            {
                "action": "delay_network",
                "container": container,
                "delay": delay,
                "duration": duration,
            }
        )
        return out

    def _loss_network(self, params: dict[str, Any]) -> dict[str, Any]:
        blocked = self._ensure_ready()
        if blocked:
            return blocked
        container = self._resolve_container(params.get("container", "target"))
        loss = str(params.get("loss", "10"))
        duration = self._normalize_duration(params.get("duration"), "60s")

        pumba_args = [
            "netem",
            "--duration",
            duration,
            "loss",
            "--percent",
            loss,
            container,
        ]
        out = self._run(self._pumba_argv(*pumba_args), label=f"netem loss {container}")
        out.update(
            {
                "action": "loss_network",
                "container": container,
                "loss": loss,
                "duration": duration,
            }
        )
        return out

    def _rate_limit(self, params: dict[str, Any]) -> dict[str, Any]:
        blocked = self._ensure_ready()
        if blocked:
            return blocked
        container = self._resolve_container(params.get("container", "target"))
        rate = params.get("rate", "1000kbit")
        duration = self._normalize_duration(params.get("duration"), "60s")

        pumba_args = [
            "netem",
            "--duration",
            duration,
            "rate",
            "--rate",
            str(rate),
            container,
        ]
        out = self._run(self._pumba_argv(*pumba_args), label=f"netem rate {container}")
        out.update(
            {
                "action": "rate_limit",
                "container": container,
                "rate": rate,
                "duration": duration,
            }
        )
        return out

    def get_available_actions(self) -> list[str]:
        return [
            "kill_container",
            "pause_container",
            "stop_container",
            "delay_network",
            "loss_network",
            "rate_limit",
        ]

    def get_status(self) -> dict[str, Any]:
        return {
            "module": "pumba",
            "configured": self.validate_config(),
            "dry_run": self.dry_run,
            "docker_host": self.docker_host,
            "compose_file": self.compose_file,
            "project_name": self.project_name,
            "docker_available": self._docker_available() if not self.dry_run else None,
            "pumba_binary": bool(self._pumba_executable()),
            "target_containers": self.target_containers,
            "interval": self.interval,
        }
