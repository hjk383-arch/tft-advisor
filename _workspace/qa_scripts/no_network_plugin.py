"""pytest 플러그인(QA): 외부 네트워크 연결 시도를 즉시 실패시킨다. 사용: PYTHONPATH=_workspace/qa_scripts pytest -p no_network_plugin"""
import socket

_orig = socket.socket.connect
ATTEMPTS = []


def _guard(self, addr, *a, **k):
    host = addr[0] if isinstance(addr, tuple) else addr
    if host in ("127.0.0.1", "::1", "localhost") or (isinstance(addr, str)):
        return _orig(self, addr, *a, **k)
    ATTEMPTS.append(addr)
    raise OSError(f"QA no-network guard: blocked connect to {addr!r}")


socket.socket.connect = _guard


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"QA no-network guard: blocked attempts = {len(ATTEMPTS)} {ATTEMPTS[:5]}")
