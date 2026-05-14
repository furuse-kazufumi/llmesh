# Running industrial IoT and LLMs in the same frame — llmesh

> Designing and shipping `llmesh-mcp` v3.1.0 — a secure LLM mesh over MCP.
> I'm building it to **understand how to actually deploy AI** and to **anchor my engineering career** around hard, less-glamorous problems.

## Why I started

Most LLM conversations assume **cloud-first, chat-first**. The shop floor, the substation, the hospital, and the trading desk live under a completely different set of constraints:

- Data may not leave the org boundary.
- Every action needs an audit trail.
- The world speaks Modbus, OPC-UA, MQTT, Serial, EtherCAT, BACnet — not REST.

`llmesh` is my personal attempt to bridge that gap **inside a single framework**. Cloud LLMs (OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek) and local LLMs (Ollama / llama.cpp) sit behind the same ABC, while industrial protocols are first-class from day one.

## The 8 design pillars

1. **Cross-protocol** — Modbus / OPC-UA / MQTT / Serial / EtherCAT / BACnet / HTTP(S) / WebSocket / gRPC / Email / SSH / FTP / SNMP / NTP, all unified.
2. **Cloud and local LLMs through one ABC** — identical call surface for chat, tool calls, streaming, JSON mode.
3. **MCP-compliant** — agents on top stay vendor-neutral via Anthropic's Model Context Protocol.
4. **Privacy pipeline** — a 4-layer filter (PII detect / mask / consent / audit) you cannot accidentally bypass.
5. **TimelineStore** — sensors / SPC / RAG / audit / trace recorded as a 5-tuple (`task_id, node_id, event_type, timestamp_utc, metadata`). The visualisation side (`llove`) reads, never writes.
6. **Trusted Peers + mTLS** — peer-by-name allow-list, designed to live inside an internal network.
7. **6× via Rust extensions** — performance-critical hot paths drop to Rust; Python ergonomics stay intact.
8. **Clean under OWASP static audit** — zero `shell=True` / `pickle` / `eval` / SQL injection / weak crypto. Every HTTP client has size limits. SemVer is fully enforced.

## Why this matters for my career

The LLM hype cycle keeps the spotlight on flashy demos, but **what actually stops products are the boring constraints**. Building `llmesh` left me with implementation-grounded talking points instead of buzzwords:

- I can decide, at design level, what to **keep, replace, or drop** when cloud LLMs are not allowed.
- I established an integration pattern where **industrial protocols and LLM events share the same time series**, which makes the system observable.
- I gained real experience splitting **Rust and Python by responsibility** (PyO3 + hot-path profiling + 5× gate).
- I held **OWASP-clean static analysis** + SemVer as a hard rule from day one — uncommon in LLM OSS.

These are the kinds of skills regulated-industry, infrastructure, manufacturing, and enterprise-SI AI teams are forced to ask about.

## Where it stands today (2026-05-14)

- **v3.1.0** — Secure LLM Mesh over MCP. 117 chapters / 500+ requirements / 2300+ tests passing.
- OWASP-clean static audit; SemVer formally adopted (`docs/API_STABILITY.md` is the public-symbol contract).
- **6×** speedup via Rust extensions.
- Family: backend `llmesh` / TUI dashboard `llove` / self-evolving LLM `llive` / one-shot install `pip install llmesh-suite` (in prep).
- PyPI: `pip install llmesh-mcp`.

## Where it's going

`llmesh` aims to be **a reference implementation engineers can argue from** when they push AI adoption into regulated environments. Pair it with `llove` (TUI dashboard) and `llive` (self-evolving modular-memory LLM), and you get an **LLM × industrial-IoT stack** that stays off the cloud, preserves audit trails, and is observable on-site.

> GitHub: <https://github.com/furuse-kazufumi/llmesh>
> PyPI: `pip install llmesh-mcp`

#AI #LLM #IndustrialIoT #MCP #ModelContextProtocol #MLOps #Rust #OpenSource #IndieHacker #Career
