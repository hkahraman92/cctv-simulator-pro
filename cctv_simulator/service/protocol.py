"""JSON-RPC 2.0 over newline-delimited JSON (stdin/stdout).

Framing: one UTF-8 JSON object per line, both directions. Requests run on a
small worker pool so a long analysis does not block ``system.ping`` or a
cancel. Conventions borrowed from LSP:

* ``$/cancelRequest`` notification ``{"id": <request id>}`` sets the request's
  :class:`CancelToken`; a method that checks it raises :class:`Cancelled`,
  which is answered with error code ``REQUEST_CANCELLED``.
* ``$/progress`` notifications ``{"id", "done", "total", "message"}`` are sent
  by methods through :meth:`Context.progress`.

stdout is the protocol channel and nothing else may write to it: while the
server runs, ``sys.stdout`` is pointed at stderr so a stray ``print`` in the
engine shows up in the log instead of corrupting a frame.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, IO, Optional

# JSON-RPC 2.0 reserved codes + LSP's cancel code.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
REQUEST_CANCELLED = -32800
# Application errors (domain failures the shell shows to the user).
APP_ERROR = -32000


class RpcError(Exception):
    """An error with a JSON-RPC code; ``message`` is user-facing Turkish."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class Cancelled(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


@dataclass
class Context:
    """Per-request handle passed to every method."""

    request_id: Any
    token: CancelToken
    _notify: Callable[[str, Dict[str, Any]], None] = field(repr=False)

    def progress(self, done: float, total: float, message: str = "") -> None:
        self.token.check()
        self._notify("$/progress", {"id": self.request_id, "done": done,
                                    "total": total, "message": message})


def to_jsonable(value: Any) -> Any:
    """JSON has no inf/NaN: non-finite floats become ``null``."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


Method = Callable[[Dict[str, Any], Context], Any]


class Server:
    def __init__(self, methods: Dict[str, Method], *, workers: int = 2,
                 out: Optional[IO[str]] = None) -> None:
        self.methods = methods
        self._out = out
        self._write_lock = threading.Lock()
        self._tokens: Dict[Any, CancelToken] = {}
        self._tokens_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rpc")

    # ── output ────────────────────────────────────────────────────────────
    def _send(self, message: Dict[str, Any]) -> None:
        line = json.dumps(to_jsonable(message), ensure_ascii=False, allow_nan=False)
        with self._write_lock:
            self._out.write(line + "\n")
            self._out.flush()

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _error(self, req_id: Any, code: int, message: str, data: Any = None) -> None:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"jsonrpc": "2.0", "id": req_id, "error": err})

    # ── dispatch ──────────────────────────────────────────────────────────
    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            self._error(None, PARSE_ERROR, f"Geçersiz JSON: {exc.msg}")
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            self._error(msg.get("id") if isinstance(msg, dict) else None,
                        INVALID_REQUEST, "Geçersiz JSON-RPC isteği.")
            return
        method, params, req_id = msg["method"], msg.get("params") or {}, msg.get("id")
        is_notification = "id" not in msg

        if method == "$/cancelRequest":
            with self._tokens_lock:
                token = self._tokens.get((params or {}).get("id"))
            if token is not None:
                token.cancel()
            return
        if is_notification:
            return          # no other notifications are understood; ignore per spec
        if not isinstance(params, dict):
            self._error(req_id, INVALID_PARAMS, "Parametreler bir nesne olmalı.")
            return
        fn = self.methods.get(method)
        if fn is None:
            self._error(req_id, METHOD_NOT_FOUND, f"Bilinmeyen yöntem: {method}")
            return
        token = CancelToken()
        with self._tokens_lock:
            self._tokens[req_id] = token
        self._pool.submit(self._run, fn, params, req_id, token)

    def _run(self, fn: Method, params: Dict[str, Any], req_id: Any, token: CancelToken) -> None:
        ctx = Context(req_id, token, self.notify)
        try:
            token.check()
            result = fn(params, ctx)
            token.check()
            self._send({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Cancelled:
            self._error(req_id, REQUEST_CANCELLED, "İstek iptal edildi.")
        except RpcError as exc:
            self._error(req_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # engine bug: report, keep serving
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            self._error(req_id, INTERNAL_ERROR, f"İç hata: {type(exc).__name__}: {exc}",
                        {"traceback": tb})
        finally:
            with self._tokens_lock:
                self._tokens.pop(req_id, None)

    def wait_idle(self) -> None:
        self._pool.shutdown(wait=True)

    # ── main loop ─────────────────────────────────────────────────────────
    def serve(self, inp: IO[str]) -> None:
        for line in inp:
            self.handle_line(line)
        self.wait_idle()


def serve_stdio(methods: Dict[str, Method]) -> None:
    """Run until stdin closes (the shell exiting closes our stdin)."""
    stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    sys.stdout = sys.stderr          # keep the protocol channel clean
    Server(methods, out=stdout).serve(stdin)
