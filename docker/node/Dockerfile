# LLMesh node image — lightweight MCP node for PoC swarm
#
# Security: non-root, minimal ENV, no capabilities at runtime

FROM python:3.11-slim

RUN useradd --no-create-home --shell /bin/false --uid 65533 llmesh

WORKDIR /app
COPY --chown=llmesh:llmesh . /app

RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir -e . 2>/dev/null || true && \
    pip install --no-cache-dir fastapi uvicorn[standard]

USER llmesh

# Only expose node role and port — no secrets in ENV
ENV PATH="/usr/local/bin:/usr/bin:/bin"

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "llmesh.mcp.server:app", "--host", "0.0.0.0"]
CMD ["--port", "8000"]
