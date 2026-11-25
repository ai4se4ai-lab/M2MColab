"""In-process service metrics for the status page."""
from __future__ import annotations

import threading
import time
from collections import Counter


class Metrics:
    def __init__(self) -> None:
        self.started = time.time()
        self._lock = threading.Lock()
        self.requests = 0
        self.api_requests = 0
        self.mcp_requests = 0
        self.unauthorized = 0
        self.tool_calls: Counter[str] = Counter()
        self.last_error: dict | None = None
        self.last_mcp_call: float | None = None

    def request(self, path: str) -> None:
        with self._lock:
            self.requests += 1
            if path.startswith("/api/"):
                self.api_requests += 1
            elif path == "/mcp" or path.startswith("/mcp/"):
                self.mcp_requests += 1

    def denied(self) -> None:
        with self._lock:
            self.unauthorized += 1

    def tool(self, name: str) -> None:
        with self._lock:
            self.tool_calls[name] += 1
            self.last_mcp_call = time.time()

    def error(self, where: str, message: str) -> None:
        with self._lock:
            self.last_error = {"t": time.time(), "where": where, "message": message[:300]}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requests": self.requests,
                "api_requests": self.api_requests,
                "mcp_requests": self.mcp_requests,
                "unauthorized": self.unauthorized,
                "tool_calls": dict(self.tool_calls.most_common()),
                "tool_calls_total": sum(self.tool_calls.values()),
                "last_mcp_call": self.last_mcp_call,
                "last_error": self.last_error,
            }
