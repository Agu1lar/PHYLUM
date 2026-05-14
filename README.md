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
| Tests | pytest, pytest-asyncio (420+ tests) |

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
| `runtime_manager.py` | Run orchestration, pipelines, daemon loop |
| `agentic_loop.py` | LLM loop with tool-calling and reflection |
| `action_executor.py` | Task execution with recovery |
| `recovery_engine.py` | Failure classification, target_node for graph |
| `execution_strategy.py` | Autonomous execution mode decision |
| `canonical_tools.py` | Canonical tool catalog |
| `tool_registry.py` | Tool instantiation and dispatch |
| `state_graph.py` | State graph engine |
| `graph_definitions.py` | Graph topologies per pipeline |
| `world_model.py` | Typed entities with confidence and TTL |
| `strategy_memory.py` | Strategy history by objective type |
| `semantic_index.py` | Vector DB for semantic search |
| `prompt_cache.py` | Prompt and tool cache for LLM |
| `selector_healing.py` | UI selector self-healing |
| `sandbox_executor.py` | Isolated Python/PowerShell execution |
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

### 9. Performance optimizations

- **Prompt caching**: system prompt (~3000+ tokens) and tool definitions (~35+ tools) cached in-process between steps
- **Native Anthropic prompt caching**: cache_control=ephemeral for server-side provider cache
- **Provider-aware**: zero caching overhead for providers without support
- **Local feature hashing**: 128-dimension embeddings without external API for semantic search

---

## Tool catalog

The canonical catalog shared by planner, runtime, API and UI:

| Tool | Actions |
|---|---|
| `shell` | Command execution |
| `filesystem` | Read, write, search, copy/move, organize, undo |
| `memory` | World model + strategy memory (30+ actions) + semantic search |
| `browser` | Playwright for DOM and navigation |
| `web` | HTTP requests |
| `package_manager` | Package management |
| `software_inventory` | Installed software inventory |
| `env_manager` | Environment variables |
| `driver_manager` | Device drivers |
| `os` | System operations |
| `desktop` | Processes, windows, Explorer, drives, clipboard, services |
| `windows_ui` | Inspect, click, fill, scroll, hotkeys, wait |
| `share_discovery` | SMB shares and Explorer context |
| `document_intelligence` | Inspection, extraction, document search |
| `office` | Word (open, search, create, export PDF), Excel (read ranges, list sheets), Outlook (read recent emails, search, draft) — all headless |
| `sandbox` | Python/PowerShell execution in sandbox |
| `artifact` | Internal file processing |
| `dynamic_tool` | Micro-tool creation and execution |

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

In a 16-step agentic loop, the system prompt (~3000+ tokens) and tool definitions (~35 tools) were rebuilt 16 times. With caching:

- Built **once**, reused **15 times**
- For Anthropic: `cache_control=ephemeral` activates server-side cache
- For other providers: zero overhead

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

**420+ tests** organized by component:

| Suite | Tests | Coverage |
|---|---|---|
| `test_phase1_sandbox.py` | 33 | Sandbox executor, artifact processor, dynamic tools |
| `test_phase2_world_model.py` | 63 | WorldEntity, WorldModel, StrategyRecord, StrategyMemory, MemoryTool |
| `test_phase3_runtime.py` | 41 | DurableQueue, SessionManager, job checkpoints, goal decomposition |
| `test_phase4_autonomy.py` | 52 | ExecutionStrategy, script recovery, orchestration, tool gaps |
| `test_state_graph.py` | 51 | GraphNode, GraphEdge, StateGraph, GraphExecutor, recovery targets |
| `test_selector_healing.py` | 32 | SelectorHealer, similarity, WorldModel integration, end-to-end |
| `test_prompt_cache.py` | 33 | PromptCache, CacheStats, Anthropic integration, lifecycle |
| `test_semantic_index.py` | 31 | Embeddings, LanceDB, semantic search, cross-domain matching |
| Others | 84+ | Runtime, providers, shell, filesystem, network, planner, reflection |

---

## Future roadmap

### Next evolutions

#### Architectural governance

The system already has ~20 modules with distinct responsibilities. The biggest risk is uncontrolled complexity.

- [ ] Clear contracts between layers (interfaces, not implementations)
- [ ] Explicit boundaries between modules with contract tests
- [ ] Architectural invariants documented and enforced by CI

#### Explicit event bus

The system is already implicitly event-driven (retries, replanning, handoffs, approvals, pause/resume). The natural evolution is a first-class event bus.

- [ ] Event bus between components (decouple orchestration from execution)
- [ ] State transitions as first-class events
- [ ] New consumers without modifying producers

#### Sandbox hardening

Dynamic Python/PowerShell execution in an agentic environment requires robust protections:

- [ ] Capability isolation (restrict access per script)
- [ ] Filesystem scopes (limit I/O per run)
- [ ] Command policies (whitelist/blacklist of commands and modules)
- [ ] Provenance (track origin of each script: who generated it, why, when)
- [ ] Audit trails (complete log of inputs and outputs)

#### Robust semantic verification

Beyond checking whether the tool returned success, verify whether the real objective was achieved:

- [ ] Goal verification ("was the objective actually achieved?")
- [ ] Semantic validation ("does the result make sense in context?")
- [ ] Postcondition checks ("was the report created?", "was the email sent?")

#### Execution economics

Economic execution control to avoid expensive loops and wasteful exploration:

- [ ] Cost per run (tokens, time, resources)
- [ ] Path complexity (tools and steps per approach)
- [ ] Stopping heuristics (when to ask for help vs. explore further)
- [ ] Route optimization (choose the most efficient path)

#### Real embedding models

The semantic index uses local feature hashing (fast, offline, deterministic). For more sophisticated searches:

- [ ] Plug-in for sentence-transformers or all-MiniLM models
- [ ] Hybrid re-ranking (BM25 + vector)
- [ ] Incremental indexing with batch updates

### Advanced capabilities (next level)

#### Visual perception and hybrid computer-use

PHYLUM avoids pixel automation as the primary path, but broad automation systems need visual fallback when there is no reliable native API.

- [ ] Screenshot state model: capture screen/window, OCR, visual elements, coordinates and relationship with UIA
- [ ] Visual grounding: map text/controls detected by screenshot to `windows_ui` selectors
- [ ] Controlled mouse/keyboard fallback by coordinates with verified bounding boxes
- [ ] Post-action visual verification: compare before/after, detect modals, spinners, errors and confirmations
- [ ] Visual run replay: timeline with redacted screenshots and action annotations
- [ ] Anti-fragility policy: prefer native API, use visual only when UIA/COM/DOM fail

#### Operational skills library

The project already creates dynamic tools, but still lacks a layer of versioned, auditable and reusable skills by domain.

- [ ] Local skill manifest with name, version, permissions, inputs/outputs and risks
- [ ] Skill runner with sandbox and capability declaration before execution
- [ ] Skill discovery by objective: choose installed skills before generating a new script
- [ ] Skill signing/checksum and provenance to prevent execution of altered code without review
- [ ] Skill evaluation: minimum tests per skill before making available to the agent
- [ ] Local/offline marketplace: import/export skill packages without telemetry

#### Workspace development automation

Since the runtime runs coupled to the local workspace, it can become a stronger engineering operator than a generic desktop executor.

- [ ] Persistent codebase map: symbols, imports, routes, tests, configs and ownership per file
- [ ] Test diagnostic loop: run test, interpret failure, patch, rerun target test, expand regression
- [ ] Patch planner: decompose large changes by files/owners with risk and application order
- [ ] Workspace awareness: detect open IDE, branch, venv, task runner, dev ports and related processes
- [ ] Refactor guardrails: prevent out-of-scope edits and detect accidental changes to unrelated files
- [ ] Engineering report per run: files touched, commands executed, tests run, remaining risks

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

#### Long-running automation robustness

Real automations can last minutes or hours and cross unstable networks, frozen apps and restarts.

- [ ] Heartbeat per long tool and standardized incremental progress
- [ ] Frozen window/unresponsive process watchdog with specific recovery
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
