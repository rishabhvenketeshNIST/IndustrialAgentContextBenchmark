"""Start and stop a local Eclipse Mosquitto broker (development and tests).

The UNS uses a real MQTT broker. This helper runs the ``mosquitto`` executable as a child process with
the repository configuration (``uns/mosquitto.conf``) on a chosen port, so that tests can start,
stop and restart it. In production-like use, run Mosquitto yourself (``mosquitto -c
uns/mosquitto.conf``, a system service, or the ``eclipse-mosquitto`` Docker image).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

CONF = Path(__file__).with_name("mosquitto.conf")
_WINDOWS_DEFAULT = Path(r"C:\Program Files\mosquitto\mosquitto.exe")


def find_mosquitto() -> Optional[str]:
    """The mosquitto executable: $MOSQUITTO, PATH, or the default Windows install location."""
    for cand in (os.environ.get("MOSQUITTO"), shutil.which("mosquitto")):
        if cand and Path(cand).exists():
            return cand
    return str(_WINDOWS_DEFAULT) if _WINDOWS_DEFAULT.exists() else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


class LocalBroker:
    def __init__(self, port: Optional[int] = None, executable: Optional[str] = None) -> None:
        self.executable = executable or find_mosquitto()
        if not self.executable:
            raise FileNotFoundError("mosquitto executable not found (install Eclipse Mosquitto or set MOSQUITTO)")
        self.port = port or free_port()
        self._dir = Path(tempfile.mkdtemp(prefix="uns_broker_"))
        self._proc: Optional[subprocess.Popen] = None

    def _config(self) -> Path:
        text = CONF.read_text(encoding="utf-8").replace("listener 1883 127.0.0.1", f"listener {self.port} 127.0.0.1")
        path = self._dir / "mosquitto.conf"
        path.write_text(text, encoding="utf-8")
        return path

    def start(self, timeout: float = 10.0) -> "LocalBroker":
        self._proc = subprocess.Popen([self.executable, "-c", str(self._config())],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while not port_open(self.port):
            if self._proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"mosquitto did not start on port {self.port}")
            time.sleep(0.05)
        return self

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        deadline = time.monotonic() + 10
        while port_open(self.port) and time.monotonic() < deadline:
            time.sleep(0.05)

    def restart(self) -> None:
        self.stop()
        self.start()

    def __enter__(self) -> "LocalBroker":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
