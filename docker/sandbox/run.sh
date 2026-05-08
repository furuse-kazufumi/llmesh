#!/usr/bin/env sh
# LLMesh sandbox runner — GATE-04 compliant
#
# SECURITY DESIGN:
#   - --network=none        : No outbound network access from inside the container
#   - --read-only           : Root filesystem is read-only
#   - --tmpfs /tmp:...      : Only /tmp is writable; noexec prevents code injection
#   - --security-opt=no-new-privileges : Prevents privilege escalation via setuid/setgid
#   - --cap-drop=ALL        : All Linux capabilities are dropped
#   - --env-file /dev/null  : No environment variables are injected from the host
#
# SECRET HANDLING:
#   Do NOT pass API keys, tokens, or passwords as environment variables or
#   command-line arguments. Secrets must be injected at build time via
#   build secrets (--secret id=...) or via a read-only tmpfs secrets mount,
#   never via --env or -e flags visible in process listings.

IMAGE="${1:-llmesh-sandbox}"
shift 2>/dev/null || true

exec docker run \
  --rm \
  --network=none \
  --read-only \
  --tmpfs /tmp:size=64m,noexec,nosuid,nodev \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  --env-file /dev/null \
  "${IMAGE}" "$@"
