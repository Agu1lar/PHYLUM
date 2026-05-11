[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

Agente Operacional Desktop — Visão Geral

Resumo
-------
Agente Operacional Desktop é uma plataforma modular para executar e automatizar tarefas no desktop do usuário. Fornece:
- FastAPI backend + LangGraph-like execution graph
- Nodes desacoplados (Planner, Safety, Shell, Filesystem, Browser, Memory, Reflection)
- Tools modularizadas (Shell, FileSystem, Browser, Package Manager, OS, Memory)
- Agents especializados (FileSystemAgent, BrowserAgent, OS Inspector)
- Frontend desktop (Tauri + React + TypeScript + Tailwind) com websocket
- Persistência leve (aiosqlite) para estado, aprovações e histórico

Arquitetura (alto nível)
-----------------------
Frontend (Tauri/React)
  ↕ WebSocket / HTTP
FastAPI (API / WebSocket)
  ↕ LangGraph Runtime (StateGraph)
Execution Engine
  ├─ Planner Node
  ├─ Safety Node
  ├─ Shell Node (ShellExecutor)
  ├─ FileSystem Node (FileSystemAgent / tools)
  ├─ Browser Node (BrowserAgent / Playwright)
  ├─ Memory Node (Persistence)
  └─ Reflection Node (validation & retry recommendations)

Principais Características
-------------------------
- Schema-typed tools (Pydantic), validação, retries, timeout, logs
- Shell executor seguro para Windows (asyncio.create_subprocess_exec, UAC wrapper)
- Playwright-driven BrowserAgent (isolated contexts, monitored downloads)
- Filesystem agent com sandbox, quarantine e rollback
- OS introspection usando psutil, WMI, PowerShell
- Planner Agent: transforma NL em plano de tasks estruturadas
- Reflection Node: valida execução, checa estado e recomenda retries
- Frontend moderno com chat, logs, tasks, approvals, terminal e tema escuro

Como rodar (desenvolvimento)
---------------------------
1) Backend
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   (instale navegadores Playwright) .venv\Scripts\python.exe -m playwright install
   uvicorn app_main:app --reload --host 127.0.0.1 --port 8000

2) Frontend
   cd frontend
   npm install
   npm run dev
   (ou: tauri dev após instalar Rust & Tauri deps)

3) Testar
   - Use endpoints: POST /run (submete tarefa), GET /state/{id}
   - WebSocket ws://127.0.0.1:8000/ws para updates

Segurança e Operações
---------------------
- Shell executor inclui blacklist, whitelist opcional, mandatory timeout, retries e cancelamento
- Filesystem ops usam quarantine, protected-path checks e rollback
- Playwright runs in isolated contexts and closes browsers per task
- Persistence (aiosqlite) stores approvals and histories — secure storage recommended for secrets

O que falta / melhorias recomendadas (priorizadas)
-------------------------------------------------
1) Testes (alto):
   - Unit tests (pytest + asyncio) para tools, nodes, agents
   - Integration tests: end-to-end graph runs, Playwright flows, filesystem ops
2) Segurança (alto):
   - Secret management (encrypted vault or OS keyring)
   - Harden Shell whitelist/blacklist and add policy engine for approvals
   - Code signing & secure update for Tauri app
3) Reliability / Observability (alto):
   - Metrics (Prometheus) + structured logs (JSON) + distributed traces
   - Health checks and liveness endpoints
4) LangGraph integration (medium):
   - Replace fallback StateGraph with official LangGraph runtime and formal node registration
   - Use typed edges, conditional transitions and retries integrated with engine
5) Concurrency & Robustness (medium):
   - Locking for persistence writes, transaction semantics for sequences
   - Improve ExecutionEngine to honor reflection recommendations automatically and cap retries
6) UX / Frontend (medium):
   - Auth and role-based approvals UI, approval flows and push notifications
   - Terminal: PTY-backed remote shell with strict permission checks
7) Packaging & CI (medium):
   - CI pipelines (lint, test, build) and release pipelines for the Tauri app
   - Installer generation for Windows (MSI) and auto-updater implementation
8) Platform coverage (low→medium):
   - Extend secure shell executor and filesystem agent support to Linux/macOS
   - Abstract Playwright download paths and use OS-appropriate quarantine
9) Advanced agent features (low→medium):
   - LLM-assisted planner (prompt templates + safety review loop)
   - Policy language for safety node (Rego or DSL)
   - Plugin system for third-party tools

Roadmap (next sprints)
----------------------
Sprint 1 (setup): tests baseline + CI, wire frontend WebSocket + backend integration, add unit tests for ShellExecutor and FileSystemTool
Sprint 2 (security): secret store, approval workflows, policy engine skeleton
Sprint 3 (stability): LangGraph runtime integration, execution engine improvements, metrics
Sprint 4 (UX & packaging): finalize Tauri packaging, add auto-update, refine UI

Arquivos principais (onde olhar)
-------------------------------
- app_main.py — FastAPI glue & graph builder
- agent/ (persistence.py, executor.py, tool_call.py)
- nodes_*.py — node implementations (planner, safety, shell, reflection, filesystem, browser)
- tools/ — modular tools (shell_tool, filesystem_tool, browser_tool, package_tool, os_tool, memory_tool)
- fs_agent.py, browser_agent.py, os_inspect_agent.py — agents
- frontend/ — Tauri + React UI scaffold

Contribuindo
------------
- Abra issues descritivos (bug / feature)
- Siga o padrão: model (Pydantic) → tool (async) → node → agent
- Escreva testes para cada mudança

Licença
-------
Por padrão este scaffold não inclui licença. Adicione LICENSE (MIT/Apache) conforme política do seu projeto.

Backend WebSocket usage example
- Connect to ws://127.0.0.1:8000/ws to receive run events.
- Example: run filesystem write via HTTP and observe events over WS:

POST /run/tool/filesystem
{
  "action": "write",
  "path": "C:\\temp\\example.txt",
  "content": "hello"
}

The WS will receive JSON events: tool_started, tool_finished, tool_failed.


Arquivo criado:
  C:\Users\User\Documents\AgenteDesktop\README_PROJECT.md

Se desejar, atualizo o README.md principal com um resumo reduzido, ou gero as issues iniciais e testes base (pytest) automaticamente.