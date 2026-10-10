# The AutoM2M service: web app, REST API and MCP endpoint in one process.
#   docker compose up --build      ->  http://localhost:8765  (MCP: /mcp, API docs: /api/docs)

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
# bubblewrap isolates every validator run (no network, no view of /data or other tenants)
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AGENTHOT_DATA_DIR=/data AGENTHOT_LLM=host AGENTHOT_SANDBOX=bwrap
WORKDIR /app
# editable install: the service finds teams/ and web/dist next to the sources
COPY pyproject.toml README.md ./
COPY src/ src/
COPY teams/ teams/
RUN pip install --no-cache-dir -e ".[serve]"
COPY --from=web /web/dist web/dist
RUN useradd --create-home --uid 10001 agenthot && mkdir -p /data && chown agenthot /data
USER agenthot
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health').status == 200 else 1)"
CMD ["agenthot", "serve", "--host", "0.0.0.0", "--port", "8765"]
