"""One process for the web app, the REST API and the MCP endpoint.

    /            the React site (web/dist), with the Services tab
    /api/...     REST API (OpenAPI docs at /api/docs)
    /mcp         MCP over streamable HTTP (same tools as the stdio server)

Authentication is an API key (`Authorization: Bearer am2m_...` or
`X-API-Key`), created self-service with POST /api/keys. The MCP endpoint
and the stateful /api/auto/* routes need a key; health, pricing, key
creation and the stateless checker are public. Each key gets its own
project directories under $AGENTHOT_DATA_DIR/tenants/<key id>/.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from .. import mcp_server as M
from agenthot import sandbox
from agenthot.sandbox import SandboxRefused
from autom2m.workspace import AutoWorkspace, AutoWorkspaceError, check_team_json
from agenthot.workspace import WorkspaceError
from .keys import TIERS, KeyRecord, KeyStore, enforce, key_from_headers
from .metrics import Metrics

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_TEAMS = ["devteam_proposal", "devteam_admitted_g2", "chakin_pilot", "classeval_reference"]


def _web_dist() -> Path | None:
    p = Path(os.getenv("AGENTHOT_WEB_DIST") or REPO_ROOT / "web" / "dist")
    return p if (p / "index.html").is_file() else None


def _transport_security():
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [h.strip() for h in (os.getenv("AGENTHOT_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    if not hosts:  # API keys guard the endpoint; DNS-rebinding checks are opt-in
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=[f"{s}://{h}" for h in hosts for s in ("http", "https")])


# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------


class KeyCreate(BaseModel):
    label: str = Field("", max_length=80, description="A name to recognise the key by")
    email: str = Field("", max_length=120, description="Optional contact address (not verified)")


class CheckRequest(BaseModel):
    team: dict[str, Any] | str = Field(..., description="A typed team (JSON object or JSON text)")
    naive: bool = Field(False, description="Class-level W4 (comparison only)")


class TaskRequest(BaseModel):
    source: str = Field(..., description="Python skeleton: a class with stub methods, or a stub function")
    description: str = ""


class TeamRequest(BaseModel):
    team: dict[str, Any] | str


class BindingSubmit(BaseModel):
    target_key: str
    binding: str
    value: str
    footprint_version: str


class SolveRequest(BaseModel):
    source: str | None = None


# ---------------------------------------------------------------------------
# the ASGI front: routes /mcp to the MCP app, counts requests, authenticates
# MCP calls and passes the tenant to the tools in a trusted header
# ---------------------------------------------------------------------------


class Front:
    def __init__(self, api: FastAPI, mcp_app, keys: KeyStore, metrics: Metrics) -> None:
        self.api, self.mcp_app, self.keys, self.metrics = api, mcp_app, keys, metrics

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.api(scope, receive, send)
        path = scope.get("path", "")
        # never trust a client-supplied tenant header
        headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() != M.TENANT_HEADER.encode()]
        scope = dict(scope, headers=headers)
        self.metrics.request(path)
        if path != "/mcp" and not path.startswith("/mcp/"):
            return await self.api(scope, receive, send)
        hmap = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers}
        rec = await asyncio.to_thread(self.keys.resolve, key_from_headers(hmap))
        if rec is None:
            self.metrics.denied()
            body = json.dumps({"error": "unauthorized",
                               "detail": "send an API key: Authorization: Bearer <key> (create one at /#/services/keys)"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b'Bearer realm="agenthot"')]})
            await send({"type": "http.response.body", "body": body})
            return
        enforce(rec.tier, "mcp")
        headers.append((M.TENANT_HEADER.encode(), rec.id.encode()))
        if scope.get("method") == "POST":
            receive = await self._peek_tool_calls(receive)
        await self.mcp_app(scope, receive, send)

    async def _peek_tool_calls(self, receive):
        """Buffer the JSON-RPC body to count tools/call by name, then replay it."""
        chunks, more = [], True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                return _replay([msg], receive)
            chunks.append(msg)
            more = msg.get("more_body", False)
        try:
            payload = json.loads(b"".join(m.get("body", b"") for m in chunks) or b"null")
            for item in payload if isinstance(payload, list) else [payload]:
                if isinstance(item, dict) and item.get("method") == "tools/call":
                    self.metrics.tool(str((item.get("params") or {}).get("name")))
        except (ValueError, TypeError):
            pass
        return _replay(chunks, receive)


def _replay(messages: list, receive):
    queue = list(messages)

    async def again():
        if queue:
            return queue.pop(0)
        return await receive()

    return again


# ---------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------


def create_app(*, data_dir: str | Path | None = None, web_dist: str | Path | None = None):
    if data_dir is not None:
        os.environ["AGENTHOT_DATA_DIR"] = str(data_dir)
    ddir = M.data_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    # Many API keys share this process: their code (validators execute team values)
    # must never run with the service's own privileges. Fail closed unless the
    # sandbox isolates, or the operator explicitly trusts every key holder.
    sandbox.require_isolation(os.getenv("AGENTHOT_ALLOW_UNSANDBOXED_EXEC", "") != "1")
    keys = KeyStore(ddir / "service.db")
    metrics = Metrics()
    mcp_app = M.mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=_transport_security())

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    api = FastAPI(
        title="AutoM2M service",
        version=__version__,
        summary="AgentHOT + AutoM2M as a service: typed-team checker (W1–W6), host-mode builder loop, "
                "team runtime. The same operations are available as MCP tools at /mcp.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    api.state.keys, api.state.metrics = keys, metrics

    bearer = HTTPBearer(auto_error=False, description="API key from POST /api/keys")
    xkey = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_key(cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                    x: str | None = Depends(xkey)) -> KeyRecord:
        rec = keys.resolve((cred.credentials if cred else None) or x)
        if rec is None:
            metrics.denied()
            raise HTTPException(401, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"})
        return rec

    def project_ws(rec: KeyRecord = Depends(require_key),
                   x_agenthot_project: str | None = Header(None, description="Project name (default: default)"),
                   project: str | None = Query(None, description="Project name (overrides the header)")) -> AutoWorkspace:
        try:
            d = M.tenant_project_dir(rec.id, project or x_agenthot_project)
        except Exception as exc:  # noqa: BLE001  (ToolError from the validator)
            raise HTTPException(400, str(exc)) from None
        return M.auto_workspace(d)

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SandboxRefused as exc:
            raise HTTPException(403, str(exc)) from None
        except (AutoWorkspaceError, WorkspaceError) as exc:
            raise HTTPException(409, str(exc)) from None
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            metrics.error("api", f"{type(exc).__name__}: {exc}")
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from None

    # ---- public ----------------------------------------------------------
    @api.get("/api/health", tags=["service"], summary="Service health, MCP tool list and counters")
    async def health():
        tools = [t.name for t in await M.mcp.list_tools()]
        return {
            "status": "ok",
            "service": "autom2m",
            "version": __version__,
            "started_at": metrics.started,
            "uptime_s": round(time.time() - metrics.started, 1),
            "mcp": {"endpoint": "/mcp", "transport": "streamable-http", "auth": "Authorization: Bearer <api key>",
                    "tools": tools, "tool_count": len(tools)},
            "llm": {"mode": "host", "solve_backend": M.solve_backend()},
            "execution": sandbox.execution_status(),
            "keys": keys.counts(),
            "metrics": metrics.snapshot(),
            "web": _web_dist() is not None,
        }

    @api.get("/api/pricing", tags=["service"], summary="Plans and what each includes")
    def pricing():
        return {"tiers": TIERS, "note": "Research preview: every feature is free and unlimited for now."}

    @api.post("/api/keys", tags=["keys"], status_code=201, summary="Create an API key (shown once)")
    def create_key(body: KeyCreate):
        rec, key = keys.create(body.label.strip(), body.email.strip())
        return {"key": key, "record": rec.public(),
                "note": "Store the key now: it is shown only once (only its hash is kept)."}

    @api.get("/api/keys/me", tags=["keys"], summary="The presented key: tier, usage")
    def key_me(rec: KeyRecord = Depends(require_key)):
        return rec.public()

    @api.delete("/api/keys/me", tags=["keys"], summary="Revoke the presented key")
    def revoke_me(rec: KeyRecord = Depends(require_key)):
        keys.revoke(rec.id)
        return {"revoked": True, "id": rec.id}

    @api.post("/api/auto/check", tags=["autom2m"], summary="Run the W1–W6 checker on a typed team (stateless)")
    def auto_check(body: CheckRequest):
        """W1–W6 on a typed team. Stateless and public (used by the Playground)."""
        return call(check_team_json, body.team, naive=body.naive)

    @api.get("/api/teams/samples", tags=["autom2m"], summary="Sample typed teams")
    def samples():
        out = []
        for name in SAMPLE_TEAMS:
            p = REPO_ROOT / "teams" / f"{name}.json"
            if p.is_file():
                out.append({"name": name, "team": json.loads(p.read_text())})
        return {"samples": out}

    # ---- keyed: AutoM2M step by step (mirrors the auto_* MCP tools) -----
    @api.post("/api/auto/task", tags=["autom2m"], summary="Set the task from a Python skeleton")
    def auto_task(body: TaskRequest, ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.set_task, body.source, body.description)

    @api.get("/api/auto/propose", tags=["autom2m"], summary="Builder prompt: propose, revise or delta")
    def auto_propose(mode: str = "auto", ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.builder_prompt, mode)

    @api.post("/api/auto/team", tags=["autom2m"], summary="Submit a typed team: checked, then admitted or applied as a delta")
    def auto_team(body: TeamRequest, ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.submit_team, body.team)

    @api.post("/api/auto/run", tags=["autom2m"], summary="Run to a fixpoint and evaluate φ")
    def auto_run(ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.run)

    @api.get("/api/auto/bindings", tags=["autom2m"], summary="Next values to fill, with footprint-bounded prompts")
    def auto_bindings(agent: str | None = None, limit: int = 5, ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.next_bindings, agent, limit)

    @api.post("/api/auto/bindings", tags=["autom2m"], summary="Submit one value; validators decide")
    def auto_submit(body: BindingSubmit, ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.submit_binding, body.target_key, body.binding, body.value, body.footprint_version)

    @api.get("/api/auto/status", tags=["autom2m"], summary="Task, team, φ, pending values and history")
    def auto_status(ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.status)

    @api.post("/api/auto/attribute", tags=["autom2m"], summary="Locate (and classify) φ failures")
    def auto_attribute(limit: int = 2, ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.attribute, limit)

    @api.get("/api/auto/deliverable", tags=["autom2m"], summary="Assembled code and accepted methods")
    def auto_deliverable(ws: AutoWorkspace = Depends(project_ws)):
        return call(ws.deliverable)

    @api.post("/api/auto/solve", tags=["autom2m"], summary="Whole loop unattended (needs a server LLM)")
    def auto_solve(body: SolveRequest, ws: AutoWorkspace = Depends(project_ws)):
        backend = M.solve_backend()
        if backend is None:
            raise HTTPException(501, "no engine LLM configured on this server (AGENTHOT_SOLVE_LLM); use host mode")
        return call(AutoWorkspace(ws.project_dir, backend=backend).solve, body.source)

    @api.delete("/api/auto", tags=["autom2m"], summary="Delete the project's AutoM2M state")
    def auto_reset(ws: AutoWorkspace = Depends(project_ws)):
        M._auto.pop(str(ws.project_dir), None)
        return call(ws.reset)

    @api.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        dist = _web_dist()
        if dist is not None:
            from fastapi.responses import FileResponse

            return FileResponse(dist / "index.html")
        return JSONResponse({"detail": "not found"}, status_code=404)

    dist = Path(web_dist) if web_dist else _web_dist()
    if dist is not None and (dist / "index.html").is_file():
        api.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:
        @api.get("/", include_in_schema=False)
        def root():
            return {"service": "autom2m", "version": __version__, "docs": "/api/docs", "mcp": "/mcp",
                    "web": "not built: run `npm run build` in web/"}

    return Front(api, mcp_app, keys, metrics)


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="autom2m serve", description="Run the web app, REST API and MCP endpoint")
    ap.add_argument("--host", default=os.getenv("AGENTHOT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("AGENTHOT_PORT", "8765")))
    ap.add_argument("--data-dir", default=None, help="keys database and tenant projects (default ~/.autom2m-service)")
    ap.add_argument("--web-dist", default=None, help="built web app (default web/dist)")
    a = ap.parse_args(argv)
    app = create_app(data_dir=a.data_dir, web_dist=a.web_dist)
    print(f"autom2m service {__version__}: http://{a.host}:{a.port}/  (MCP: /mcp, API docs: /api/docs)")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
