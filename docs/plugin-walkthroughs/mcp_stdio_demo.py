"""Speak MCP (JSON-RPC over stdio) to autom2m-mcp by hand.

Usage: python mcp_stdio_demo.py <empty project dir>   (needs autom2m-mcp on PATH, e.g. the repo venv)
"""
import json, subprocess, sys, os
env = dict(os.environ, AGENTHOT_LLM="host", AGENTHOT_PROJECT_DIR=sys.argv[1])
p = subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "autom2m-mcp")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
def send(m):
    p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
def recv():
    return json.loads(p.stdout.readline())
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo","version":"1"}}})
r = recv(); print("INIT", json.dumps(r)[:1500])
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
r = recv(); tools = r["result"]["tools"]; print("NTOOLS", len(tools), [t["name"] for t in tools])
print("SUBMIT", json.dumps(next(t for t in tools if t["name"]=="submit_binding"), indent=1))
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"team_init","arguments":{"template":"devteam"}}})
r = recv(); print("CALLKEYS", list(r["result"].keys()))
send({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"model_edit","arguments":{"view":"Req","as_agent":"Architect","ops":[{"op":"delete","key":"UserStory#S3"}]}}})
r = recv(); print("ERR", json.dumps(r)[:1200])
p.terminate()
