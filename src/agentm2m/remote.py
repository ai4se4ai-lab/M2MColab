"""Forward tool calls to a hosted agentm2m service (`agentm2m serve`).

When `AGENTM2M_URL` and `AGENTM2M_API_KEY` are set, the stdio MCP server that
the Claude Code plugin starts becomes a thin client of the hosted service:
every tool call is sent to `<AGENTM2M_URL>/mcp` with the key, under the same
tool name, so the plugin's skills, subagents and hooks work unchanged against
a local or a hosted engine. `AGENTM2M_PROJECT` selects the server-side
project (default: the service's `default`).

A minimal synchronous MCP client over streamable HTTP: initialize once, keep
the session id, re-initialize if the server forgot it.
"""
from __future__ import annotations

import itertools
import json
import os
import threading
from typing import Any

import requests

PROTOCOL_VERSION = "2025-06-18"


class RemoteError(RuntimeError):
    """A transport or authentication problem talking to the hosted service."""


class RemoteToolError(RuntimeError):
    """The hosted tool itself reported an error (shown to the model verbatim)."""


def remote_settings() -> tuple[str, str, str | None] | None:
    url = (os.getenv("AGENTM2M_URL") or "").strip().rstrip("/")
    if not url:
        return None
    key = (os.getenv("AGENTM2M_API_KEY") or "").strip()
    project = (os.getenv("AGENTM2M_PROJECT") or "").strip() or None
    return url, key, project


class RemoteClient:
    def __init__(self, url: str, key: str, project: str | None = None, *, timeout: float = 300.0) -> None:
        if not key:
            raise RemoteError("AGENTM2M_URL is set but AGENTM2M_API_KEY is empty: create a key on the service's "
                              "web page (Services -> API keys)")
        self.endpoint = url.rstrip("/") + ("" if url.rstrip("/").endswith("/mcp") else "/mcp")
        self.http = requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream",
                                  "Content-Type": "application/json"})
        if project:
            self.http.headers["X-AgentM2M-Project"] = project
        self.timeout = timeout
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._session: str | None = None

    # ---- JSON-RPC over streamable HTTP ----------------------------------
    def _post(self, payload: dict) -> requests.Response:
        headers = {"Mcp-Session-Id": self._session} if self._session else {}
        if self._session:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        try:
            return self.http.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RemoteError(f"cannot reach the agentm2m service at {self.endpoint}: {exc}") from None

    @staticmethod
    def _message(resp: requests.Response, rid: int) -> dict:
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("id") == rid:
                        return msg
            raise RemoteError("the service closed the stream without a response")
        return resp.json()

    def _request(self, method: str, params: dict) -> dict:
        rid = next(self._ids)
        resp = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if resp.status_code == 401:
            raise RemoteError("the agentm2m service rejected the API key (AGENTM2M_API_KEY): create a new one on its "
                              "web page (Services -> API keys)")
        if resp.status_code == 404 and self._session:
            raise _SessionLost()
        if resp.status_code >= 400:
            raise RemoteError(f"the agentm2m service answered {resp.status_code}: {resp.text[:300]}")
        msg = self._message(resp, rid)
        if "error" in msg:
            raise RemoteError(f"{method} failed: {msg['error'].get('message', msg['error'])}")
        if method == "initialize":
            self._session = resp.headers.get("mcp-session-id")
        return msg.get("result") or {}

    def _initialize(self) -> None:
        self._session = None
        self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                     "clientInfo": {"name": "agentm2m-plugin-proxy", "version": "1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            for attempt in range(2):
                try:
                    if self._session is None and attempt == 0:
                        self._initialize()
                    result = self._request("tools/call", {"name": name, "arguments": arguments})
                    break
                except _SessionLost:
                    self._initialize()
            else:  # pragma: no cover
                raise RemoteError("could not re-establish the MCP session")
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise RemoteToolError(text or "remote tool error")
        if result.get("structuredContent") is not None:
            sc = result["structuredContent"]
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        try:
            return json.loads(text)
        except ValueError:
            return text


class _SessionLost(Exception):
    pass


_client: RemoteClient | None = None
_client_key: tuple | None = None


def client() -> RemoteClient | None:
    """The process-wide client for the configured service, or None (local mode)."""
    global _client, _client_key
    s = remote_settings()
    if s is None:
        return None
    if _client is None or _client_key != s:
        _client, _client_key = RemoteClient(*s), s
    return _client
