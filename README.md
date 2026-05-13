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

## O que o projeto ainda nao e

Apesar do salto recente, o sistema ainda nao esta no mesmo nivel operacional de um OpenClaw maduro.
Ele ja entrou na categoria certa, mas ainda faltam camadas de robustez para chegar em `OpenClaw-level ou acima`.

Os gaps principais restantes sao:

- mais adapters de dominio para fluxos reais do dia a dia
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

### Fase 3 - Runtime sempre ativo e Planejamento de Longo Prazo

- daemon local mais robusto para operacao continua
- filas duraveis de objetivos e raciocinio de longo prazo do Planner para desmembrar tarefas complexas
- jobs de longa duracao com retomada real apos restart
- sessoes mais duraveis por objetivo e por workspace operacional

### Fase 4 - Release gates de Agente Autonomo

Antes de considerar o sistema concluido neste novo nivel de autonomia, os seguintes cenarios devem funcionar perfeitamente:

- O agente consegue receber uma tarefa complexa (ex: "leia meus ultimos emails, cruze com a planilha local X e crie um relatorio") e resolve-la criando scripts em tempo real.
- O agente consegue contornar a ausencia de uma tool especifica criando um script de automacao ou analise sob demanda.
- O agente decide autonomamente o momento mais eficiente para processar algo internamente vs. usar a maquina do usuario.
- Recuperacao inteligente de falhas operacionais criando abordagens ou scripts alternativos se o caminho primario falhar.

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
