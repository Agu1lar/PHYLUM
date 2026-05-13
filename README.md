<!-- Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or any later version. -->

# PHYLUM

**Local-first, open-source agentic runtime for Windows.**
**Your hardware, your AI, your control.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

PHYLUM e um runtime agentico que roda inteiramente na sua maquina. Nenhum dado sai do seu ambiente. Voce escolhe o provider LLM, voce controla as credenciais, voce decide o que o agente pode fazer. Codigo aberto sob GPLv3 — livre para usar, modificar e distribuir.

---

## Por que PHYLUM?

- **Local-first**: tudo roda no seu hardware. Seus dados nunca saem da sua maquina.
- **Open-source (GPLv3)**: codigo aberto, auditavel, sem vendor lock-in.
- **Voce escolhe o LLM**: Anthropic, OpenAI, Gemini, OpenRouter ou qualquer provider compativel. Traga sua propria API key.
- **Zero telemetria**: nenhum tracking, nenhum analytics, nenhum phone-home.
- **Autonomia real**: o agente planeja, executa, reflete, se recupera de falhas e aprende — sem depender de servicos cloud proprietarios.

---

## Visao geral

PHYLUM recebe instrucoes em linguagem natural e as executa no ambiente Windows do usuario. Nao e um simples "tool runner" — e um runtime agentico completo que planeja, executa, reflete, se recupera de falhas e aprende com experiencias anteriores.

O agente opera com um ciclo cognitivo:

```
planner -> safety -> tool router -> execution -> reflection -> recovery (se necessario)
```

Todo o ciclo e executado como um **grafo de estados dirigido** com 13 tipos de nos e transicoes condicionais, permitindo recuperacao precisa para o no exato onde uma falha ocorreu.

---

## Stack tecnologica

| Camada | Tecnologias |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, aiosqlite |
| Frontend | React, Vite, WebSocket |
| LLM Integration | Multi-provider (OpenAI, Anthropic, Gemini), tool-calling nativo |
| Automacao Windows | pywinauto, pywin32, WMI, win32com (COM) |
| Automacao Web | Playwright |
| Processamento de docs | pypdf, PyMuPDF, python-docx, openpyxl, extract-msg, Pillow, pytesseract |
| Persistencia | SQLite (via aiosqlite), JSON KV store |
| Vector DB | LanceDB (embedded, serverless) |
| Validacao | Pydantic |
| Testes | pytest, pytest-asyncio (420+ testes) |

---

## Arquitetura

### Fluxo principal

1. O usuario envia uma instrucao pela UI ou API
2. O **RuntimeManager** cria a run e persiste o estado inicial
3. O **PlannerAgent** decompoe a instrucao em tasks usando o catalogo canonico (ou fases ordenadas para goals complexos)
4. O **SafetyNode** classifica risco e decide: `allow`, `require_approval` ou `deny`
5. O **ToolRouterNode** executa a tool real
6. O **ReflectionNode** gera um resumo semantico da acao
7. O **RecoveryEngine** decide: retry, handoff humano, replanning, script alternativo ou falha terminal
8. O **ExecutionStrategy** pode redirecionar tasks entre processamento interno e desktop automaticamente
9. Eventos de run/task/approval/handoff/recovery sao emitidos por WebSocket para a UI em tempo real

### Grafo de estados

Cada pipeline do runtime e modelado como um grafo dirigido:

| Tipo de no | Funcao |
|---|---|
| `entry` | Ponto de entrada |
| `planner` | Decomposicao de tasks |
| `safety` | Classificacao de risco |
| `approval` | Gate de aprovacao humana |
| `executor` | Execucao de tool |
| `reflection` | Analise de resultado |
| `recovery` | Classificacao de falha |
| `script_recovery` | Fallback via sandbox |
| `checkpoint` | Persistencia de progresso |
| `handoff` | Transferencia para humano |
| `complete` / `fail` | Estados terminais |

Tres topologias de grafo compiladas: **agentic** (LLM-driven), **local_heuristic** (regras locais) e **manual_assist** (plan-and-present).

### Arquivos centrais

| Arquivo | Responsabilidade |
|---|---|
| `app_main.py` | FastAPI app, endpoints REST, daemon lifecycle |
| `runtime_manager.py` | Orquestracao de runs, pipelines, daemon loop |
| `agentic_loop.py` | Loop LLM com tool-calling e reflection |
| `action_executor.py` | Execucao de tasks com recovery |
| `recovery_engine.py` | Classificacao de falhas, target_node para grafo |
| `execution_strategy.py` | Decisao autonoma de modo de execucao |
| `canonical_tools.py` | Catalogo canonico de tools |
| `tool_registry.py` | Instanciacao e dispatch de tools |
| `state_graph.py` | Engine de grafo de estados |
| `graph_definitions.py` | Topologias de grafo por pipeline |
| `world_model.py` | Entidades tipadas com confianca e TTL |
| `strategy_memory.py` | Historico de estrategias por tipo de objetivo |
| `semantic_index.py` | Vector DB para busca semantica |
| `prompt_cache.py` | Cache de prompt e tools para LLM |
| `selector_healing.py` | Self-healing de seletores UI |
| `sandbox_executor.py` | Execucao isolada de Python/PowerShell |
| `artifact_processor.py` | Processamento interno de arquivos |
| `dynamic_tool_creator.py` | Criacao de micro-ferramentas em runtime |
| `durable_queue.py` | Fila duravel de objetivos (SQLite) |
| `session_manager.py` | Sessoes duraveis por objetivo/workspace |
| `planner_agent.py` | Planner com decomposicao de goals |

---

## Capacidades do sistema

### 1. Automacao de desktop Windows

- **Gerenciamento de processos e janelas**: listar, abrir, fechar, trazer ao foco
- **Explorer profundo**: drives mapeados, pastas, contexto de janelas abertas
- **UI Automation nativa** via pywinauto: inspecionar, clicar, preencher, selecionar, scroll, hotkeys, esperar elementos
- **Selector healing automatico**: quando um seletor UI falha, o agente busca candidatos similares no World Model, testa na UI ao vivo e atualiza com confianca renovada
- **Office COM adapters headless**: Word (abrir, buscar texto, exportar PDF, save-as, criar documentos), Excel (ler ranges, listar planilhas), Outlook (ler emails recentes, buscar mensagens, criar rascunho) — tudo rodando em background sem exigir que o usuario abra nenhum aplicativo
- **Discovery operacional**: aplicativos instalados, servicos, shares SMB, clipboard, notificacoes

### 2. Processamento de documentos e artefatos

- **Processamento interno** sem abrir no desktop: TXT, CSV, JSON, PDF, DOCX, XLSX, MSG
- **Extracao de texto** com OCR opcional (pytesseract + PyMuPDF)
- **Discovery documental**: busca por nome ou conteudo, filtros de metadados
- **Classificacao**: contratos, notas fiscais, emails, anexos
- **Indexacao local** para consultas recorrentes

### 3. Execucao dinamica de codigo

- **Sandbox isolado** para Python e PowerShell com timeout, cancelamento e coleta de artefatos
- **Auto-inject de COM init**: scripts sandbox que usam win32com recebem automaticamente `pythoncom.CoInitialize()` e `try/except` wrapper
- **Fallback sync**: quando `asyncio.create_subprocess_exec` falha com `NotImplementedError` (certas configs Windows), o sandbox cai para `subprocess.run` via thread
- **Scripts gerados em tempo real** para resolver problemas nao previstos
- **Orquestracao multi-fonte**: um unico script pode ler emails (Outlook COM), cruzar com planilha (openpyxl), e gerar relatorio
- **Auto-criacao de micro-tools**: ferramentas persistidas em disco e reutilizaveis entre runs
- **Recuperacao por script alternativo**: quando Office COM falha, gera script openpyxl; quando browser falha, gera script urllib

### 4. Navegacao web

- **Playwright** para DOM, navegacao e interacao
- Bridge para dialogos nativos disparados pelo browser

### 5. Planejamento inteligente

- **Decomposicao multi-step**: tarefas complexas viram sequencias de sub-tasks
- **Decomposicao multi-fase**: goals complexos sao divididos em fases ordenadas com dependencias
- **Decisao autonoma**: o agente decide automaticamente entre processamento interno (artifact/sandbox) e desktop (apps nativos, UI, Office COM)
- **Preenchimento de lacunas**: tools inexistentes sao contornadas com sandbox scripts ou dynamic tools
- **Fallback proativo**: se o caminho primario falha, o agente gera abordagens alternativas automaticamente

### 6. Memoria e aprendizado

- **World Model tipado** com 9 tipos de entidade: share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment
- **Confianca com decay temporal**: cada entidade tem score 0.0-1.0 que decai 0.05/dia, com TTL/expiracao por tipo
- **Strategy Memory**: registra sequencias de tool calls bem-sucedidas por tipo de objetivo, com confidence, used_count e context_tags
- **Busca semantica** via Vector DB local (LanceDB): encontra estrategias e entidades por similaridade semantica, nao apenas substring
- **Reaproveitamento automatico**: seletores UI, caminhos de rede, localizacoes de apps e estrategias anteriores sao reutilizados sem re-discovery
- **Auto-persistencia**: toda discovery (shares, apps, selectors) e automaticamente salva no world model

### 7. Runtime duravel

- **Daemon always-on**: loop a cada 5s processando fila de goals, promovendo diferidos, recuperando travados
- **Fila duravel** (SQLite): prioridade, retry automatico, scheduling, hierarquia de goals, workspaces
- **Sessoes duraveis** por objetivo e workspace: checkpoint, fases, contexto acumulado, expiracao
- **Job checkpoints**: snapshots de estado para retomada apos crash ou restart
- **Concorrencia**: ate 3 runs simultaneos processados pelo daemon
- **Lifecycle completo**: queued → running → completed/failed/cancelled/retrying/deferred

### 8. Controle e seguranca

- **Handoff humano real**: pause → reply → resume com contexto preservado
- **Approvals por efeito**: mutacoes reais exigem aprovacao, discovery e reads sao permitidos
- **Safety classification**: cada acao e classificada em allow/require_approval/deny
- **Cancel real**: runs podem ser canceladas mid-execution com cleanup

### 9. Otimizacoes de performance

- **Prompt caching**: system prompt (~3000+ tokens) e tool definitions (~35+ tools) cacheados in-process entre steps
- **Anthropic prompt caching nativo**: cache_control=ephemeral para cache server-side do provider
- **Provider-aware**: zero overhead de caching para providers sem suporte
- **Feature hashing local**: embeddings de 128 dimensoes sem API externa para busca semantica

---

## Catalogo de tools

O catalogo canonico compartilhado por planner, runtime, API e UI:

| Tool | Acoes |
|---|---|
| `shell` | Execucao de comandos |
| `filesystem` | Leitura, escrita, busca, copy/move, organizacao, undo |
| `memory` | World model + strategy memory (30+ acoes) + busca semantica |
| `browser` | Playwright para DOM e navegacao |
| `web` | Requisicoes HTTP |
| `package_manager` | Gerenciamento de pacotes |
| `software_inventory` | Inventario de software instalado |
| `env_manager` | Variaveis de ambiente |
| `driver_manager` | Drivers de dispositivos |
| `os` | Operacoes de sistema |
| `desktop` | Processos, janelas, Explorer, drives, clipboard, servicos |
| `windows_ui` | Inspecao, clique, preenchimento, scroll, hotkeys, espera |
| `share_discovery` | Shares SMB e contexto do Explorer |
| `document_intelligence` | Inspecao, extracao, busca documental |
| `office` | Word (abrir, buscar, criar, exportar PDF), Excel (ler ranges, listar planilhas), Outlook (ler emails recentes, buscar, rascunho) — tudo headless |
| `sandbox` | Execucao de Python/PowerShell em sandbox |
| `artifact` | Processamento interno de arquivos |
| `dynamic_tool` | Criacao e execucao de micro-tools |

---

## Exemplos de uso

O que o sistema consegue fazer hoje:

- "Abra o Word e exporte o documento X para PDF"
- "Descubra todos os drives mapeados e liste os compartilhamentos de rede"
- "Busque documentos que mencionem 'contrato de servico' e me de um resumo"
- "Leia a planilha de vendas, cruze com os emails do Outlook e gere um relatorio"
- "Instale a impressora de rede HP do escritorio"
- "Configure o caminho do aplicativo X no sistema"
- "Me de um resumo dos ultimos 10 emails do Outlook sobre o projeto Y"
- "Retorne meus 3 ultimos emails do Outlook em um documento Word" (sem abrir nenhum app)
- "Crie um script que organize os arquivos da pasta Downloads por tipo e data"
- "Investigue por que a impressora nao esta funcionando" (com pause para pedir contexto)
- "Processe este CSV de 50mil linhas e me de as top 10 categorias por receita"

---

## Tratamentos e nuances

### Recovery inteligente

O `RecoveryEngine` nao apenas classifica erros — ele resolve. Cada falha gera uma classificacao com `target_node` indicando para onde o grafo deve retornar:

- **retry** → volta ao `executor` com a mesma task
- **replan** → volta ao `planner` para gerar nova abordagem
- **script_recovery** → vai ao `script_recovery` para executar script alternativo
- **ask_user** → vai ao `handoff` para transferir ao humano
- **stop** → vai ao `fail` como estado terminal

Quando Office COM falha, scripts openpyxl/python-docx sao gerados automaticamente. Quando filesystem falha, scripts os/shutil substituem. Quando browser falha, scripts urllib/requests contornam.

### Selector healing

UI automation e inerentemente fragil — seletores quebram com atualizacoes de software. O sistema trata isso com self-healing:

1. pywinauto falha em encontrar o elemento
2. O healer busca candidatos similares no World Model (por app_context + fuzzy similarity)
3. Candidatos sao testados contra a UI ao vivo
4. Se funcionar, o seletor original e atualizado com confianca renovada
5. Um alias e criado para o intent do seletor falho

Thresholds configuraveis: `HEAL_MIN_SCORE=0.60`, `HEAL_CONFIDENCE_ON_SUCCESS=0.90`, `HEAL_CONFIDENCE_BOOST=0.15`.

### Decisao autonoma de modo de execucao

O `ExecutionStrategy` analisa cada task e decide transparentemente:

- **internal**: dados que podem ser processados em memoria (CSV, JSON, texto)
- **desktop**: interacao visual necessaria (clicar, arrastar, preencher formularios)
- **script**: melhor resolvido com um script sob medida
- **native**: deve usar a tool nativa diretamente

Office COM e tratado como **headless** (background): `office.outlook_read_latest`, `office.outlook_search_messages`, `office.word_create_document` executam via COM sem exigir que o usuario abra Outlook/Word. O agente nunca pede para o usuario abrir nenhum aplicativo.

### Sandbox robusto para COM

Scripts no sandbox que usam `win32com` ou `Dispatch` recebem automaticamente:
- `pythoncom.CoInitialize()` / `CoUninitialize()` para inicializacao correta do COM apartment
- `try/except` wrapper com `traceback.print_exc()` para nunca falhar silenciosamente
- `sys.exit(1)` em caso de excecao para garantir returncode != 0
- `CREATE_NO_WINDOW` flag no Windows para evitar popups COM bloqueantes
- Fallback para `subprocess.run` via thread quando `asyncio.create_subprocess_exec` lanca `NotImplementedError`
- `sys.executable` em vez de `"python"` para garantir o mesmo interpretador

### Persistencia e durabilidade

O sistema sobrevive crashes e restarts:

- Fila de goals persiste em SQLite
- Sessoes acumulam contexto entre runs
- Job checkpoints capturam snapshots de estado completo
- O daemon recupera goals travados (stale > 600s)
- Checkpoints sao deletados apos estados terminais

### Busca semantica vs. tipada

O World Model e Strategy Memory operam em duas camadas de busca:

- **Tipada** (substring): `find_strategies("install_printer")` busca por substring exata
- **Semantica** (vetorial): `semantic_search("setup printing device")` encontra "install network printer driver" por similaridade de embeddings

O Vector DB (LanceDB) usa feature hashing local (128 dim, cosine metric) — zero dependencia de API externa, funciona 100% offline.

### Prompt caching

Em um agentic loop de 16 steps, o system prompt (~3000+ tokens) e tool definitions (~35 tools) eram reconstruidos 16 vezes. Com o cache:

- Construidos **1 vez**, reutilizados **15 vezes**
- Para Anthropic: `cache_control=ephemeral` ativa cache server-side
- Para outros providers: zero overhead

---

## Execucao rapida

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

### Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

---

## Diagnostics

O backend expoe endpoints para verificar prontidao operacional:

| Endpoint | Funcao |
|---|---|
| `GET /health` | Saude geral do sistema |
| `GET /tools` | Lista de tools disponiveis |
| `GET /diagnostics/doctor` | Diagnostico completo do ambiente |
| `GET /onboarding/capabilities` | Capacidades detectadas |
| `GET /daemon/status` | Estado do daemon loop |
| `GET /goals` | Lista de goals na fila |
| `GET /sessions` | Sessoes ativas |

---

## Cobertura de testes

**420+ testes** organizados por componente:

| Suite | Testes | Cobertura |
|---|---|---|
| `test_phase1_sandbox.py` | 33 | Sandbox executor, artifact processor, dynamic tools |
| `test_phase2_world_model.py` | 63 | WorldEntity, WorldModel, StrategyRecord, StrategyMemory, MemoryTool |
| `test_phase3_runtime.py` | 41 | DurableQueue, SessionManager, job checkpoints, goal decomposition |
| `test_phase4_autonomy.py` | 52 | ExecutionStrategy, script recovery, orchestration, tool gaps |
| `test_state_graph.py` | 51 | GraphNode, GraphEdge, StateGraph, GraphExecutor, recovery targets |
| `test_selector_healing.py` | 32 | SelectorHealer, similarity, WorldModel integration, end-to-end |
| `test_prompt_cache.py` | 33 | PromptCache, CacheStats, Anthropic integration, lifecycle |
| `test_semantic_index.py` | 31 | Embeddings, LanceDB, semantic search, cross-domain matching |
| Outros | 84+ | Runtime, providers, shell, filesystem, network, planner, reflection |

---

## Roadmap futuro

### Proximas evolucoes

#### Governanca arquitetural

O sistema ja possui ~20 modulos com responsabilidades distintas. O maior risco e complexidade descontrolada.

- [ ] Contratos claros entre camadas (interfaces, nao implementacoes)
- [ ] Boundaries explicitos entre modulos com testes de contrato
- [ ] Invariantes arquiteturais documentados e enforced por CI

#### Event bus explicito

O sistema ja e implicitamente event-driven (retries, replanning, handoffs, approvals, pause/resume). A evolucao natural e um event bus first-class.

- [ ] Event bus entre componentes (desacoplar orchestration de execution)
- [ ] State transitions como eventos first-class
- [ ] Novos consumers sem modificar producers

#### Separacao reasoning / execution

Hoje reasoning e execution estao relativamente acoplados. A arquitetura alvo:

- [ ] **Cognitive layer**: planejamento, raciocinio, decisao de estrategia
- [ ] **Operational layer**: orquestracao de tasks, retry, recovery
- [ ] **Execution layer**: execucao real de tools, sandbox, UI automation
- [ ] **State layer**: persistencia, world model, strategy memory

#### Hardening do sandbox

Execucao dinamica de Python/PowerShell em ambiente agentico exige protecoes robustas:

- [ ] Capability isolation (restringir acesso por script)
- [ ] Filesystem scopes (limitar I/O por run)
- [ ] Command policies (whitelist/blacklist de comandos e modulos)
- [ ] Provenance (rastrear origem de cada script: quem gerou, por que, quando)
- [ ] Audit trails (log completo de inputs e outputs)

#### Verificacao semantica robusta

Alem de verificar se a tool retornou success, verificar se o objetivo real foi alcancado:

- [ ] Goal verification ("o objetivo foi realmente atingido?")
- [ ] Semantic validation ("o resultado faz sentido no contexto?")
- [ ] Postcondition checks ("o relatorio foi criado?", "o email foi enviado?")

#### Execution economics

Controle economico de execucao para evitar loops caros e exploracao inutil:

- [ ] Custo por run (tokens, tempo, recursos)
- [ ] Complexidade de caminho (tools e passos por abordagem)
- [ ] Heuristicas de parada (quando pedir ajuda vs. explorar mais)
- [ ] Otimizacao de rota (escolher caminho mais eficiente)

#### Evolucao do grafo de execucao

O State Graph ja esta implementado. Proximos passos:

- [ ] Dependency graph entre sub-tarefas (nao apenas sequencia)
- [ ] Branch execution (caminhos alternativos em paralelo)
- [ ] Speculative execution (iniciar sub-tarefas antes de confirmar necessidade)
- [ ] Partial completion (concluir parcialmente e prosseguir com resultados intermediarios)

#### Embedding models reais

O semantic index usa feature hashing local (rapido, offline, deterministico). Para buscas mais sofisticadas:

- [ ] Plug-in de sentence-transformers ou modelos all-MiniLM
- [ ] Re-ranking hibrido (BM25 + vetorial)
- [ ] Indexacao incremental com batch updates

### Capacidades avancadas (proximo nivel)

#### Parallel tool calls (batch execution) ✅ Concluido

O agentic loop agora executa multiplas tool calls em paralelo quando sao independentes.
Quando o LLM emite N tool calls em um unico turn, o runtime classifica cada uma como
independente (read/inspection) ou dependente (mutation/data-flow) e executa as independentes
via `asyncio.gather`. Resultados sao reordenados na sequencia original para o message history.

- [x] Suporte a multiplas tool calls por turn do LLM (batch de NormalizedToolCall)
- [x] Execucao concorrente via asyncio.gather para tools independentes
- [x] Deteccao de dependencias entre tool calls para ordenar quando necessario
- [x] Reducao de custo: N tools independentes gastam 1 step em vez de N

#### Extended thinking (chain-of-thought estruturado)

O LLM responde direto com uma tool call sem raciocinio intermediario visivel.
Habilitar extended thinking (Anthropic) ou chain-of-thought permite ao modelo avaliar opcoes, descartar hipoteses e planejar multi-step antes de agir:

- [x] Ativar `adaptive thinking` na API Anthropic (Sonnet 4.6, Opus 4.6/4.7)
- [x] Capturar e persistir o reasoning chain para observabilidade (evento `agent_thinking`, checkpoint)
- [x] Usar o thinking como input para o ReflectionNode (o modelo ja pensou — nao precisa re-avaliar)
- [x] Preservar thinking blocks em multi-turn para continuidade de raciocinio
- [x] Timeout adaptativo (120s) para chamadas com thinking ativo
- [x] Remocao de fallback morto (`SCRIPT_TEMPLATES["read_email_outlook"]`)

#### Context window management (sumarizacao de resultados) — IMPLEMENTADO

`ContextWindowManager` (core/context_window.py) comprime automaticamente mensagens antigas antes de cada chamada ao LLM,
mantendo a conversa dentro do token budget sem perder dados criticos:

- [x] Sumarizar tool results apos consumo (manter resumo, descartar detalhes)
- [x] Sliding window com prioridade recency (resultados recentes inteiros, antigos resumidos)
- [x] Token budgeting por step (garantir espaco para o LLM responder — reserve_for_response=8000 tokens)
- [x] Compressao seletiva: manter dados numericos/paths inteiros, comprimir texto narrativo

Detalhes da implementacao:
- **Estimativa de tokens**: heuristica chars/4, aplicada por mensagem incluindo tool_calls e thinking_blocks
- **Sliding window**: ultimas N tool results (recency_window=4) nunca sao comprimidas
- **Compressao estruturada**: tool results JSON sao parseados; paths, URLs, numeros, status e IDs preservados; texto narrativo longo truncado com marcador `[compressed]`
- **Listas grandes**: truncadas para 2-3 primeiros elementos + contagem dos restantes
- **Emergency drop**: se compressao nao for suficiente, tool results mais antigos sao substituidos por placeholder minimal
- **Integracao**: `agentic_loop.py` chama `compress_if_needed()` antes de cada `client.complete()`
- **Testes**: 26 testes dedicados cobrindo estimativa, recency, compressao seletiva, budget e emergency drop

#### Web research como parte da discovery autonoma

Quando o agente nao sabe como resolver um problema (ex: "como descobrir impressoras na rede local via PowerShell"),
ele deveria pesquisar na web e aplicar o resultado. Hoje o tool `web.search` existe mas o LLM nao o usa proativamente para aprender:

- [ ] Instrucao explicita no system prompt para usar web.search como ferramenta de aprendizado
- [ ] Integrar resultados de web search no raciocinio do agente (nao so retornar ao usuario)
- [ ] Cache de respostas web no World Model (evitar re-pesquisar o mesmo problema)
- [ ] Filtro de qualidade: preferir docs oficiais, StackOverflow, Microsoft Learn

#### Sub-agentes e execucao paralela de branches

Hoje o planner decompoe em fases sequenciais. Para tasks complexas, branches paralelos seriam mais eficientes
(ex: enquanto um agente investiga a rede, outro checa drivers instalados, outro busca na web):

- [ ] Spawn de sub-agentes com contexto isolado e objetivo especifico
- [ ] Merge de resultados quando todos os sub-agentes completam
- [ ] Budget individual por sub-agente (tokens, timeout, custo)
- [ ] Cancelamento em cascata (se o objetivo geral e atingido, cancelar branches restantes)

#### Retry inteligente com re-inject de erros de validacao

Quando uma tool call falha por validacao (ex: campo `content` faltando), o sistema gasta um step inteiro
para o LLM ver o erro e tentar de novo. Um middleware que intercepta e re-injeta automaticamente seria mais eficiente:

- [ ] Middleware pre-execution que valida argumentos antes de gastar o step
- [ ] Re-inject automatico do erro com contexto ("campo content e obrigatorio, re-gere incluindo o conteudo")
- [ ] Limite de re-injects por step (evitar loop infinito)
- [ ] Metricas de taxa de re-inject por tool (identificar tools com schema confuso para o LLM)

#### Model routing por complexidade

O mesmo modelo (claude-sonnet-4-6) e usado para "ola" e para "instale drivers da rede".
Um router inteligente economizaria custo e latencia direcionando tasks simples para modelos rapidos:

- [ ] Classificador de complexidade (trivial, simples, complexo, multi-step)
- [ ] Pool de modelos: rapido/barato (haiku/gpt-4o-mini) para trivial, completo para complexo
- [ ] Fallback automatico: se modelo rapido falha, escalar para modelo completo
- [ ] Metricas de custo por run com breakdown por modelo usado

#### Streaming e feedback em tempo real

O usuario nao ve nada ate a run inteira acabar ou falhar. Streaming de progresso daria mais confianca:

- [ ] SSE/WebSocket com streaming de events (task_planned, task_started, thinking)
- [ ] Streaming de texto parcial do LLM (mostrar o que o agente esta pensando)
- [ ] Progress bar por fase do grafo (planner -> executor -> reflection)
- [ ] Notificacoes de recovery ("tool falhou, tentando alternativa...")

#### Memoria conversacional cross-run

Cada run e independente — se o usuario diz "faz de novo mas com 5 emails", o agente nao sabe o que "de novo" significa.
Um conversation buffer persistido entre runs permitiria continuidade:

- [ ] Conversation buffer em SQLite (ultimas N mensagens por sessao)
- [ ] Resolucao de referencias ("de novo" = ultima task, "aquele arquivo" = ultimo path usado)
- [ ] Contexto de sessao injetado no system prompt (resumo das ultimas interacoes)
- [ ] TTL por sessao (limpar contexto apos inatividade)

#### Constrained decoding e schema enforcement

Garantir que o LLM sempre gere tool calls com todos os campos obrigatorios,
em vez de validar depois e desperdicar steps:

- [ ] JSON schema enforcement na geracao (guided decoding onde o provider suportar)
- [ ] Pre-validacao client-side dos argumentos antes de aceitar o tool call
- [ ] Template de argumentos obrigatorios injetado no prompt por tool
- [ ] Metricas de compliance por tool (quais tools o LLM mais erra)

---

## Documentacao complementar

- `README_PROJECT.md` — panorama tecnico completo do runtime e arquitetura
- `README_SETUP.md` — setup local, CI e troubleshooting

---

## Licenca

PHYLUM e software livre distribuido sob a **GNU General Public License v3.0** (GPLv3).
Veja o arquivo [LICENSE](LICENSE) para o texto completo.

Copyright (C) 2026 Aguilar.

---

## Direcao do projeto

Construir um runtime agentico local-first que opere no Windows como um operador humano experiente: usando APIs nativas, tools tipadas, approvals claros, runtime duravel e aprendizado continuo. Codigo aberto, sem dependencia de cloud proprietario, sem telemetria.

Principios:

- **Local-first**: seus dados ficam na sua maquina, sempre
- **Open-source**: codigo auditavel, contribuicoes bem-vindas
- **Autonomia crescente**: menos fallback precoce, mais resolucao proativa
- **Zero pixel automation**: APIs nativas como espinha dorsal
- **Aprendizado real**: cada run melhora o world model e strategy memory
- **Durabilidade**: o sistema sobrevive crashes, restarts e interrupcoes
- **Transparencia**: cada decisao e rastreavel, cada acao e auditavel
