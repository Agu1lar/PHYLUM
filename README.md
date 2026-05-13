# Agente Desktop

`Agente Desktop` e um runtime local para automacao operacional no Windows orientada por linguagem natural.
O projeto combina backend `FastAPI`, frontend `React/Vite` com `WebSocket` em tempo real e um runtime agentic com:

- `planner -> safety -> tool router -> reflection`
- loop `agentic` com provider LLM
- handoff humano real com `pause -> reply -> resume`
- approvals por efeito real da acao
- persistencia local de runs, approvals, checkpoints e contexto operacional

O foco do projeto e `Windows local-first`, priorizando APIs nativas e automacao sem pixel automation.

## Estado atual

Hoje o sistema ja nao e apenas um `tool runner com UI`.
Ele ja possui:

- runtime duravel com `cancel`, `pause`, `resume`, reidratacao e retomada de runs
- superficie canonica de tools compartilhada por planner, runtime, API e UI
- modo `API-first`: com provider configurado, o caminho principal e agentic; sem provider, o sistema entra em `manual assist`
- policy orientada a efeito, com approvals concentrados em mutacoes reais
- discovery operacional para janelas, processos, apps, Explorer, drives e shares
- UI Automation nativa inicial via `pywinauto`
- UI Automation com selector healing, anchors compostos, ranking de ambiguidades e memoria local de seletores por app/versao
- discovery documental com OCR opcional, indexacao local, filtros de metadados e classificacao de contratos/notas fiscais/emails/anexos
- adapters Office via COM para Word, Excel e Outlook
- memoria operacional tipada e checkpoints de autonomia
- world model tipado com entidades de confianca, expiracao (TTL) e decay automatico
- memoria de shares descobertos, aliases de documentos, caminhos de apps e seletores UI
- historico de estrategia bem-sucedida por tipo de objetivo com reaproveitamento automatico
- reaproveitamento automatico de seletores, caminhos e candidatos validos entre runs
- sandbox de execucao dinamica (Python/PowerShell) com isolamento, timeout e coleta de artefatos
- processamento interno de artefatos (text, CSV, JSON, PDF, DOCX, XLSX, MSG) sem abrir no desktop
- auto-criacao e persistencia de micro-ferramentas dinamicas reutilizaveis entre runs
- planejamento dinamico multi-step com fallback proativo via sandbox e dynamic tools
- daemon local sempre ativo com polling de fila, promocao de goals diferidos e recuperacao de stale
- fila duravel de objetivos com prioridade, retry automatico, scheduling e hierarquia de goals
- sessoes duraveis por objetivo e workspace com checkpoint, fases, contexto acumulado e expiracao
- jobs de longa duracao com checkpoint real e retomada automatica apos restart
- decomposicao de objetivos complexos em fases ordenadas com dependencias
- decisao autonoma entre processamento interno (artifact/sandbox) e desktop (apps nativos, UI, Office COM)
- recuperacao inteligente de falhas com geracao automatica de scripts alternativos
- preenchimento autonomo de lacunas de tools via sandbox scripts e dynamic tools
- orquestracao de tarefas multi-fonte com scripts gerados em tempo real (emails + planilha + relatorio)

## Arquitetura

Fluxo principal:

1. O usuario envia uma instrucao pela UI ou pela API.
2. O `RuntimeManager` cria a run e persiste o estado inicial.
3. O `AgenticLoop` ou o parser de fallback propõem tasks usando o mesmo catalogo canonico.
4. O `SafetyNode` classifica risco e decide entre `allow`, `require_approval` ou `deny`.
5. O `ToolRouterNode` executa a tool real.
6. O `ReflectionNode` gera um resumo semantico da acao.
7. O `RecoveryEngine` decide entre retry, handoff humano, replanning ou falha terminal.
8. Eventos de run/task/approval/handoff/recovery sao enviados por `WebSocket` para a UI.

Arquivos centrais:

- `app_main.py`
- `runtime_manager.py`
- `agentic_loop.py`
- `action_executor.py`
- `recovery_engine.py`
- `canonical_tools.py`
- `tool_registry.py`
- `sandbox_executor.py`
- `artifact_processor.py`
- `dynamic_tool_creator.py`
- `world_model.py`
- `strategy_memory.py`
- `durable_queue.py`
- `session_manager.py`
- `execution_strategy.py`

## Superficie operacional atual

Hoje o catalogo canonico inclui:

- `shell`
- `filesystem`
- `memory`
- `browser`
- `web`
- `package_manager`
- `software_inventory`
- `env_manager`
- `driver_manager`
- `os`
- `desktop`
- `windows_ui`
- `share_discovery`
- `document_intelligence`
- `office`
- `sandbox`
- `artifact`
- `dynamic_tool`

### Capabilities principais

- `desktop`: processos, janelas, Explorer profundo, drives mapeados, instaladores, abrir app/arquivo/pasta, clipboard, notificacoes e servicos
- `windows_ui`: inspecionar janelas/dialogos, listar elementos, encontrar elemento, esperar elemento, clicar/invocar, preencher texto, selecionar item, hotkeys, scroll e leitura de foco
- `share_discovery`: mappings SMB, contexto do Explorer e inspecao de shares corporativos
- `document_intelligence`: inspecao de documentos, extracao de texto, busca por conteudo e documentos recentes
- `office`: abrir documento, exportar PDF, save-as, buscar texto no Word, ler ranges no Excel, buscar mensagens no Outlook, listar planilhas, criar rascunho de email e revelar path de documento ativo
- `browser`: Playwright para DOM, navegacao e bridge inicial para dialogos nativos
- `filesystem`: leitura, escrita, busca, copy/move, organizacao e undo
- `sandbox`: execucao controlada de scripts Python e PowerShell dinamicos em ambiente isolado com timeout, cancelamento e coleta de artefatos
- `artifact`: carregamento, leitura, transformacao e analise de arquivos internamente (TXT, CSV, JSON, PDF, DOCX, XLSX, MSG) sem abrir no desktop do usuario
- `dynamic_tool`: criacao, persistencia e execucao de micro-ferramentas dinamicas durante a run, reutilizaveis entre sessoes
- `memory` (world model): entidades tipadas (share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment) com confianca (0-1), TTL/expiracao e decay automatico; domain shortcuts para remember/find shares, apps, aliases, selectors e paths; historico de estrategia por tipo de objetivo (record success/failure, find best strategy, reuse)

## O que o projeto faz hoje

Exemplos praticos do nivel atual:

- abrir Word, Explorer, pastas e arquivos locais
- descobrir drives mapeados e contexto de janelas abertas do Explorer
- buscar documentos por nome ou por conteudo em formatos comuns
- operar partes de UI nativa do Windows sem automacao por pixels
- exportar documentos Office para PDF
- pausar uma investigacao, pedir contexto ao usuario e retomar do ponto certo
- registrar observacoes, hipoteses, subobjetivos e verificacoes de meta durante a run
- executar scripts Python ou PowerShell dinamicos em sandbox controlado para resolver problemas sob demanda
- carregar e processar arquivos internamente (CSV, JSON, PDF, DOCX, XLSX, MSG) sem abrir no desktop
- criar micro-ferramentas dinamicas durante a run para cenarios nao previstos nas tools nativas
- decompor tarefas complexas multi-step que misturam navegacao, manipulacao de arquivos e extracao de dados
- persistir automaticamente shares, apps, seletores e caminhos descobertos no world model para reutilizacao futura
- consultar estrategias bem-sucedidas anteriores antes de executar tarefas complexas
- reaproveitar seletores UI, caminhos de rede e localizacoes de apps de runs anteriores sem re-discovery
- enfileirar objetivos com prioridade, retry automatico e agendamento futuro em fila duravel SQLite
- operar com daemon sempre ativo que processa a fila de goals, promove goals diferidos e recupera goals travados
- manter sessoes duraveis por objetivo e workspace que sobrevivem restart e acumulam contexto entre runs
- retomar jobs de longa duracao do ultimo checkpoint real apos crash ou restart do backend
- decompor automaticamente objetivos complexos em fases ordenadas com dependencias inter-fase
- decidir autonomamente quando processar dados internamente (artifact/sandbox) vs. usar apps nativos do desktop
- receber tarefas complexas multi-fonte (ex: "leia emails, cruze com planilha, crie relatorio") e resolver com scripts gerados em tempo real
- contornar a ausencia de tools especificas gerando scripts de automacao ou analise sob demanda
- recuperar automaticamente de falhas operacionais criando abordagens ou scripts alternativos quando o caminho primario falha

## O que o projeto ainda nao e

Apesar do salto recente, o sistema ainda nao esta no mesmo nivel operacional de um OpenClaw maduro.
Ele ja entrou na categoria certa, mas ainda faltam camadas de robustez para chegar em `OpenClaw-level ou acima`.

Os gaps principais restantes sao:

- mais adapters de dominio para fluxos reais do dia a dia
- testes end-to-end em cenarios reais com providers LLM configurados
- hardening de sandbox e governanca para operacao completamente autonoma em producao

## Execucao rapida

### Backend

Windows PowerShell:

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

## Diagnostics

O backend expoe endpoints para verificar a prontidao operacional do ambiente:
- operacao sempre ativa mais robusta, com filas duraveis, jobs longos e retomada mais agressiva

## Execucao rapida

### Backend

Windows PowerShell:

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

## Diagnostics

O backend expoe endpoints para verificar a prontidao operacional do ambiente:

- `GET /health`
- `GET /tools`
- `GET /diagnostics/doctor`
- `GET /onboarding/capabilities`

Esses endpoints ajudam a validar:

- providers configurados
- disponibilidade de `pywinauto`
- disponibilidade de `win32com`
- disponibilidade dos parsers documentais
- presenca do instalador desktop

## Roadmap futuro: Rumo à Autonomia Plena (Nível Agente)

Esta secao registra as proximas atualizacoes planejadas para levar o projeto alem de um "OpenClaw", alcancando um grau de **autonomia de agente via linguagem natural**. 

A nova visao estabelece que o sistema nao deve ficar engessado as tools atuais. As tools servem apenas como "facilitadores de caminho". O agente deve ser capaz de planejar e executar acoes por conta propria. Por exemplo, se o usuario pedir algo sobre um arquivo, o agente pode decidir baixa-lo, processa-lo internamente e devolver apenas a resposta (ou abrir o resultado), sem precisar desenvolver um projeto na maquina do usuario para tal fim ou ficar restrito a uma tool especifica.

### Fase 1 - Code Execution e Resolucao Proativa ✅ Concluido

- **Sandbox de Execucao** ✅: Capacidade de criar e executar scripts dinamicos (Python, PowerShell) em ambiente controlado localmente para resolver problemas sob demanda (ex: analisar um arquivo customizado, formatar dados complexos, ou automatizar uma acao que a tool padrao nao alcanca). Implementado em `sandbox_executor.py` e `tool_sandbox.py`.
- **Processamento Interno de Artefatos** ✅: Se o usuario solicitar a leitura ou transformacao de um arquivo, a IA pode baixar/carregar o arquivo em sua memoria interna, processar e devolver o resultado final sem delegar o trabalho de volta para o sistema do usuario. Suporta TXT, CSV, JSON, PDF, DOCX, XLSX, MSG. Implementado em `artifact_processor.py` e `tool_artifact.py`.
- **Auto-criacao de Tools** ✅: O agente pode escrever e persistir "micro-ferramentas" dinamicas durante a run para lidar com novos cenarios nao previstos nas tools nativas. Micro-tools sao persistidas em disco e reutilizaveis entre runs. Implementado em `dynamic_tool_creator.py` e `tool_dynamic.py`.
- **Raciocinio de Longo Prazo e Planejamento Dinamico** ✅: Capacidade do Planner de desmembrar tarefas complexas que misturam navegacao web, manipulacao de arquivos locais e extracao de dados em multiplos passos autonomos. System prompt do AgenticLoop atualizado com instrucoes de decomposicao multi-step e fallback via sandbox/dynamic tools.

### Fase 2 - World model mais forte ✅ Concluido

- **Entidades tipadas com confianca e expiracao** ✅: World model com 9 tipos de entidade (share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment), cada uma com confidence score (0.0-1.0), decay temporal automatico (0.05/dia), TTL/expiracao configuravel por tipo e hit counter. Implementado em `world_model.py`.
- **Memoria de shares descobertos, aliases de documentos e caminhos de apps** ✅: Domain-specific stores com metodos de conveniencia (remember_share, remember_app_path, remember_document_alias, remember_selector, remember_path_candidate) e lookups rapidos (find_share, find_app_path, etc.). Auto-persistencia de discoveries no runtime (shares, apps, selectors) via `runtime_manager.py`. Integrado em `tool_memory.py` com 30+ novas acoes.
- **Historico de estrategia bem-sucedida por objetivo** ✅: Strategy memory que registra sequencias de tool calls bem-sucedidas por goal_type, com confidence, used_count, context_tags e duration. Suporta record_success, record_failure, find_strategies, best_strategy e mark_reused. Estrategias com mais usos e maior confianca sao priorizadas. Implementado em `strategy_memory.py`.
- **Reaproveitamento automatico de seletores, caminhos e candidatos validos** ✅: System prompt do AgenticLoop instrui o LLM a consultar o world model antes de re-descobrir (world_find_*), persistir apos discovery bem-sucedido (world_remember_*), e reforcar com world_touch apos reuso. Runtime auto-persiste discoveries de shares, apps e selectors no checkpoint flow. Planner atualizado com 35+ keywords para acoes do world model e strategy memory.

### Fase 3 - Runtime sempre ativo e Planejamento de Longo Prazo ✅ Concluido

- **Daemon local robusto** ✅: RuntimeManager agora inicia um daemon loop no startup que roda a cada 5s, promovendo goals diferidos/retrying para queued, recuperando goals stuck em running (stale_seconds=600), retomando jobs com checkpoint pendente e processando a fila de goals com ate 3 runs concorrentes. O daemon para graciosamente no shutdown. API /daemon/status expoe estado do daemon.
- **Fila duravel de objetivos** ✅: DurableQueue implementa uma fila SQLite-backed com prioridade, retry automatico (max_retries + retry_delay_seconds), scheduling via scheduled_at, hierarquia de goals (parent_goal_id), workspaces, e lifecycle completo (queued -> running -> completed/failed/cancelled/retrying/deferred). APIs REST em /goals para enqueue, list, get e cancel. Cleanup automatico de goals antigos.
- **Jobs de longa duracao com retomada real** ✅: Job checkpoints sao salvos automaticamente no _checkpoint_agent_session contendo agent_session, autonomy, runtime_context e progresso de tasks. Tabela job_checkpoints no SQLite com state_snapshot, step_index, total_steps e flag resumable. O daemon detecta checkpoints resumable e re-lanca pipelines com resume=True, restaurando o estado completo do agente. Checkpoint deletado apos terminal state.
- **Sessoes duraveis por objetivo e workspace** ✅: SessionManager implementa sessoes SQLite-backed com objective, workspace, fases ordenadas (com advance_phase), contexto acumulado (merge_context), checkpoint incremental, run_ids/goal_ids, TTL/expires_at e status lifecycle (active/paused/completed/failed/expired). find_or_create_session permite reutilizar sessoes ativas. submit_run_with_session vincula automaticamente runs a sessoes. APIs REST em /sessions para create, list, get, close, checkpoint. Expiracao automatica de sessoes inativas pelo daemon.

### Fase 4 - Release gates de Agente Autonomo ✅ Concluido

- **Tarefas complexas multi-fonte resolvidas com scripts em tempo real** ✅: ExecutionStrategy.generate_orchestration_script gera scripts Python que leem multiplas fontes (Outlook via win32com, Excel via openpyxl, CSV, JSON, texto), cruzam dados e produzem relatorios/exportacoes. O system prompt do AgenticLoop instrui o LLM a preferir um unico sandbox.execute_python para tarefas multi-fonte ao inves de encadear 5+ tool calls separadas. Templates para read_email_outlook, read_excel_data, cross_reference_report e generate_report cobrem os cenarios mais comuns.
- **Contorno autonomo de ausencia de tools** ✅: ExecutionStrategy.detect_tool_gap identifica tools inexistentes e propoe sandbox scripts como alternativa. O system prompt instrui o LLM a checar dynamic_tool.list primeiro, depois gerar sandbox scripts para preencher lacunas, e usar dynamic_tool.create para necessidades recorrentes. known_gaps cobrem email.read, report.generate e data.cross_reference.
- **Decisao autonoma interno vs. desktop** ✅: ExecutionStrategy.decide_execution_mode classifica cada task em internal/desktop/script/native. Office COM reads (excel_read_range, outlook_search_messages, word_find_text) sao automaticamente redirecionadas para sandbox/artifact quando o objetivo e leitura de dados, nao interacao visual. Arquivos CSV/JSON/TXT/PDF/DOCX/XLSX sao processados internamente via artifact. RuntimeManager._apply_execution_strategy intercepta tasks no momento da criacao e redireciona transparentemente.
- **Recuperacao inteligente com scripts alternativos** ✅: RecoveryEngine integra ExecutionStrategy.suggest_script_recovery como fallback antes de declarar falha terminal. Quando Office COM falha, gera script openpyxl/python-docx. Quando browser/Playwright falha, gera script urllib. Quando filesystem falha, gera script os/shutil. ActionExecutor._execute_script_recovery executa o script alternativo automaticamente e, se bem-sucedido, marca a task original como completed sem pedir intervencao do usuario.

## Implementacoes futuras e riscos arquiteturais

Esta secao documenta melhorias estruturais que o projeto vai precisar para escalar sem colapsar em complexidade. Sao observacoes criticas sobre o estado atual e direcoes concretas para o futuro.

### 1. Governanca arquitetural rigida contra complexidade

O projeto esta MUITO perto de virar complexo demais. Esse e o maior risco hoje. Ja existem: runtime, planner, memory, strategy memory, recovery, sandbox, dynamic tools, approvals, world model, UI automation, artifact pipeline, office adapters. Isso comeca a entrar no territorio de **distributed cognitive runtime**. Sem governanca arquitetural muito rigida, vira caos. Acoes futuras:

- Definir contratos claros entre camadas (interfaces, nao implementacoes)
- Estabelecer boundaries explicitos entre modulos
- Criar testes de contrato entre componentes
- Documentar invariantes arquiteturais que nao podem ser violados

### 2. Arquitetura de eventos mais explicita

O sistema ja e implicitamente event-driven, mas ainda parece procedural/orchestrated. A migracao mental deve ir para: **Event Bus**, **State Transitions**, **Execution Graph**. Retries, replanning, handoffs, recovery, approvals e pause/resume ja sao naturalmente orientados a eventos. Hoje o padrao e "runtime chama runtime" — no futuro isso escala mal. Acoes futuras:

- Introduzir um event bus explicito entre componentes
- Modelar transicoes de estado como eventos first-class
- Desacoplar orchestration de execution via eventos
- Permitir que novos consumers se inscrevam em eventos sem modificar producers

### 3. Separacao entre reasoning e execution

Hoje reasoning e execution estao relativamente acoplados. Futuramente o projeto vai precisar de camadas distintas:

- **Cognitive layer**: planejamento, raciocinio, decisao de estrategia
- **Operational layer**: orquestracao de tasks, retry, recovery
- **Execution layer**: execucao real de tools, sandbox, UI automation
- **State layer**: persistencia, world model, strategy memory

Sem essa separacao, o planner comeca a conhecer demais o runtime, e isso vira acoplamento infernal. O objetivo e que cada camada possa evoluir independentemente.

### 4. Hardening do sandbox

A execucao dinamica de Python/PowerShell e um canhao nuclear arquitetural. Hoje funciona em ambiente controlado com timeout e isolamento basico, mas para autonomia real o projeto vai precisar de:

- **Capability isolation**: restringir o que scripts podem acessar
- **Filesystem scopes**: limitar escrita/leitura a diretorios especificos por run
- **Command policies**: whitelist/blacklist de comandos e modulos
- **Syscall restrictions**: limitar operacoes de sistema disponiveis
- **Provenance**: rastrear origem de cada script executado (quem gerou, por que, quando)
- **Audit trails**: log completo de tudo que o sandbox executou, com inputs e outputs

Esse e exatamente o tipo de coisa que explode em runtime agentic sem essas protecoes.

### 5. Verificacao semantica robusta

Hoje o agente executa, verifica superficialmente e continua. Mas autonomia robusta exige verificacao semantica real:

- **Goal verification**: "o objetivo foi realmente alcancado?"
- **Semantic validation**: "o resultado faz sentido no contexto do pedido?"
- **Postcondition checks**: "o relatorio foi criado?", "o Excel contem os dados corretos?", "o email foi enviado para a pessoa certa?"

Isso e MUITO mais dificil do que executar tools. Requer que o agente saiba interpretar o resultado no contexto do objetivo original, nao apenas verificar se a tool retornou success.

### 6. Execution economics

O projeto ainda vai precisar de controle economico de execucao:

- **Custo operacional**: quanto cada run consome em tokens, tempo e recursos
- **Complexidade de caminho**: quantas tools e passos cada abordagem exige
- **Heuristicas de execucao**: quando parar de explorar e pedir ajuda
- **Otimizacao de caminho**: escolher a rota mais eficiente entre alternativas

Agentes autonomos podem facilmente gastar tokens demais, explorar caminhos inuteis e entrar em loops caros. Sem execution economics, o custo operacional cresce sem controle.

### 7. Planner com grafo de execucao

O planner atual e relativamente linear. Eventualmente vai precisar evoluir para:

- **Task graph**: tarefas como nos em um grafo, nao uma lista sequencial
- **Dependency graph**: dependencias explicitas entre tarefas
- **Branch execution**: caminhos alternativos que podem ser tentados em paralelo
- **Speculative execution**: iniciar sub-tarefas antes de confirmar que serao necessarias
- **Partial completion**: concluir parcialmente uma tarefa e continuar com o que ja esta pronto

Isso e especialmente critico para tarefas longas onde o plano precisa adaptar-se em tempo real.

## Documentacao complementar

Para detalhes mais profundos:

- `README_PROJECT.md`: panorama tecnico mais completo do runtime e da arquitetura
- `README_SETUP.md`: setup local, CI e troubleshooting

## Direcao do projeto

Objetivo: construir um agente desktop local que opere no Windows de forma parecida com um operador humano, mas usando APIs nativas, tools tipadas, approvals claros e runtime duravel.

Direcao atual:

- mais autonomia operacional via execucao proativa
- menos fallback precoce
- menos friccao de approval para discovery
- mais verificacao semantica de resultado
- zero dependencia de pixel automation como espinha dorsal
