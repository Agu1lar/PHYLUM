<!-- Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or any later version. -->

# PHYLUM

**Local-first, open-source agentic runtime for Windows.**
**Your hardware, your AI, your control.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

PHYLUM is an agentic runtime that runs entirely on your machine. No data leaves your environment. You choose the LLM provider, you control the credentials, you decide what the agent can do. Open-source under GPLv3 — free to use, modify and distribute.

---

## Why PHYLUM?

- **Local-first**: everything runs on your hardware. Your data never leaves your machine.
- **Open-source (GPLv3)**: open, auditable code with no vendor lock-in.
- **You choose the LLM**: Anthropic, OpenAI, Gemini, OpenRouter or any compatible provider. Bring your own API key.
- **Zero telemetry**: no tracking, no analytics, no phone-home.
- **Real autonomy**: the agent plans, executes, reflects, recovers from failures and learns — without relying on proprietary cloud services.

---

## Overview

PHYLUM receives natural language instructions and executes them in the user's Windows environment. It is not a simple "tool runner" — it is a full agentic runtime that plans, executes, reflects, recovers from failures and learns from past experiences.

The agent operates with a cognitive cycle:

```
planner -> safety -> tool router -> execution -> reflection -> recovery (if needed)
```

The entire cycle runs as a **directed state graph** with 13 node types and conditional transitions, enabling precise recovery to the exact node where a failure occurred.

---

## Tech stack

| Layer | Technologies |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, aiosqlite |
| Frontend | React, Vite, WebSocket |
| LLM Integration | Multi-provider (OpenAI, Anthropic, Gemini), native tool-calling |
| Windows Automation | pywinauto, pywin32, WMI, win32com (COM) |
| Web Automation | Playwright |
| Document Processing | pypdf, PyMuPDF, python-docx, openpyxl, extract-msg, Pillow, pytesseract |
| Persistence | SQLite (via aiosqlite), JSON KV store |
| Vector DB | LanceDB (embedded, serverless) |
| Validation | Pydantic |
| Tests | pytest, pytest-asyncio (1200+ tests across 49 suites) |

---

## Technologies implemented in the runtime

These capabilities have graduated from the roadmap and are part of PHYLUM's current base:

- **State Graph with subtask DAG**: the pipeline is driven by a state graph and, within the local runtime, subtasks use a real dependency graph via `depends_on`, with cycle detection, parallel branches, safe speculative execution for reads and partial completion.
- **Reasoning/execution layer separation**: `CognitiveLayer` handles planning, LLM loop and strategy decisions; `OperationalLayer` handles task graph, recovery and graph executors; `ExecutionLayer` runs tools, safety, reflection and desktop automation; `StateLayer` handles persistence, World Model, Strategy Memory, durable queue and sessions.
- **Parallel tool calls**: when the LLM emits multiple independent tool calls in the same turn, the agentic loop executes them in parallel via `asyncio.gather`, preserving the original order of results in the message history.
- **Parallel sub-agents**: `subagent.run_parallel_branches` creates isolated branches with a specific objective, its own budget for steps/timeout/tools/tokens/cost, result merging and cascading cancellation when a branch satisfies the objective.
- **Anthropic extended thinking**: Claude models with thinking receive `thinking: adaptive`, extended timeout, thinking block persistence and `agent_thinking` events for observability.
- **Context window management**: `ContextWindowManager` compresses old tool results, preserves paths/URLs/numbers/status/IDs, keeps the recent window intact and applies emergency drop when needed.
- **Web research as autonomous discovery**: the agent uses `web.search_web` as an internal learning tool when it doesn't know a technique, prioritizes official sources/Microsoft Learn/StackOverflow and caches results as `web_resource` in the World Model.
- **Prompt/tool caching**: system prompt, tool definitions and provider-aware payloads are cached in-process; Anthropic uses `cache_control=ephemeral`.
- **Explicit event bus**: first-class async pub/sub (`EventBus`) with 30+ typed events, wildcard subscriptions, history buffer and concurrent handler dispatch. State transitions, tool calls, approvals, recoveries and fallbacks are all events.
- **Robust semantic verification**: `GoalVerifier` checks if the objective was actually achieved; `SemanticValidator` checks if the result makes sense in context; `PostconditionChecker` confirms side effects of mutations (file exists after write, etc.).
- **Execution economics**: `CostTracker` accumulates tokens/time/cost per run with pricing for 15+ models; `PathComplexityAnalyzer` scores tool/step/retry/replan/error complexity; `StoppingHeuristics` decides when to stop based on budget, errors, diminishing returns and confidence; `RouteOptimizer` picks the most efficient path from historical data.
- **Per-run cost budget with hard stop**: each agentic loop run has a USD budget (default $0.25) and token budget (default 80k tokens). If the budget is exceeded mid-run, the loop halts gracefully with a summary of completed work instead of silently spending more.
- **Pluggable embedding models**: `EmbeddingProvider` abstraction with `FeatureHashProvider` (default, offline, deterministic) and `SentenceTransformerProvider` (all-MiniLM-L6-v2) with lazy loading and automatic fallback.
- **Hybrid re-ranking (BM25 + vector)**: full Okapi BM25 scorer with inverted index, combined with vector similarity via reciprocal rank fusion (RRF) for both strategy and entity search.
- **Incremental batch indexing**: `batch_upsert_strategies()` and `batch_upsert_entities()` for efficient bulk updates to the semantic index with configurable batch sizes.
- **Visual perception and hybrid computer-use**: screenshot capture and OCR, visual element detection, visual grounding (maps OCR detections to UIA selectors), coordinate-based mouse/keyboard fallback with verified bounding boxes, post-action visual verification (before/after comparison, modal/spinner/error detection), visual run replay with redacted screenshots and action annotations, and anti-fragility policy (prefer native API, use visual only when UIA/COM/DOM fail).
- **Operational skills library**: `SkillManifest` (name, version, semver, permissions, I/O schema, risk descriptors), `SkillRegistry` (persistent on-disk, import/export), `SkillRunner` (sandbox execution with capability declaration and integrity verification).
- **Persistent codebase map**: `CodebaseMap` with AST-based Python scanner, regex-based JS/TS/config scanner, SQLite-backed storage, incremental/full scan, and query API for symbols, imports, routes, tests, configs and ownership per file.
- **Test diagnostic loop**: `TestDiagnosticLoop` orchestrates run → interpret → patch → rerun → expand cycle with `TestRunner`, `FailureInterpreter` and `RegressionExpander` for pytest/jest.
- **Patch planner**: `PatchPlanner` decomposes large changes by files/owners with risk scoring (`RiskAssessor`), topological ordering (`DependencyOrderer` via Kahn's algorithm) and CODEOWNERS integration (`OwnerResolver`).
- **Heartbeat and incremental progress**: `HeartbeatEmitter` for periodic async heartbeats during long tools, `ProgressTracker` for multi-phase progress with ETA estimation, both integrated with the event bus.
- **Process watchdog**: `ProcessWatchdog` and `FrozenWindowDetector` monitor processes/windows for unresponsiveness via Win32 `IsHungAppWindow`/`SendMessageTimeout`, with configurable recovery actions (retry_message, close_graceful, kill, restart).
- **Contextual boosting**: search results are re-ranked by task domain (e.g. "office" boosts `document_alias` by 0.25 over `share`/`device`), with a configurable boost map for 11 task categories.
- **Cross-reference entity linking**: entities link to related entities (e.g. `app_path` → selectors → `document_alias`) with bidirectional/unidirectional links, relation labels, and `find_cross_references()` with configurable depth traversal.
- **UI operation lock**: `UIOperationLock` async mutex serialises all mouse/keyboard/clipboard operations; `CursorGuard` saves/restores cursor position; `InterferenceDetector` detects user mouse movement and focus changes; optional `InputGuard` blocks user input during critical sequences with safety timeout.
- **Hung process reaper**: when a tool times out on a frozen app (e.g. Excel COM hang), the reaper confirms the process is hung via `IsHungAppWindow`, kills it with `taskkill /F` to free the blocked thread, and emits a `process_reaped` event.
- **LLM API retry with backoff**: transient errors (HTTP 429/500/502/503/529) and connection failures are retried up to 3 times with exponential backoff, `Retry-After` header parsing, and structured `LLMApiError` on exhaustion.
- **Agentic pipeline fallback**: if the LLM-driven agentic pipeline fails (API error, parsing error), the system automatically falls back to the local heuristic pipeline, preserving any work already completed by the agentic pipeline.
- **Tool result compaction**: `_compact_tool_result()` strips binary data, caps stdout/stderr at 1500 chars, removes internal diagnostic fields, and limits total tool result size to 3000 chars before sending to the LLM.
- **Compact system prompt**: the system prompt was reduced by 82% (from ~1933 tokens to ~357 tokens), focusing on actionable directives rather than verbose examples. Includes a hint for `ConvertTo-Json` over `Format-Table` to save output tokens.
- **Anthropic message merging**: consecutive tool results are merged into a single `user` message with multiple `tool_result` blocks, preventing HTTP 400 errors from Anthropic's alternating-role requirement.
- **Automatic dependency management**: when a sandbox script fails with `ModuleNotFoundError`/`ImportError`, the system detects the missing package (with a mapping of 25+ known module→package pairs like `cv2`→`opencv-python`, `PIL`→`Pillow`), routes through the safety approval node (`approval_mode: single`), installs via `pip`, and retries the original script — all without wasting an LLM step.

---

## Architecture

### Main flow

1. The user sends an instruction via the UI or API
2. The **RuntimeManager** creates the run and persists the initial state
3. The **PlannerAgent** decomposes the instruction into tasks using the canonical catalog (or ordered phases for complex goals)
4. The **SafetyNode** classifies risk and decides: `allow`, `require_approval` or `deny`
5. The **ToolRouterNode** executes the actual tool
6. The **ReflectionNode** generates a semantic summary of the action
7. The **RecoveryEngine** decides: retry, human handoff, replanning, alternative script or terminal failure
8. The **ExecutionStrategy** can transparently redirect tasks between internal processing and desktop automatically
9. Run/task/approval/handoff/recovery events are emitted via WebSocket to the UI in real time

### State graph

Each runtime pipeline is modeled as a directed graph:

| Node type | Function |
|---|---|
| `entry` | Entry point |
| `planner` | Task decomposition |
| `safety` | Risk classification |
| `approval` | Human approval gate |
| `executor` | Tool execution |
| `reflection` | Result analysis |
| `recovery` | Failure classification |
| `script_recovery` | Fallback via sandbox |
| `checkpoint` | Progress persistence |
| `handoff` | Transfer to human |
| `complete` / `fail` | Terminal states |

Three compiled graph topologies: **agentic** (LLM-driven), **local_heuristic** (local rules) and **manual_assist** (plan-and-present).

### Core files

| File | Responsibility |
|---|---|
| `app_main.py` | FastAPI app, REST endpoints, daemon lifecycle |
| `runtime_manager.py` | Run orchestration, pipelines, daemon loop, agentic fallback |
| `agentic_loop.py` | LLM loop with tool-calling, cost budget, result compaction |
| `action_executor.py` | Task execution with recovery and goal verification |
| `recovery_engine.py` | Failure classification, target_node for graph |
| `execution_strategy.py` | Autonomous execution mode decision |
| `execution_economics.py` | CostTracker, PathComplexity, StoppingHeuristics, RouteOptimizer |
| `event_bus.py` | Async pub/sub event bus with 30+ typed events |
| `semantic_verifier.py` | GoalVerifier, SemanticValidator, PostconditionChecker |
| `context_window.py` | Context window compression and management |
| `canonical_tools.py` | Canonical tool catalog |
| `tool_registry.py` | Tool instantiation and dispatch |
| `state_graph.py` | State graph engine |
| `graph_definitions.py` | Graph topologies per pipeline |
| `world_model.py` | Typed entities with confidence, TTL, boosting and cross-refs |
| `strategy_memory.py` | Strategy history by objective type |
| `semantic_index.py` | Vector DB, BM25, hybrid re-ranking, pluggable embedders |
| `prompt_cache.py` | Prompt and tool cache for LLM |
| `multi_provider_client.py` | Multi-provider LLM client with retry, backoff, message merging |
| `selector_healing.py` | UI selector self-healing |
| `sandbox_executor.py` | Isolated Python/PowerShell execution |
| `skill_manifest.py` | Skill manifest, registry, runner |
| `codebase_map.py` | Persistent codebase map with AST scanning |
| `test_diagnostic_loop.py` | Test diagnostic loop (run/interpret/patch/rerun/expand) |
| `patch_planner.py` | Patch decomposition with risk and dependency ordering |
| `heartbeat.py` | Heartbeat emitter and progress tracker |
| `process_watchdog.py` | Frozen window detector and process watchdog |
| `ui_lock.py` | UI operation lock, cursor guard, interference detector |
| `hung_process_reaper.py` | Hung process detection and forced termination |
| `visual_grounding.py` | Visual grounding engine (OCR → UIA selectors) |
| `visual_replay.py` | Visual run replay recorder |
| `visual_policy.py` | Anti-fragility policy for visual automation |
| `artifact_processor.py` | Internal file processing |
| `dynamic_tool_creator.py` | Runtime micro-tool creation |
| `durable_queue.py` | Durable goal queue (SQLite) |
| `session_manager.py` | Durable sessions per objective/workspace |
| `planner_agent.py` | Planner with goal decomposition |

---

## System capabilities

### 1. Windows desktop automation

- **Process and window management**: list, open, close, bring to focus
- **Deep Explorer**: mapped drives, folders, open window context
- **Native UI Automation** via pywinauto: inspect, click, fill, select, scroll, hotkeys, wait for elements
- **UI Operation Lock**: async mutex serialises all mouse/keyboard/clipboard operations so only one runs at a time; includes cursor save/restore, user interference detection (cursor movement + focus change), and optional `BlockInput` for critical multi-step sequences with hard safety timeout
- **Hung Process Reaper**: when a tool times out on a frozen application (e.g. Excel COM hang), the reaper confirms the process is hung via `IsHungAppWindow`, kills it with `taskkill /F` to free the blocked thread-pool thread, and emits a `process_reaped` event; integrated into `BaseTool.run()` for `WindowsUiTool` (30s) and `OfficeTool` (120s)
- **Automatic selector healing**: when a UI selector fails, the agent searches for similar candidates in the World Model, tests them against the live UI and updates with renewed confidence
- **Headless Office COM adapters**: Word (open, search text, export PDF, save-as, create documents), Excel (read ranges, list sheets), Outlook (read recent emails, search messages, create drafts) — all running in background without requiring the user to open any application
- **Operational discovery**: installed applications, services, SMB shares, clipboard, notifications

### 2. Document and artifact processing

- **Internal processing** without opening on desktop: TXT, CSV, JSON, PDF, DOCX, XLSX, MSG
- **Text extraction** with optional OCR (pytesseract + PyMuPDF)
- **Document discovery**: search by name or content, metadata filters
- **Classification**: contracts, invoices, emails, attachments
- **Local indexing** for recurring queries

### 3. Dynamic code execution

- **Isolated sandbox** for Python and PowerShell with timeout, cancellation and artifact collection
- **Auto-inject COM init**: sandbox scripts using win32com automatically receive `pythoncom.CoInitialize()` and `try/except` wrapper
- **Sync fallback**: when `asyncio.create_subprocess_exec` fails with `NotImplementedError` (certain Windows configs), the sandbox falls back to `subprocess.run` via thread
- **Automatic dependency install**: if a script fails with `ModuleNotFoundError`, the system detects the missing package (25+ known mappings like `cv2`→`opencv-python`), requests approval, installs via pip, and retries — without wasting an LLM step
- **Scripts generated in real time** to solve unforeseen problems
- **Multi-source orchestration**: a single script can read emails (Outlook COM), cross-reference with a spreadsheet (openpyxl), and generate a report
- **Auto-creation of micro-tools**: tools persisted to disk and reusable across runs
- **Alternative script recovery**: when Office COM fails, generates openpyxl scripts; when browser fails, generates urllib scripts

### 4. Web navigation

- **Playwright** for DOM, navigation and interaction
- Bridge for native dialogs triggered by the browser

### 5. Intelligent planning

- **Multi-step decomposition**: complex tasks become sequences of sub-tasks
- **Multi-phase decomposition**: complex goals are divided into ordered phases with dependencies
- **Autonomous decision**: the agent automatically decides between internal processing (artifact/sandbox) and desktop (native apps, UI, Office COM)
- **Gap filling**: missing tools are worked around with sandbox scripts or dynamic tools
- **Proactive fallback**: if the primary path fails, the agent automatically generates alternative approaches

### 6. Memory and learning

- **Typed World Model** with 9 entity types: share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment
- **Confidence with temporal decay**: each entity has a 0.0-1.0 score that decays 0.05/day, with TTL/expiration per type
- **Contextual Boosting**: search results are re-ranked by task domain (e.g. "office" boosts `document_alias` over `share`/`device`)
- **Cross-Reference linking**: entities link to related entities (e.g. app_path → selectors, selectors → document_aliases) with graph traversal
- **Strategy Memory**: records successful tool call sequences by objective type, with confidence, used_count and context_tags
- **Semantic search** via local Vector DB (LanceDB): finds strategies and entities by semantic similarity, not just substring
- **Automatic reuse**: UI selectors, network paths, app locations and past strategies are reused without re-discovery
- **Auto-persistence**: all discovery (shares, apps, selectors) is automatically saved to the world model

### 7. Durable runtime

- **Always-on daemon**: loop every 5s processing goal queue, promoting deferred goals, recovering stuck ones
- **Durable queue** (SQLite): priority, automatic retry, scheduling, goal hierarchy, workspaces
- **Durable sessions** per objective and workspace: checkpoint, phases, accumulated context, expiration
- **Job checkpoints**: state snapshots for resumption after crash or restart
- **Concurrency**: up to 3 simultaneous runs processed by the daemon
- **Full lifecycle**: queued → running → completed/failed/cancelled/retrying/deferred

### 8. Control and security

- **Real human handoff**: pause → reply → resume with preserved context
- **Effect-based approvals**: real mutations require approval, discovery and reads are allowed
- **Safety classification**: each action is classified as allow/require_approval/deny
- **Real cancel**: runs can be cancelled mid-execution with cleanup

### 9. Performance and cost optimizations

- **Prompt caching**: compact system prompt (~357 tokens) and tool definitions cached in-process between steps
- **Native Anthropic prompt caching**: `cache_control=ephemeral` for server-side provider cache (78% cost reduction on repeated context)
- **Provider-aware**: zero caching overhead for providers without support
- **Local feature hashing**: 128-dimension embeddings without external API for semantic search
- **Per-run cost budget**: hard stop at $0.25 USD / 80k tokens per run (configurable), preventing runaway costs
- **Tool result compaction**: binary data filtered, stdout/stderr capped at 1500 chars, total tool result capped at 3000 chars before LLM sees it
- **LLM response limits**: max_tokens capped at 2048 (normal) / 8000 (thinking) to control output costs
- **Context window compression**: 40k token budget with automatic compression of old messages, keeping only the 3 most recent turns intact
- **Sub-agent budgets**: each parallel branch limited to 3 steps, 4 tool calls, 60s timeout, $0.05 USD, 3k tokens
- **Stopping heuristics**: automatic stop on budget exhaustion (85%), 2 consecutive errors, diminishing returns, or confidence below 20%

---

## Tool catalog

The canonical catalog shared by planner, runtime, API and UI:

| Tool | Actions |
|---|---|
| `shell` | Command execution (PowerShell/cmd) |
| `filesystem` | Read, write, search, copy/move, organize, undo |
| `memory` | World model + strategy memory (30+ actions) + semantic search + boosted search + cross-references |
| `browser` | Playwright for DOM and navigation |
| `web` | HTTP requests, web search, resource caching |
| `package_manager` | Package management |
| `software_inventory` | Installed software inventory |
| `env_manager` | Environment variables |
| `driver_manager` | Device drivers and printers |
| `os` | System operations |
| `desktop` | Processes, windows, Explorer, drives, clipboard, services |
| `windows_ui` | Inspect, click, fill, scroll, hotkeys, wait (with UI lock) |
| `share_discovery` | SMB shares and Explorer context |
| `document_intelligence` | Inspection, extraction, document search |
| `office` | Word, Excel, Outlook — all headless via COM |
| `sandbox` | Python/PowerShell execution in sandbox |
| `artifact` | Internal file processing |
| `dynamic_tool` | Micro-tool creation and execution |
| `skill` | Skill manifest, registration, execution |
| `visual` | Screenshot capture, OCR, visual grounding |
| `codebase_map` | Codebase scanning, symbol/import/route queries |
| `test_diagnostic` | Test diagnostic loop (run/interpret/patch/rerun) |
| `patch_planner` | Change decomposition with risk analysis |
| `heartbeat` | Long-tool heartbeat and progress tracking |
| `execution_economics` | Cost tracking, stopping heuristics, route optimization |
| `subagent` | Parallel branches with isolated budgets |

---

## Usage examples

What the system can do today:

- "Open Word and export document X to PDF"
- "Discover all mapped drives and list network shares"
- "Search for documents mentioning 'service contract' and give me a summary"
- "Read the sales spreadsheet, cross-reference with Outlook emails and generate a report"
- "Install the office HP network printer"
- "Configure the path for application X on the system"
- "Give me a summary of the last 10 Outlook emails about project Y"
- "Return my last 3 Outlook emails in a Word document" (without opening any app)
- "Create a script that organizes files in the Downloads folder by type and date"
- "Investigate why the printer is not working" (with pause to ask for context)
- "Process this 50k-line CSV and give me the top 10 categories by revenue"

---

## Implementation details

### Intelligent recovery

The `RecoveryEngine` doesn't just classify errors — it resolves them. Each failure generates a classification with `target_node` indicating where the graph should return to:

- **retry** → returns to `executor` with the same task
- **replan** → returns to `planner` to generate a new approach
- **script_recovery** → goes to `script_recovery` to execute an alternative script
- **ask_user** → goes to `handoff` to transfer to human
- **stop** → goes to `fail` as terminal state

When Office COM fails, openpyxl/python-docx scripts are generated automatically. When filesystem fails, os/shutil scripts substitute. When browser fails, urllib/requests scripts work around it.

### Selector healing

UI automation is inherently fragile — selectors break with software updates. The system handles this with self-healing:

1. pywinauto fails to find the element
2. The healer searches for similar candidates in the World Model (by app_context + fuzzy similarity)
3. Candidates are tested against the live UI
4. If it works, the original selector is updated with renewed confidence
5. An alias is created for the failed selector's intent

Configurable thresholds: `HEAL_MIN_SCORE=0.60`, `HEAL_CONFIDENCE_ON_SUCCESS=0.90`, `HEAL_CONFIDENCE_BOOST=0.15`.

### Autonomous execution mode decision

The `ExecutionStrategy` analyzes each task and decides transparently:

- **internal**: data that can be processed in memory (CSV, JSON, text)
- **desktop**: visual interaction needed (click, drag, fill forms)
- **script**: best solved with a tailored script
- **native**: should use the native tool directly

Office COM is treated as **headless** (background): `office.outlook_read_latest`, `office.outlook_search_messages`, `office.word_create_document` execute via COM without requiring the user to open Outlook/Word. The agent never asks the user to open any application.

### Robust COM sandbox

Sandbox scripts using `win32com` or `Dispatch` automatically receive:
- `pythoncom.CoInitialize()` / `CoUninitialize()` for correct COM apartment initialization
- `try/except` wrapper with `traceback.print_exc()` to never fail silently
- `sys.exit(1)` on exception to guarantee returncode != 0
- `CREATE_NO_WINDOW` flag on Windows to avoid blocking COM popups
- Fallback to `subprocess.run` via thread when `asyncio.create_subprocess_exec` raises `NotImplementedError`
- `sys.executable` instead of `"python"` to guarantee the same interpreter

### Persistence and durability

The system survives crashes and restarts:

- Goal queue persists in SQLite
- Sessions accumulate context across runs
- Job checkpoints capture complete state snapshots
- The daemon recovers stuck goals (stale > 600s)
- Checkpoints are deleted after terminal states

### Semantic vs. typed search

The World Model and Strategy Memory operate on two search layers:

- **Typed** (substring): `find_strategies("install_printer")` searches by exact substring
- **Semantic** (vector): `semantic_search("setup printing device")` finds "install network printer driver" by embedding similarity

The Vector DB (LanceDB) uses local feature hashing (128 dim, cosine metric) — zero external API dependency, works 100% offline.

### Prompt caching

In a 10-step agentic loop, the compact system prompt (~357 tokens) and tool definitions (~35 tools) are built once and reused across all steps. With caching:

- Built **once**, reused across all subsequent steps
- For Anthropic: `cache_control=ephemeral` activates server-side cache (78% cost reduction)
- For other providers: zero overhead
- Tool results are compacted and sanitized before being added to messages, saving additional tokens

---

## Quick start

### Backend

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install --with-deps
.\.venv\Scripts\python.exe -m uvicorn app_main:app --reload
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

---

## Diagnostics

The backend exposes endpoints to verify operational readiness:

| Endpoint | Function |
|---|---|
| `GET /health` | Overall system health |
| `GET /tools` | Available tools list |
| `GET /diagnostics/doctor` | Full environment diagnostic |
| `GET /onboarding/capabilities` | Detected capabilities |
| `GET /daemon/status` | Daemon loop state |
| `GET /goals` | Goals in queue |
| `GET /sessions` | Active sessions |

---

## Test coverage

**1230+ tests** across 50 suites, organized by component:

| Suite | Coverage |
|---|---|
| `test_phase1_sandbox.py` | Sandbox executor, artifact processor, dynamic tools |
| `test_phase2_world_model.py` | WorldEntity, WorldModel, StrategyRecord, StrategyMemory, MemoryTool |
| `test_phase3_runtime.py` | DurableQueue, SessionManager, job checkpoints, goal decomposition |
| `test_phase4_autonomy.py` | ExecutionStrategy, script recovery, orchestration, tool gaps |
| `test_state_graph.py` | GraphNode, GraphEdge, StateGraph, GraphExecutor, recovery targets |
| `test_selector_healing.py` | SelectorHealer, similarity, WorldModel integration, end-to-end |
| `test_prompt_cache.py` | PromptCache, CacheStats, Anthropic integration, lifecycle |
| `test_semantic_index.py` | Embeddings, LanceDB, semantic search, cross-domain matching |
| `test_event_bus.py` | EventBus, pub/sub, wildcards, async handlers, history |
| `test_semantic_verifier.py` | GoalVerifier, SemanticValidator, PostconditionChecker |
| `test_execution_economics.py` | CostTracker, PathComplexity, StoppingHeuristics, RouteOptimizer |
| `test_embedding_bm25_batch.py` | BM25, hybrid re-ranking, batch indexing, pluggable embedders |
| `test_visual_perception.py` | Visual grounding, visual replay, visual policy, screenshot model |
| `test_skill_manifest.py` | SkillManifest, SkillRegistry, SkillRunner, capability declaration |
| `test_codebase_map.py` | CodebaseMap, PythonScanner, PatternScanner, incremental updates |
| `test_diagnostic_and_planner.py` | TestDiagnosticLoop, PatchPlanner, RiskAssessor, DependencyOrderer |
| `test_heartbeat_watchdog.py` | HeartbeatEmitter, ProgressTracker, ProcessWatchdog, FrozenWindowDetector |
| `test_hung_process_reaper.py` | Hung process reaper, target context, IsHungAppWindow integration |
| `test_contextual_boost_crossref.py` | Contextual boosting, cross-reference linking, graph traversal |
| `test_ui_lock.py` | UIOperationLock, CursorGuard, InterferenceDetector, InputGuard |
| `test_subagent_branches.py` | Subagent parallel branches, budget enforcement, cascading cancel |
| `test_context_window.py` | ContextWindowManager, compression, emergency drop |
| `test_multi_provider_client.py` | LLM API retry, Anthropic message merging, LLMApiError |
| `test_dependency_install.py` | Missing module detection, module→package mapping, recovery classification, approval flow |
| Others (26 suites) | Runtime, providers, shell, filesystem, network, planner, reflection, downloads, extended thinking, parallel calls, agentic discovery, persistence, and more |

---

## Future roadmap

### Next evolutions

#### Architectural governance

The system already has ~30 modules with distinct responsibilities. The biggest risk is uncontrolled complexity.

- [ ] Clear contracts between layers (interfaces, not implementations)
- [ ] Explicit boundaries between modules with contract tests
- [ ] Architectural invariants documented and enforced by CI

#### Sandbox hardening (remaining)

Automatic dependency detection and approval-gated install are implemented. What's still missing:

- [ ] Capability isolation (restrict access per script)
- [ ] Filesystem scopes (limit I/O per run)
- [ ] Command policies (whitelist/blacklist of commands and modules)
- [ ] Provenance (track origin of each script: who generated it, why, when)
- [ ] Audit trails (complete log of inputs and outputs)

#### Operational skills library (remaining)

The skill manifest, registry and runner are implemented. What's still missing:

- [ ] Skill discovery by objective: choose installed skills before generating a new script
- [ ] Skill signing/checksum and provenance to prevent execution of altered code without review
- [ ] Skill evaluation: minimum tests per skill before making available to the agent
- [ ] Local/offline marketplace: import/export skill packages without telemetry

#### Workspace development automation (remaining)

Codebase map, test diagnostic loop and patch planner are implemented. What's still missing:

- [ ] Workspace awareness: detect open IDE, branch, venv, task runner, dev ports and related processes
- [ ] Refactor guardrails: prevent out-of-scope edits and detect accidental changes to unrelated files
- [ ] Engineering report per run: files touched, commands executed, tests run, remaining risks

### Advanced capabilities (next level)

#### Continuous automation evaluation

Agentic automation systems need to measure whether they improve or degrade over time.

- [ ] Local golden tasks: suite of representative tasks with fixtures and expected outcomes
- [ ] Benchmarks by domain: Office, filesystem, browser, Windows UI, drivers, documents and web research
- [ ] Regression replay: re-execute old runs in dry-run mode to compare plan, cost and result
- [ ] Confidence score per tool/action based on real success history
- [ ] Autonomy metrics: steps to success, avoidable handoffs, effective recoveries, interrupted loops
- [ ] Quality dashboard per runtime/model/provider version

#### Desktop session observability and control

For long-running automation, the user needs to understand and control what is happening without reading raw logs.

- [ ] Visual timeline per run with task graph, events, approvals, retries and optional screenshots
- [ ] Granular pause/resume per task or branch, not just per entire run
- [ ] Dry-run/plan-only with effect simulation and predicted approvals
- [ ] Shadow mode: observe user actions and suggest automations without executing
- [ ] Session recorder: transform a manual sequence into a reusable skill/script
- [ ] Redaction layer for screenshots, logs and tool results before persisting or displaying

#### Long-running automation robustness (remaining)

Heartbeat, progress tracker and process watchdog are implemented. What's still missing:

- [ ] Checkpoint per task graph branch, including large intermediate results
- [ ] Idempotent resume: avoid repeating mutations already applied after crash
- [ ] Rollback plan per mutating task when artifacts or known prior state exist
- [ ] Per-run quotas: CPU, memory, files touched, open processes and downloads limits

#### Intelligent retry with validation error re-injection

When a tool call fails due to validation (e.g.: missing `content` field), the system wastes an entire step for the LLM to see the error and try again. A middleware that intercepts and re-injects automatically would be more efficient:

- [ ] Pre-execution middleware that validates arguments before spending the step
- [ ] Automatic error re-injection with context ("content field is required, re-generate including the content")
- [ ] Re-injection limit per step (avoid infinite loops)
- [ ] Re-injection rate metrics per tool (identify tools with confusing schemas for the LLM)

#### Model routing by complexity

The same model (claude-sonnet-4-6) is used for "hello" and for "install network drivers". An intelligent router would save cost and latency by directing simple tasks to fast models:

- [ ] Complexity classifier (trivial, simple, complex, multi-step)
- [ ] Model pool: fast/cheap (haiku/gpt-4o-mini) for trivial, full for complex
- [ ] Automatic fallback: if fast model fails, escalate to full model
- [ ] Cost metrics per run with breakdown by model used

#### Streaming and real-time feedback

The user sees nothing until the entire run finishes or fails. Progress streaming would build more confidence:

- [ ] SSE/WebSocket with event streaming (task_planned, task_started, thinking)
- [ ] Partial LLM text streaming (show what the agent is thinking)
- [ ] Progress bar per graph phase (planner -> executor -> reflection)
- [ ] Recovery notifications ("tool failed, trying alternative...")

#### Cross-run conversational memory

Each run is independent — if the user says "do it again but with 5 emails", the agent doesn't know what "again" means. A conversation buffer persisted across runs would enable continuity:

- [ ] Conversation buffer in SQLite (last N messages per session)
- [ ] Reference resolution ("again" = last task, "that file" = last path used)
- [ ] Session context injected into system prompt (summary of last interactions)
- [ ] TTL per session (clear context after inactivity)

#### Constrained decoding and schema enforcement

Ensure the LLM always generates tool calls with all required fields, instead of validating afterwards and wasting steps:

- [ ] JSON schema enforcement in generation (guided decoding where the provider supports it)
- [ ] Client-side pre-validation of arguments before accepting the tool call
- [ ] Required argument template injected into the prompt per tool
- [ ] Compliance metrics per tool (which tools the LLM gets wrong most often)

#### Time-travel debugging and state branching

Inspired by [LangGraph's checkpoint system](https://github.com/langchain-ai/langgraph), the ability to navigate historical execution states, modify them, and resume from any point creates powerful debugging and exploration capabilities.

- [ ] State snapshot per graph node: full serializable checkpoint after each node completes
- [ ] Time-travel API: list, inspect and diff historical snapshots for a run
- [ ] Branch from checkpoint: select a prior state, modify it, and resume execution as a new branch
- [ ] State diff viewer: compare any two checkpoints side-by-side (fields changed, tasks added/removed)
- [ ] Replay with mutation: re-execute a past run with altered inputs or parameters to explore alternatives

#### Graceful drain and cooperative shutdown

Long-running automations need to stop cleanly on SIGTERM, service restart, or user cancel — without losing work or corrupting state.

- [ ] Cooperative drain: signal the runtime to stop after the current step completes, not mid-execution
- [ ] Drain-aware nodes: nodes can inspect drain state and skip expensive work when shutdown is imminent
- [ ] Resumable checkpoint on drain: save a full checkpoint before exiting so the run can resume on next startup
- [ ] SIGTERM handler: automatic drain on process signals with configurable grace period
- [ ] Drain reason tracking: record why the run was drained (sigterm, user_cancel, quota_exceeded, service_restart)

#### Configurable durability modes

Different automation scenarios require different trade-offs between persistence overhead and crash resilience.

- [ ] Exit-only mode: persist state only when the graph exits (best performance, no mid-run recovery)
- [ ] Async mode: persist state asynchronously while the next step executes (good balance)
- [ ] Sync mode: persist state synchronously before starting each step (highest durability)
- [ ] Per-run durability override: allow each run to specify its durability level based on criticality
- [ ] Durability metrics: track persistence latency overhead per mode to inform trade-off decisions

#### Dynamic fan-out and map-reduce orchestration

Some tasks require spawning a dynamic number of parallel sub-tasks based on runtime data (e.g., process each file in a directory, check each email in a batch).

- [ ] Dynamic Send API: spawn N parallel tasks from within a node based on runtime-computed inputs
- [ ] Fan-out with typed results: each spawned task returns a typed result collected by the parent
- [ ] Fan-in reducer: configurable merge strategy (concat, vote, first-success, custom) for parallel results
- [ ] Bounded concurrency: limit how many parallel tasks run simultaneously to avoid resource exhaustion
- [ ] Partial completion: continue with available results even if some branches fail or timeout

#### Structured long-term memory taxonomy

The World Model and Strategy Memory store entities and strategies. A richer taxonomy would enable more sophisticated reasoning across sessions and users.

- [ ] Semantic memory: stable facts about entities, preferences, and environment (persisted indefinitely)
- [ ] Episodic memory: time-stamped events and interactions with decay and summarization
- [ ] Procedural memory: learned skills, behaviors, and tool-calling patterns extracted from successful runs
- [ ] Namespace-based store: organize memories by (user, session, domain) with independent TTL per namespace
- [ ] Background memory extraction: asynchronous extraction of memories from completed runs without blocking the next turn
- [ ] Memory conflict resolution: detect and merge contradictory facts across sessions with confidence-weighted resolution

#### Multi-mode streaming pipeline

A single streaming API that supports multiple concurrent output modes for different consumers (UI, logs, debugger, metrics).

- [ ] Values mode: emit full state snapshot after each step (for state inspectors)
- [ ] Updates mode: emit only changed fields per step (for efficient UI updates)
- [ ] Messages mode: token-by-token LLM output streaming with metadata (for real-time display)
- [ ] Custom events mode: emit arbitrary events from within tools/nodes via StreamWriter (for domain-specific consumers)
- [ ] Debug mode: comprehensive step information including timing, input/output, state diffs (for troubleshooting)
- [ ] Checkpoint mode: emit checkpoint creation events (for persistence monitoring)
- [ ] Task mode: emit task start/finish with results and errors (for orchestration dashboards)
- [ ] Composable modes: subscribe to multiple modes simultaneously in a single stream

#### Graph introspection and visualization API

Programmatic access to the graph topology, execution trace, and node metadata for debugging, documentation, and UI generation.

- [ ] Graph topology export: serialize the compiled graph as JSON/DOT/Mermaid for visualization
- [ ] Node metadata API: query node type, expected inputs/outputs, edges, and conditions programmatically
- [ ] Execution trace: record the exact path taken through the graph per run with timing per node
- [ ] Conditional edge introspection: list all possible branches and which conditions trigger each
- [ ] Subgraph nesting: visualize nested subgraphs with drill-down into each level

#### Deterministic replay and side-effect isolation

Ensure that workflows can be replayed identically by isolating non-deterministic operations and side effects.

- [ ] Task wrapper for side effects: decorator that marks operations with side effects (API calls, file writes) for replay isolation
- [ ] Deterministic replay mode: re-execute a past run using recorded task results instead of live execution
- [ ] Side-effect registry: catalog all side-effecting operations per tool to enable selective replay
- [ ] Replay diff report: compare live vs. replayed execution to detect regressions or environment drift

---

## PHYLUM Manifesto: The Alliance for Digital Sovereignty

### 1. Code is Destiny, Ownership is Yours.

We believe that artificial intelligence should not be a walled garden controlled by the few. If AI is going to manage our digital lives, it must be auditable, local and free. PHYLUM is your sovereign territory within Windows.

### 2. Privacy is Not a Setting, It's the Default.

Data is an extension of the human mind. In PHYLUM, processing is local-first. Your documents, passwords and routines never feed third-party models. Privacy is protected by the physics of your hardware, not by mutable terms of service.

### 3. Collective Intelligence via P2P.

No user should have to teach the same thing to the machine twice. We are building a Digital Immune System where every automation discovery and every selector fix is shared anonymously and in a decentralized manner. If one PHYLUM learns, all PHYLUMs evolve.

### 4. The Economy of Collaboration (DePIN).

AI processing is the oil of the 21st century. Through PHYLUM, we transform idle hardware into assets. By providing computational power to the network, you are rewarded. The goal is a self-sustaining network where high-performance automation pays for itself, democratizing access to cutting-edge models without relying on credit cards or abusive subscriptions.

### 5. End of the Silo, Beginning of Orchestration.

We refuse to be hostages of closed ecosystems. PHYLUM was born to be the universal translator between software, APIs and human intentions. If a tool exists, PHYLUM must know how to operate it. If it doesn't exist, PHYLUM must know how to create it.

### Pillars of the Future (Decentralization Roadmap)

- **Global Strategy Mesh**: P2P database of execution graphs and Strategy Records validated by reputation.
- **Neural Marketplace**: decentralized token exchange for inference. Provide VRAM, receive tokens; use tokens to access models your local hardware cannot support.
- **Privacy-Safe Learning**: knowledge protocols that share task logic without exposing data content.
- **Anti-Fragile Governance**: a system where the evolution of code and tools is driven by real success on users' machines, not by shareholder profit.

---

## Supplementary documentation

- `README_PROJECT.md` — complete technical overview of the runtime and architecture
- `README_SETUP.md` — local setup, CI and troubleshooting

---

## License

PHYLUM is free software distributed under the **GNU General Public License v3.0** (GPLv3).
See the [LICENSE](LICENSE) file for the full text.

Copyright (C) 2026 Aguilar.

---

## Project direction

Build a local-first agentic runtime that operates on Windows like an experienced human operator: using native APIs, typed tools, clear approvals, durable runtime and continuous learning. Open-source, no proprietary cloud dependency, no telemetry.

Principles:

- **Local-first**: your data stays on your machine, always
- **Open-source**: auditable code, contributions welcome
- **Growing autonomy**: less premature fallback, more proactive resolution
- **Zero pixel automation**: native APIs as the backbone
- **Real learning**: each run improves the world model and strategy memory
- **Durability**: the system survives crashes, restarts and interruptions
- **Transparency**: every decision is traceable, every action is auditable
