---
layout: default
title: "FullSense ™ — llmesh"
description: "Secure LLM Mesh over MCP"
nav_order: 1
---

# FullSense ™ — llmesh

> **ざっくり何の文書？（中学生にもわかる説明）**
> このページは、いろいろな AI（人工知能）を 1 か所にまとめて安全に使うための道具「llmesh」の、案内地図のようなものです。会社の中の秘密や個人の情報を外に出さずに、自分のパソコンや社内のサーバーだけで AI を動かす仕組みを目指しています。下の表やリンクから、もっとくわしい説明のページへ進めます。
> 専門用語の意味は [用語集（GLOSSARY.md）](GLOSSARY.md) にまとめてあります。

---

> **Part of the [FullSense ™](https://github.com/furuse-kazufumi/llive/blob/main/TRADEMARK.md) family** — **llmesh** ・ `llive` ・ `llove` の 3 製品で構成される FullSense ブランドの中で、本サイトは **llmesh (secure LLM ハブ（LLM hub） over MCP)** の公式 documentation です。

---

## FullSense Family

```mermaid
flowchart TD
    F["<b>FullSense ™</b><br/>umbrella brand &amp; spec"]
    LM["<b>llmesh</b><br/>secure LLM hub<br/>(on-prem MCP)"]
    LI["<b>llive</b><br/>self-evolving memory"]
    LO["<b>llove</b><br/>TUI dashboard"]
    F --> LM
    F --> LI
    F --> LO
    LM <-.->|MCP| LI
    LM <-.->|hub| LO
    style F fill:#fef3c7,stroke:#f59e0b,stroke-width:2px
    style LM fill:#dbeafe,stroke:#3b82f6,stroke-width:2px
```

| Product   | Role                                       | Site                                                |
|-----------|--------------------------------------------|-----------------------------------------------------|
| **llmesh** | secure LLM hub / on-prem MCP server        | this site                                          |
| **llive**  | self-evolving modular memory LLM framework | <https://furuse-kazufumi.github.io/llive/>          |
| **llove**  | TUI dashboard / HITL workbench             | <https://furuse-kazufumi.github.io/llove/>          |

## Architecture — Secure LLM Hub (MCP) Topology

llmesh は **on-prem MCP server** として複数の LLM client (Claude Desktop / LM Studio / Open WebUI / Cursor) を 1 つの hub に集約し、プライバシーフィルタ + 監査チェーンを挟む。

```mermaid
flowchart LR
    CD["Claude Desktop"]
    LMS["LM Studio"]
    OWUI["Open WebUI"]
    CUR["Cursor"]

    subgraph HUB["llmesh hub<br/>(on-prem)"]
        MCP["MCP server"]
        PRIV["Privacy<br/>Filter"]
        AUD["Audit<br/>Chain<br/>(SHA-256)"]
    end

    BE1["LLM backend<br/>(Ollama)"]
    BE2["LLM backend<br/>(OpenAI API)"]
    BE3["LLM backend<br/>(Anthropic API)"]

    CD --> MCP
    LMS --> MCP
    OWUI --> MCP
    CUR --> MCP
    MCP --> PRIV
    PRIV --> AUD
    AUD --> BE1
    AUD --> BE2
    AUD --> BE3

    style HUB fill:#dbeafe,stroke:#3b82f6,stroke-width:2px
    style PRIV fill:#fee2e2,stroke:#ef4444
    style AUD fill:#e0e7ff,stroke:#6366f1
```

## Quick Start

```bash
pip install llmesh
```

詳細は [README.md](https://github.com/furuse-kazufumi/llmesh#readme) を参照。

## Documentation

| Topic               | File                                                  |
|---------------------|-------------------------------------------------------|
| Architecture        | [ARCHITECTURE.md](ARCHITECTURE.md)                    |
| API stability       | [API_STABILITY.md](API_STABILITY.md)                  |
| Deployment          | [DEPLOYMENT.md](DEPLOYMENT.md)                        |
| Development         | [DEVELOPMENT.md](DEVELOPMENT.md)                      |
| Glossary            | [GLOSSARY.md](GLOSSARY.md)                            |
| Industrial guide    | [INDUSTRIAL_GUIDE.md](INDUSTRIAL_GUIDE.md)            |
| Migration           | [MIGRATION.md](MIGRATION.md)                          |
| Observability       | [OBSERVABILITY.md](OBSERVABILITY.md)                  |
| Peering             | [PEERING.md](PEERING.md)                              |
| Performance         | [PERFORMANCE.md](PERFORMANCE.md)                      |
| Platforms           | [PLATFORMS.md](PLATFORMS.md)                          |
| Requirements        | [REQUIREMENTS.md](REQUIREMENTS.md)                    |
| Roadmap             | [ROADMAP.md](ROADMAP.md)                              |
| Security model      | [SECURITY.md](SECURITY.md)                            |
| Setup               | [SETUP.md](SETUP.md)                                  |
| Changelog           | [CHANGELOG.md](CHANGELOG.md)                          |

## Links

- **GitHub**: <https://github.com/furuse-kazufumi/llmesh>
- **PyPI**: <https://pypi.org/project/llmesh/>
- **Contact**: `kazufumi@furuse.work`

---

*FullSense ™ / llmesh ™ are trademarks of Kazufumi Furuse. Code distributed under Apache-2.0.*
