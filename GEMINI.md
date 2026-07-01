# Coding Agent Guide

## Reference Documentation

If you have ADK skills available, use those instead of fetching the URLs below.

Otherwise, consult these resources as needed:

| 資源名稱 (Resource) | 連結層 (GitHub Web Link) | 說明 (Description) |
| :--- | :--- | :--- |
| **ADK Cheatsheet** | [adk-cheatsheet.md](https://github.com/GoogleCloudPlatform/agent-starter-pack/blob/main/agent_starter_pack/resources/docs/adk-cheatsheet.md) | 快速查詢 Agent 定義、Tool/Callback 宣告與 Orchestration 程式碼。 |
| **Evaluation Guide** | [adk-eval-guide.md](https://github.com/GoogleCloudPlatform/agent-starter-pack/blob/main/agent_starter_pack/resources/docs/adk-eval-guide.md) | 指標打分、環境模擬與 User Simulation 的深度解密。 |
| **Deployment Guide** | [adk-deploy-guide.md](https://github.com/GoogleCloudPlatform/agent-starter-pack/blob/main/agent_starter_pack/resources/docs/adk-deploy-guide.md) | WIF 安全配置、CI/CD 觸發器與 Secret Manager 整合的最佳實踐。 |
| **Development Guide** | [development-guide.md](https://github.com/GoogleCloudPlatform/agent-starter-pack/blob/main/docs/guide/development-guide.md) | 完整開發工作流、開發階段指引與最佳實踐說明。 |
| **ADK Docs** | [adk.dev (Portal)](https://adk.dev) | 官方文件入口，提供全面概念、最新 SDK 起步說明。 |

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `make playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Start with 1-2 eval cases, run `make eval`, iterate. Expect 5-10+ iterations. See the **Evaluation Guide** for metrics, evalset schema, LLM-as-judge config, and common gotchas.

### Phase 4: Pre-Deployment Tests
Run `make test`. Fix issues until all tests pass.

### Phase 5: Deploy to Dev
**Requires explicit human approval.** Run `make deploy` only after user confirms. See the **Deployment Guide** for details.

### Phase 6: Production Deployment
Ask the user: Option A (simple single-project) or Option B (full CI/CD pipeline with `uvx agent-starter-pack setup-cicd`). See the [deployment docs](https://raw.githubusercontent.com/GoogleCloudPlatform/agent-starter-pack/refs/heads/main/docs/guide/deployment.md) for step-by-step instructions.

## Development Commands

| Command | Purpose |
|---------|---------|
| `make playground` | Interactive local testing |
| `make test` | Run unit and integration tests |
| `make eval` | Run evaluation against evalsets |
| `make eval-all` | Run all evalsets |
| `make lint` | Check code quality |
| `make tf-apply` | Set up dev infrastructure (Terraform; runs tf-init first) |
| `make deploy` | Deploy to dev |

---

## Operational Guidelines for Coding Agents

- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked. Use `gemini-3-flash-preview` or `gemini-3.1-pro-preview` for new agents.
- **Model 404 errors**: Fix `GOOGLE_CLOUD_LOCATION` (e.g., `global` instead of `us-east1`), not the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `make install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
