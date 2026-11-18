"""Single-instance IPC server via UNIX socket."""
import os
import socket
import threading
from typing import Callable, Optional

try:
    from ..config import IPC_SOCK
except ImportError:
    import os
    IPC_SOCK = os.path.expanduser("~/.cache/meikai/meikai_ipc.sock")


class IpcServer:
    """Listens on a UNIX socket for new-instance image paths."""

    def __init__(self, on_message: Callable[[str], None]):
        self._on_message = on_message
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        """Start listening. Returns True on success."""
        # Check if another instance is already actively responding on this socket
        if os.path.exists(IPC_SOCK):
            if send_to_running_instance("ping"):
                print("[IPC] Another Meikai instance is already running.")
                return False
            # Socket exists but nobody is listening — stale socket from abnormal exit
            try:
                os.remove(IPC_SOCK)
            except Exception:
                pass
        try:
            os.makedirs(os.path.dirname(IPC_SOCK), exist_ok=True)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(IPC_SOCK)
            self._sock.listen(5)
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            print(f"[IPC] Failed to start server: {e}")
            return False

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if os.path.exists(IPC_SOCK):
            try:
                os.remove(IPC_SOCK)
            except Exception:
                pass

    def _accept_loop(self):
        while self._running:
            try:
                self._sock.settimeout(1.0)
                conn, _ = self._sock.accept()
                data = conn.recv(4096).decode("utf-8", errors="ignore").strip()
                conn.close()
                if data:
                    self._on_message(data)
            except socket.timeout:
                continue
            except Exception:
                break


def send_to_running_instance(message: str) -> bool:
    """Send a message to the running instance. Returns True if succeeded."""
    if not os.path.exists(IPC_SOCK):
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(IPC_SOCK)
        sock.send(message.encode("utf-8"))
        sock.close()
        return True
    except Exception:
        return False
