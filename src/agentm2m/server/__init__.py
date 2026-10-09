"""The hosted agentm2m service: web app, REST API and the MCP endpoint in one
process (`agentm2m serve`)."""
from .app import create_app

__all__ = ["create_app"]
