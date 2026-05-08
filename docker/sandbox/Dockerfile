# LLMesh Sandbox — GATE-04 compliant execution environment
#
# Security properties:
#   - Non-root user (sandbox, UID 65532)
#   - Read-only root filesystem; writable tmpfs at /tmp only
#   - No capabilities (--cap-drop=ALL at runtime)
#   - no-new-privileges seccomp enforcement
#   - Minimal ENV (PATH only — no secrets, no tokens)
#   - Network disabled at runtime (--network=none)

FROM python:3.11-slim

# Create a non-root user with no home directory and no login shell
RUN useradd --no-create-home --shell /bin/false --uid 65532 sandbox

# Install dependencies as root before switching user
WORKDIR /sandbox
COPY --chown=sandbox:sandbox . /sandbox

RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir -e . 2>/dev/null || true

# Drop to non-root for all subsequent layers and at runtime
USER sandbox

# Minimal environment — no secrets, no tokens, PATH only
ENV PATH="/usr/local/bin:/usr/bin:/bin"

# Entrypoint runs pytest by default; override with docker run args
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["--tb=short", "-q"]
