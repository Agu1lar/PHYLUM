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
- discovery documental e busca por conteudo
- adapters Office via COM para Word, Excel e Outlook
- memoria operacional tipada e checkpoints de autonomia

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

### Capabilities principais

- `desktop`: processos, janelas, Explorer, drives mapeados, abrir app/arquivo/pasta, clipboard, notificacoes e servicos
- `windows_ui`: inspecionar janelas, listar elementos, encontrar elemento, esperar elemento, clicar/invocar, preencher texto, selecionar item, hotkeys, scroll e leitura de foco
- `share_discovery`: mappings SMB, contexto do Explorer e inspecao de shares
- `document_intelligence`: inspecao de documentos, extracao de texto, busca por conteudo e documentos recentes
- `office`: abrir documento, exportar PDF, save-as, listar planilhas de workbook, criar rascunho de email e revelar path de documento ativo
- `browser`: Playwright para DOM, navegacao e bridge inicial para dialogos nativos
- `filesystem`: leitura, escrita, busca, copy/move, organizacao e undo

## O que o projeto faz hoje

Exemplos praticos do nivel atual:

- abrir Word, Explorer, pastas e arquivos locais
- descobrir drives mapeados e contexto de janelas abertas do Explorer
- buscar documentos por nome ou por conteudo em formatos comuns
- operar partes de UI nativa do Windows sem automacao por pixels
- exportar documentos Office para PDF
- pausar uma investigacao, pedir contexto ao usuario e retomar do ponto certo
- registrar observacoes, hipoteses, subobjetivos e verificacoes de meta durante a run

## O que o projeto ainda nao e

Apesar do salto recente, o sistema ainda nao esta no mesmo nivel operacional de um OpenClaw maduro.
Ele ja entrou na categoria certa, mas ainda faltam camadas de robustez para chegar em `OpenClaw-level ou acima`.

Os gaps principais ainda sao:

- healing de seletores e UIA mais resiliente a mudancas de interface
- OCR e leitura robusta de PDFs escaneados/imagens
- mais adapters de dominio para fluxos reais do dia a dia
- world model mais forte, com confianca, historico de estrategia e reaproveitamento automatico
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

## Roadmap futuro

Esta secao registra as proximas atualizacoes planejadas para levar o projeto ao nivel `OpenClaw+`.

### Fase 1 - UI Automation mais robusta

- selector healing e matching mais resiliente
- anchors compostos por contexto, hierarquia e vizinhanca
- memoria de seletores bons por app e versao
- melhores estrategias para ambiguidades sem escalar cedo demais ao usuario

### Fase 2 - OCR e document intelligence mais profunda

- OCR real para PDFs escaneados e imagens
- indexacao semantica/local de documentos
- filtros por metadado, autor, data, extensao e origem
- descoberta de contratos, notas fiscais, emails e anexos com mais confianca

### Fase 3 - Adapters de dominio de alto valor

- Explorer mais profundo: selecao, rename, mover, copiar, contexto e navegacao guiada
- instaladores Windows e fluxos de setup mais robustos
- print dialogs, file pickers e auth popups mais bem cobertos
- fluxos mais ricos de Outlook, Word e Excel
- melhorias especificas para impressoras, drivers e shares corporativos

### Fase 4 - World model mais forte

- entidades tipadas com confianca e expiracao
- memoria de shares descobertos, aliases de documentos e caminhos de apps
- historico de estrategia bem-sucedida por objetivo
- reaproveitamento automatico de seletores, caminhos e candidatos validos

### Fase 5 - Runtime sempre ativo

- daemon local mais robusto para operacao continua
- filas duraveis de objetivos
- jobs de longa duracao com retomada real apos restart
- sessoes mais duraveis por objetivo e por workspace operacional

### Fase 6 - Release gates de agente de maquina

Antes de considerar o sistema em nivel `OpenClaw ou maior`, os cenarios abaixo devem ficar confiaveis:

- abrir Word, Explorer e Settings repetidamente sem ajuste manual
- localizar um documento em share mapeado e abri-lo
- recuperar de seletor ambiguo ou janela divergente sem ajuda quando possivel
- pedir aprovacao apenas em mutacoes reais
- retomar uma run pausada com estado operacional preservado

## Documentacao complementar

Para detalhes mais profundos:

- `README_PROJECT.md`: panorama tecnico mais completo do runtime e da arquitetura
- `README_SETUP.md`: setup local, CI e troubleshooting

## Direcao do projeto

Objetivo: construir um agente desktop local que opere no Windows de forma parecida com um operador humano, mas usando APIs nativas, tools tipadas, approvals claros e runtime duravel.

Direcao atual:

- mais autonomia operacional
- menos fallback precoce
- menos friccao de approval para discovery
- mais verificacao semantica de resultado
- zero dependencia de pixel automation como espinha dorsal
