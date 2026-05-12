# Agente Desktop

`Agente Desktop` e um runtime local para automacao orientada por linguagem natural no Windows. O projeto combina um backend FastAPI, um frontend React/Vite com WebSocket em tempo real e um runtime operacional com:

- `planner -> safety -> tool router -> reflection`
- loop `agentic` com tool-calling por provider LLM
- handoff humano real com `ask -> reply -> resume`
- persistencia local de estado, approvals e checkpoints de run

O foco atual continua sendo `Windows local-only`, sem pixel automation, OCR ou visao computacional.

## Estado atual

O projeto ja nao e apenas um `tool runner com UI`. Hoje ele tem:

- `RuntimeManager` com `pause`, `reply`, `resume`, `cancel`, reidratacao e listagem de runs
- `AgenticLoop` com tool-calling e controle interno `request_user_input`
- `RecoveryEngine` para retry e escalonamento de falhas para o usuario
- superficie canonica de tools compartilhada por planner, runtime, API e UI
- modo `API-first`: o caminho principal usa provider LLM; sem provider o sistema entra em `manual assist mode`

Arquivos centrais:

- `app_main.py`
- `runtime_manager.py`
- `agentic_loop.py`
- `recovery_engine.py`
- `nodes_safety.py`
- `nodes_tool_router.py`
- `nodes_reflection.py`
- `canonical_tools.py`

## Modos de execucao

Hoje o produto trabalha com dois comportamentos principais:

- `agentic`: modo padrao do produto. Usa provider configurado, executa tools, pode pausar para pedir contexto e depois retomar.
- `manual assist mode`: fallback quando nao ha provider configurado. O sistema monta um plano estruturado e orienta os passos, sem autonomia plena.

Internamente ainda existe caminho `heuristic/local` para execucao controlada, usado pelo backend quando o input explicita `allow_local_execution`.

## Arquitetura atual

Fluxo principal:

1. O usuario envia uma instrucao pela UI ou pela API.
2. O `RuntimeManager` cria a run, persiste o estado inicial e escolhe entre `agentic` ou `manual assist`.
3. O `planner` ou o `agentic_loop` propõem tasks usando o mesmo catalogo canonico de tools.
4. O `SafetyNode` classifica risco e decide entre `allow`, `require_approval` ou `deny`.
5. O `ToolRouterNode` executa a tool real.
6. O `ReflectionNode` produz um resumo acionavel.
7. O `RecoveryEngine` decide entre retry, pedir input ao usuario, ou erro terminal.
8. Eventos de run/task/approval/handoff/recovery sao emitidos por WebSocket e refletidos na UI.

## Runtime duravel e handoff humano

O runtime agora suporta estados formais de run como:

- `queued`
- `planning`
- `running`
- `awaiting_input`
- `awaiting_approval`
- `paused`
- `resuming`
- `recovering`
- `cancelling`
- `completed`
- `failed`
- `cancelled`

Dados persistidos por run incluem:

- `history` de eventos
- tasks e status
- approvals
- `pending_handoff`
- `handoffs`
- `agent_session`
- metadados de `recovery`

Com isso o agente pode:

- pausar para perguntar algo ao usuario
- receber resposta por API/UI
- retomar do ponto certo
- reidratar runs pausadas no startup do backend

## Catalogo canonico de tools

O catalogo real do sistema esta centralizado em `canonical_tools.py`. Hoje ele inclui:

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

Observacao: `request_user_input` existe como controle interno do loop agentic, mas nao faz parte da superficie operacional principal exibida como tool de dominio.

### Resumo das capabilities

- `shell`: executa comandos `powershell` ou `cmd` com validacao, timeout, retries, risco e cancelamento.
- `filesystem`: leitura, escrita, copia, move, mkdir, busca, stats, organizacao, estrutura e undo.
- `memory`: `set`, `get`, `delete`.
- `browser`: automacao Playwright para DOM e navegacao interativa.
- `web`: pesquisa, leitura segura, extracao de links, verificacao de URL e download validado.
- `package_manager`: `winget`, `choco` e `pip` para install/uninstall/list/search/show/upgrade.
- `software_inventory`: lista software instalado, resolve comandos e localiza executaveis.
- `env_manager`: leitura e edicao segura de env vars e `PATH`, com backup/restore.
- `driver_manager`: dispositivos, drivers, spooler, `pnputil` e consultas de impressora.
- `os`: introspeccao do sistema.
- `desktop`: primitives nativas para processos, janelas, clipboard, notificacoes e servicos.

### `filesystem`

A tool `filesystem` foi promovida para incluir acoes operacionais alem do CRUD basico:

- `read`
- `write`
- `delete`
- `move`
- `copy`
- `mkdir`
- `find_files`
- `list`
- `stat`
- `organize_directory`
- `organize_downloads`
- `organize_desktop`
- `detect_duplicates`
- `clean_temp`
- `create_structure`
- `undo`

### `web`

`web` foi separado de `browser` para cenarios de descoberta e download mais seguros:

- `search_web`
- `fetch_readonly`
- `extract_links`
- `check_url`
- `download_verified`
- `summarize_candidates`

### `software_inventory`

- `list_installed`
- `search_installed`
- `find_executable`
- `resolve_command`
- `find_install_location`
- `find_uninstaller`

### `env_manager`

- `get`
- `set`
- `unset`
- `append_path`
- `remove_path`
- `list_path_entries`
- `backup`
- `restore`

### `driver_manager`

- `list_devices`
- `device_status`
- `list_drivers`
- `find_driver_candidates`
- `install_inf`
- `add_driver_package`
- `rollback_driver`
- `scan_hardware_changes`
- `printer_status`
- `printer_driver_info`
- `restart_spooler`

## Safety, approvals, trust e rollback

Toda task passa pela policy em `nodes_safety.py`.

Regras importantes no estado atual:

- `shell`: alto risco exige aprovacao; blacklist dura continua bloqueando.
- `filesystem`: acoes mutantes e operacionais exigem aprovacao.
- `memory.delete`: exige aprovacao.
- `browser.download`, `browser.interact_dom` e `browser.upload_file`: exigem aprovacao.
- `web.download_verified`: exige aprovacao.
- `package_manager.install`, `uninstall` e `upgrade`: exigem aprovacao.
- `env_manager.set`, `unset`, `append_path`, `remove_path` e `restore`: exigem aprovacao.
- `driver_manager.install_inf`, `add_driver_package`, `rollback_driver`, `scan_hardware_changes` e `restart_spooler`: exigem aprovacao.
- `desktop.service_action`: exige aprovacao.
- `os`, `software_inventory` e `web` de leitura: permitidos.

Medidas adicionais presentes hoje:

- cancelamento cooperativo por `cancel_event`
- encerramento de arvore de processos no shell do Windows quando necessario
- `download_policy.py` para classificacao basica de confianca por dominio
- backup/restore para partes de `filesystem` e `env_manager`
- persistencia de eventos e listagem de runs/approvals

## Recovery

O `RecoveryEngine` classifica falhas em categorias como:

- `retryable`
- `needs_user`
- `blocked_by_policy`
- `terminal`

Na pratica isso permite:

- retry automatico em falhas transientes
- handoff ao usuario quando falta contexto
- erro terminal apenas quando nao ha caminho seguro

## Providers e modo agentic

O modo `agentic` usa `MultiProviderClient` com configuracao local de credenciais.

Providers suportados hoje:

- `openai`
- `anthropic`
- `openai_compatible`

As credenciais:

- sao digitadas na UI
- vao para o backend local
- sao armazenadas via `keyring`
- nao retornam ao frontend depois de salvas
- expoem apenas metadata local, como `last4`, `default_model` e `base_url`

Arquivos principais:

- `provider_registry.py`
- `credential_store.py`
- `multi_provider_client.py`
- `frontend/src/components/ProviderCredentialsForm.tsx`
- `frontend/src/components/SettingsPanel.tsx`

## API HTTP e WebSocket

### Runs

- `POST /run`
- `GET /state/{request_id}`
- `GET /runs`
- `POST /run/{request_id}/reply`
- `POST /run/{request_id}/resume`
- `POST /run/{request_id}/cancel`

### Approvals

- `POST /approval/{approval_id}`
- `POST /request_approval`
- `GET /approvals`

### Tools catalog

- `GET /tools`

### Provider settings

- `GET /settings/providers`
- `POST /settings/providers/{provider}/credential`
- `DELETE /settings/providers/{provider}/credential`
- `POST /settings/providers/{provider}/test`

### WebSocket

- `ws://127.0.0.1:8000/ws`

O WebSocket entrega eventos como:

- `run_started`
- `agent_step`
- `tool_call_proposed`
- `task_planned`
- `task_started`
- `task_retry_scheduled`
- `task_finished`
- `approval_requested`
- `approval_resolved`
- `user_input_requested`
- `user_input_received`
- `run_paused`
- `run_resumed`
- `run_finished`
- `run_failed`
- `run_cancelled`

## Exemplos de uso da API

### Run agentic

```json
POST /run
{
  "inputs": {
    "text": "veja se ha driver para minha impressora e me pergunte antes de instalar"
  },
  "runtime_mode": "agentic",
  "provider": "openai",
  "model": "gpt-4.1-mini"
}
```

### Run em manual assist mode

```json
POST /run
{
  "inputs": {
    "text": "find executable chrome"
  },
  "runtime_mode": "agentic"
}
```

Sem provider configurado, o backend responde com um plano assistido em vez de autonomia plena.

### Responder um handoff

```json
POST /run/{request_id}/reply
{
  "response": {
    "text": "Escolha a opcao oficial do fabricante"
  }
}
```

### Retomar uma run pausada

```json
POST /run/{request_id}/resume
```

### Aprovar uma task pendente

```json
POST /approval/{approval_id}
{
  "status": "approved"
}
```

### Cancelar uma run

```json
POST /run/{request_id}/cancel
```

## Como rodar em desenvolvimento

### 1. Backend

No Windows, o caminho mais confiavel e usar o Python da venv diretamente, sem depender da ativacao do shell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install
.venv\Scripts\python.exe -m uvicorn app_main:app --reload --host 127.0.0.1 --port 8000
```

Dependencias Python principais hoje:

- `fastapi`
- `uvicorn[standard]`
- `pydantic`
- `aiosqlite`
- `playwright`
- `httpx`
- `keyring`
- `psutil`
- `WMI`
- `pywin32`

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Scripts disponiveis em `frontend/package.json`:

- `npm run dev`
- `npm run build`
- `npm run preview`
- `npm run tauri:dev`
- `npm run tauri:build`

### 3. Endpoints locais padrao

- API: `http://127.0.0.1:8000`
- Frontend dev: `http://127.0.0.1:5173`
- WebSocket: `ws://127.0.0.1:8000/ws`

## Frontend atual

A UI atual oferece:

- chat para iniciar runs ou responder handoffs pausados
- seletor de `agent mode` e `manual assist mode`
- estado explicito de `waiting for provider configuration`
- selecao de provider/model no modo `agentic`
- painel de tasks
- painel de approvals
- `HandoffPanel`
- `RecoveryPanel`
- `TimelinePanel`
- painel de logs/eventos
- botao para cancelar run
- tela de settings para credenciais dos providers
- visualizacao do catalogo canonico de tools

Arquivos principais:

- `frontend/src/App.tsx`
- `frontend/src/state/store.ts`
- `frontend/src/hooks/useSocket.ts`
- `frontend/src/components/ChatPanel.tsx`
- `frontend/src/components/HandoffPanel.tsx`
- `frontend/src/components/RecoveryPanel.tsx`
- `frontend/src/components/TimelinePanel.tsx`
- `frontend/src/components/ApprovalsPanel.tsx`
- `frontend/src/components/AgentPanel.tsx`
- `frontend/src/components/TasksPanel.tsx`
- `frontend/src/components/SettingsPanel.tsx`

## Testes e verificacao

Suite atual:

```powershell
python -m pytest
```

Build do frontend:

```powershell
cd frontend
npm run build
```

Validacoes mais recentes:

- `python -m pytest` -> `17 passed`
- `npm run build` -> ok

Testes novos importantes:

- `tests/test_runtime_handoff.py`
- `tests/test_runtime_manager_native.py`
- `tests/test_agentic_provider.py`

## Arquivos importantes

### Backend

- `app_main.py`: API HTTP + WebSocket
- `runtime_manager.py`: orquestracao de runs, handoff, approvals, recovery, resume e cancelamento
- `agentic_loop.py`: loop de tool-calling com suporte a `request_user_input`
- `recovery_engine.py`: classificacao de falhas e estrategia de recuperacao
- `canonical_tools.py`: catalogo canonico compartilhado
- `planner_agent.py`: parser heuristico e fallback assistido
- `nodes_safety.py`: policy, risco e aprovacoes
- `nodes_tool_router.py`: roteamento para as tools reais
- `nodes_reflection.py`: resumo e recomendacao acionavel
- `agent_persistence.py`: persistencia local de estado, approvals e listagem de runs

### Tools e agentes

- `tool_shell.py`
- `tool_filesystem.py`
- `tool_memory.py`
- `tool_browser.py`
- `tool_web.py`
- `tool_package.py`
- `tool_software.py`
- `tool_env.py`
- `tool_driver.py`
- `tool_os.py`
- `tool_desktop.py`
- `browser_agent.py`
- `os_inspect_agent.py`
- `desktop_windows_agent.py`
- `fs_agent.py`
- `download_policy.py`

### Persistencia e providers

- `state.py`
- `agent_persistence.py`
- `credential_store.py`
- `provider_registry.py`
- `multi_provider_client.py`

## Limitacoes atuais

- foco em Windows local-only
- `desktop`, `env_manager` e `driver_manager` dependem de APIs nativas do Windows
- o frontend principal de desenvolvimento hoje e web; a camada desktop empacotada ainda nao e o centro do fluxo
- o planner heuristico continua sendo limitado para linguagem aberta; os cenarios mais flexiveis funcionam melhor no modo `agentic`
- `web` e `driver_manager` ainda sao a primeira versao da capability, com trust policy e descoberta ainda simplificadas

## Direcao do projeto

As prioridades atuais do projeto sao:

1. manter planner, runtime, API e UI convergindo para a mesma superficie real de tools
2. reforcar o caminho `API-first` como contrato principal do produto
3. expandir as capabilities nativas de desktop, software, web e drivers sem recorrer a pixel automation
4. melhorar trust policy, rollback e recovery antes de ampliar autonomia ainda mais
5. manter observabilidade, persistencia e testes junto com cada nova capability