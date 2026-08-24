"""
SSH local-forward bastion for Kubernetes API access.

Used only when the API server is not reachable from the GUI host (Lens-parity
kubeconfig still applies; the tunnel rewrites the server to 127.0.0.1).

MVP uses OpenSSH (``ssh`` on PATH) so Windows 10+ works without paramiko.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(host: str, port: int, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class SshBastionTunnel:
    """OpenSSH ``-L`` tunnel. Process is owned by this object until stop()."""

    def __init__(
        self,
        *,
        host: str,
        user: Optional[str] = None,
        port: int = 22,
        identity_file: Optional[str] = None,
        remote_api_host: str = "127.0.0.1",
        remote_api_port: int = 6443,
        local_port: int = 0,
        extra_args: Optional[List[str]] = None,
    ):
        self.host = host
        self.user = user
        self.port = int(port)
        self.identity_file = (
            str(Path(os.path.expanduser(identity_file)).resolve())
            if identity_file
            else None
        )
        self.remote_api_host = remote_api_host
        self.remote_api_port = int(remote_api_port)
        self.local_port = int(local_port) if local_port else 0
        self.extra_args = list(extra_args or [])
        self._proc: Optional[subprocess.Popen] = None

    def build_cmd(self, local_port: Optional[int] = None) -> List[str]:
        lp = int(local_port or self.local_port or 16443)
        target = f"{self.user}@{self.host}" if self.user else self.host
        spec = f"{lp}:{self.remote_api_host}:{self.remote_api_port}"
        cmd = [
            "ssh",
            "-N",
            "-L",
            spec,
            "-p",
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
        ]
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
        cmd.extend(self.extra_args)
        cmd.append(target)
        return cmd

    def start(self, wait_s: float = 15.0) -> Dict[str, Any]:
        if self.is_alive():
            return {
                "success": True,
                "local_port": self.local_port,
                "message": "bastion already up",
            }
        if not self.host:
            return {"success": False, "error": "ssh bastion host is empty"}
        if self.identity_file and not Path(self.identity_file).is_file():
            return {
                "success": False,
                "error": f"ssh identity file not found: {self.identity_file}",
            }
        self.local_port = self.local_port or pick_free_port()
        cmd = self.build_cmd(self.local_port)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error": "ssh binary not found on PATH (OpenSSH required for bastion)",
                "cmd": cmd,
            }
        if not wait_for_port("127.0.0.1", self.local_port, timeout_s=wait_s):
            err = ""
            if self._proc and self._proc.poll() is not None:
                err = (self._proc.stderr.read() if self._proc.stderr else "") or ""
            self.stop()
            return {
                "success": False,
                "error": err.strip() or f"ssh tunnel did not listen on 127.0.0.1:{self.local_port}",
                "cmd": cmd,
            }
        logger.info(
            "SSH bastion listening on 127.0.0.1:%s → %s:%s via %s",
            self.local_port,
            self.remote_api_host,
            self.remote_api_port,
            self.host,
        )
        return {
            "success": True,
            "local_port": self.local_port,
            "cmd": cmd,
            "message": f"bastion 127.0.0.1:{self.local_port}",
        }

    def is_alive(self) -> bool:
        if not self._proc or self._proc.poll() is not None:
            return False
        return wait_for_port("127.0.0.1", self.local_port, timeout_s=0.4)

    def stop(self) -> None:
        if not self._proc:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
