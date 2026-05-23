# llmesh — Project Instructions

> secure LLM hub (on-prem MCP server)。複数 LLM (OpenAI/Anthropic/Ollama/Qwen 等) を
> 統合し MCP プロトコルで配信。SPC (統計的工程管理) 内蔵、MQTT/OPC-UA で産業 IoT 直結。
> FullSense ファミリーの一員。

このファイルは Claude Code 等の AI 実装支援環境に対する指示書。

## FullSense プロジェクト優先度 (全 proj 共通)

本プロジェクトは FullSense (umbrella: llmesh / llive / llove + portal/記事) の構成要素。
全プロジェクト横断の優先度は **FullSense > llive > llmesh > llove > その他**
(2026-05-23 ユーザー確定)。FullSense=全 proj マスター進捗。進捗把握が曖昧な場合は
**FullSense 側を優先** (単一の真実)。プロジェクト間の結合 (要素統合) 判断はユーザーが
行い、勝手に結合しない。単一ソース: raptor `claude-projects.json` の `_priority` /
memory `feedback_fullsense_project_priority`。

## Project Identity

- **Name**: llmesh
- **PyPI**: `llmesh`
- **Path**: `D:/projects/llmesh/`
- **役割 (FullSense 内)**: 表現汎用層 (RepIR = LLVM-for-expression, typed representation
  over MCP) + near-real-time E2E の主担当。FullSense 実装キュー #1 (RepIR PoC) /
  #2 (予測符号化 push) を担う。

> product 固有の開発規約・アーキテクチャ詳細は今後ここに追記する (現状は優先度周知のみ)。
