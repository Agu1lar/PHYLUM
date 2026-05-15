# Registro de modelos — testes, nuances e correções

Documento vivo do PHYLUM/AgenteDesktop. Para cada provedor e modelo registrado em `providers/provider_registry.py`, consolida o que foi testado, comportamentos observados e o histórico de correções no runtime.

**Referências de código:** `providers/provider_registry.py`, `core/model_router.py`, `providers/multi_provider_client.py`, `core/agentic_loop.py`, `core/tool_selector.py`, `core/execution_economics.py`.

**Como atualizar:** ao testar um modelo ou corrigir um bug, adicione uma linha em *Histórico de correções* e atualize *Status* / *O que foi testado*. Use o formato de data `AAAA-MM-DD`.

---

## Legenda

| Status | Significado |
|--------|-------------|
| `validated` | Testado em CI e/ou manualmente com fluxo agentic |
| `partial` | Testes unitários ou smoke; agentic completo limitado |
| `registry` | Listado no registry; sem validação agentic documentada |
| `known-issue` | Funciona com ressalvas documentadas |

| Tier (roteamento) | Uso em `MODEL_POOL` |
|-------------------|---------------------|
| `fast` | Trivial / simples |
| `full` | Complexo / multi-step |
| `premium` | Reservado para tarefas pesadas |

---

## Anthropic

**Transporte:** API nativa `/v1/messages` — `multi_provider_client._complete_anthropic`.  
**Nuances do provedor:** `cache_control=ephemeral` em system/tools; merge de `tool_result` em um único `user`; extended thinking em Sonnet/Opus 4.x; recomendado como provider principal na UI.

### `claude-sonnet-4-6`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `full` |
| Status | `validated` |
| Tool calling | Sim (nativo) |
| Contexto / max output | Até 8k tokens configurados no client (thinking adaptive) |

**O que foi testado**
- [x] Registry e aliases (`claude`, `claude-3-5-sonnet-latest` → sonnet-4-6)
- [x] Roteamento complexo → full tier (`tests/test_model_router.py`)
- [x] Fluxos agentic com aprovação (`tests/test_agentic_provider.py`)
- [x] Prompt cache Anthropic (`providers/prompt_cache.py`)
- [ ] Golden benchmarks E2E dedicados por modelo

**Nuances**
- Modelo padrão do provider; user-locked quando o usuário escolhe Sonnet explicitamente.
- Suporta thinking blocks; mensagens assistant com `_thinking_blocks` na conversão.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | Provider de referência; sem regressões Groq/OpenAI afetam este path |

---

### `claude-opus-4-7`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `premium` |
| Status | `partial` |
| Tool calling | Sim |

**O que foi testado**
- [x] Listado no registry e pool premium
- [ ] Run agentic manual documentada

**Nuances**
- Reservado para tarefas premium no roteador; custo alto em `execution_economics` DEFAULT_PRICING.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

### `claude-haiku-4-5-20251001`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `fast` |
| Status | `validated` (roteamento) |
| Tool calling | Sim |

**O que foi testado**
- [x] Roteamento trivial → Haiku (`tests/test_model_router.py`)
- [x] Escalada fast → full em falha (`core/agentic_loop.py`, `tests/test_model_router.py`)

**Nuances**
- Hint `haiku` em `FAST_TIER_MODEL_HINTS` para fallback de escalada.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

## OpenAI

**Transporte:** OpenAI-compatible `/v1/chat/completions` — `max_tokens`.  
**Nuances do provedor:** Payload padrão; sem headers extras.

### `gpt-4.1-mini`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `fast` |
| Status | `validated` |
| Default provider model | Sim (`provider_registry`) |

**O que foi testado**
- [x] Credential store + agentic run (`tests/test_agentic_provider.py`)
- [x] Roteamento trivial/complexo
- [x] Parallel tool calls / subagent tests (mock openai)

**Nuances**
- Modelo default OpenAI no registry.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

### `gpt-4.1`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `full` |
| Status | `partial` |

**O que foi testado**
- [x] Roteamento complexo → gpt-4.1
- [ ] E2E agentic documentado

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

### `gpt-4o-mini`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `fast` (pool) |
| Status | `partial` |

**O que foi testado**
- [x] Pool fast tier OpenAI
- [x] Escalada para gpt-4.1 em testes de fallback

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

## Google Gemini

**Transporte:** `generateContent` — não OpenAI-compatible.  
**Nuances:** `x-goog-api-key`; tools como `functionDeclarations`; system em `systemInstruction`.

### `gemini-2.5-flash`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `fast` (default registry) |
| Status | `validated` (unit) |

**O que foi testado**
- [x] Tool calling normalizado (`tests/test_multi_provider_client.py`)
- [x] Registry Gemini

**Nuances**
- Default model do provider Gemini.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

### `gemini-2.5-pro` / `gemini-2.0-flash`

| Campo | Valor |
|-------|--------|
| Tier | `full` / `fast` (2.0-flash no pool) |
| Status | `registry` |

**O que foi testado**
- [x] Listados no registry e MODEL_POOL

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

## OpenRouter

**Transporte:** OpenAI-compatible + headers `HTTP-Referer`, `X-Title`.  
**Base URL:** `https://openrouter.ai/api/v1`

### `openai/gpt-4o-mini`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `fast` |
| Status | `validated` (unit) |

**O que foi testado**
- [x] Chat completions URL e auth (`tests/test_multi_provider_client.py`)

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

### `anthropic/claude-3.5-sonnet` / `google/gemini-2.0-flash-001`

| Campo | Valor |
|-------|--------|
| Status | `registry` |

**Nuances**
- IDs com prefixo de provedor upstream; roteamento usa pool OpenRouter local.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

## Groq

**Transporte:** [OpenAI-compatible](https://console.groq.com/docs/overview) — `https://api.groq.com/openai/v1`.  
**Nuances do provedor (global):**
- `max_completion_tokens` (não `max_tokens`) no payload.
- Timeout client **120s** (`_GROQ_TIMEOUT_SECONDS`).
- `parallel_tool_calls: false` no payload agentic.
- `disable_tool_validation: true` — Groq não rejeita HTTP 400 quando o modelo omite campos obrigatórios (ex.: `desktop` sem `action`); o PHYLUM pre-valida e reinjeta.
- Catálogo agentic: **~28 tools**, ~36–37 KB JSON só de schemas.
- **Plano gratuito (on_demand):** TPM baixo (ex.: 6000 TPM); pedido agentic completo pode pedir **~10k+ tokens** de entrada → HTTP **413** com mensagem `tokens per minute`.
- **Plano pago:** roteamento eficiente fast/full funciona como desenhado.

**Cadeia de fallback (runtime):**
1. Tools **completas** (padrão eficiente / pago).
2. Se 413/429 TPM → retry com **schemas compactos** (`_compact_tools_for_groq`).
3. Se tier `fast` ainda falha → **escalada** para modelo `full` (`apply_model_escalation`).
4. Retry HTTP **429** com backoff até 60s.

**Variáveis de ambiente:** `AGENTE_MODEL_ROUTING`, `AGENTE_MODEL_FALLBACK`.

---

### `llama-3.1-8b-instant`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `fast` |
| Status | `validated` |
| Tool calling (Groq) | Sim (documentação Groq) |
| TPM free tier | ~6000 TPM observado; agentic full tools ~10293 tokens |

**O que foi testado**
- [x] Registry + base URL Groq
- [x] Roteamento trivial `"ola"` → 8B (`tests/test_model_router.py::test_groq_trivial_routes_to_fast_8b_by_default`)
- [x] Falha TPM 413 com tools completas; retry compact (`tests/test_multi_provider_client.py::test_groq_retries_with_compact_tools_on_tpm_limit`)
- [x] Escalada fast→full em TPM (`tests/test_model_router.py::test_groq_tpm_on_fast_tier_still_escalates`)
- [x] Manual: run `ad203550-...` reproduziu 413 → escalada → mensagem genérica (antes da correção de UX)

**Nuances**
- Rápido e barato no tier pago; no free tier quase sempre exige compactação ou escalada para 70B.
- Não usar como único modelo se o catálogo completo de tools for obrigatório sem fallback.

**Histórico de correções**

| Data | Mudança |
|------|---------|
| 2026-05-15 | Provider Groq adicionado (`provider_registry`, `multi_provider_client`). |
| 2026-05-15 | Bug: trivial→8B + 28 tools estoura TPM free (413); UI mostrava "conexão falhou antes de iniciar". |
| 2026-05-15 | Mitigação temporária: fast e full ambos em 70B (revertida). |
| 2026-05-15 | **Padrão pago restaurado:** fast=8B, full=70B; fallback compact tools + escalada; timeout 120s; mensagens de erro com corpo da API (`is_groq_tpm_limit_error`). |
| 2026-05-15 | Bug: HTTP 400 `tool call validation failed: desktop ... missing properties: action` encerrava a run; fix: `disable_tool_validation: true` no payload Groq. |
| 2026-05-15 | Bug: `failed_generation` / `Failed to call a function`; fix: prompt CONVERSATION + `tool_selector` (subset por mensagem) + pre-validacao; removido atalho trivial sem tools. |
| 2026-05-15 | Outlook+arquivo: roteamento `integration_deliverable` → tier full; retry focado (6 tools) apos failed_generation. Otimizacao de schema Groq-only (`refine_tool_schemas` / `build_minimal`) removida — ver roadmap **Otimizacao de contexto LLM** no README. |
| 2026-05-15 | Outlook leitura (sem arquivo): `outlook_integration` → 70B; tool_selector prioriza `office` e exclui `shell`; `unread_only` em outlook_read_latest; `_parse_tool_arguments` para Groq `null`. |

---

### `llama-3.3-70b-versatile`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `full` |
| Status | `validated` |
| Default registry | Sim |
| Tool calling | Sim |

**O que foi testado**
- [x] Chat completions Groq (unit)
- [x] Agentic com tools completas (manual: sucesso após 429 com retry)
- [x] Destino de escalada fast→full
- [x] Sem segunda escalada em TPM no tier full (`test_groq_tpm_on_full_tier_does_not_escalate_again`)

**Nuances**
- Modelo recomendado no UI quando free tier bloqueia 8B.
- Pode receber **429** (fila); client aguarda `retry-after` (até 60s).

**Histórico de correções**

| Data | Mudança |
|------|---------|
| 2026-05-15 | Default Groq; destino de `model_escalated` no agentic loop. |
| 2026-05-15 | Preços aproximados em `execution_economics.py` para métricas `by_model`. |
| 2026-05-15 | Mesmo fix `disable_tool_validation` após escalada 8B→70B. |

---

### `openai/gpt-oss-20b`

| Campo | Valor |
|-------|--------|
| Tier roteamento | — (não no MODEL_POOL; disponível manualmente) |
| Status | `registry` |

**Nuances**
- Documentado na [overview Groq](https://console.groq.com/docs/overview) como modelo exemplo; `reasoning_effort` low/medium/high no API Groq.

**O que foi testado**
- [ ] Agentic PHYLUM

**Histórico de correções**

| Data | Mudança |
|------|---------|
| 2026-05-15 | Listado no registry |

---

### `openai/gpt-oss-120b`

| Campo | Valor |
|-------|--------|
| Tier roteamento | `premium` |
| Status | `registry` |

**Histórico de correções**

| Data | Mudança |
|------|---------|
| 2026-05-15 | Pool premium Groq |

---

### `mixtral-8x7b-32768` / `gemma2-9b-it`

| Campo | Valor |
|-------|--------|
| Status | `registry` |

**O que foi testado**
- [ ] Não exercido no CI

**Histórico de correções**

| Data | Mudança |
|------|---------|
| 2026-05-15 | Adicionados ao registry para seleção manual |

---

## OpenAI Compatible (custom)

| Campo | Valor |
|-------|--------|
| Status | `partial` |
| Requer | `base_url` configurável |

**Nuances**
- Mesmo transporte que OpenAI; modelos definidos pelo usuário.
- Pool de roteamento genérico (`gpt-4o-mini` / `gpt-4o` como hints).

**O que foi testado**
- [x] Path OpenAI-compatible no client

**Histórico de correções**

| Data | Mudança |
|------|---------|
| — | — |

---

## Matriz rápida — roteamento por provedor

| Provider | Fast (trivial) | Full (complex) | Premium |
|----------|----------------|----------------|---------|
| anthropic | claude-haiku-4-5-20251001 | claude-sonnet-4-6 | claude-opus-4-7 |
| openai | gpt-4o-mini | gpt-4.1 | gpt-4.1 |
| gemini | gemini-2.0-flash | gemini-2.5-pro | gemini-2.5-pro |
| openrouter | openai/gpt-4o-mini | anthropic/claude-3.5-sonnet | anthropic/claude-3.5-sonnet |
| groq | llama-3.1-8b-instant | llama-3.3-70b-versatile | openai/gpt-oss-120b |

---

## Eventos de runtime relacionados

| Evento | Quando |
|--------|--------|
| `model_routed` | Após `route_model_for_request` no pipeline agentic |
| `model_escalated` | Fast falhou e `apply_model_escalation` mudou o modelo |
| `agent_step` | Cada chamada LLM (summary `Calling {provider}:{model}`) |

---

## Checklist para novo modelo

Ao adicionar um modelo em `provider_registry.py`:

1. [ ] Entrada neste arquivo (seção do provedor + tabela).
2. [ ] Entrada em `MODEL_POOL` (`core/model_router.py`) se participar do roteamento.
3. [ ] Preço em `DEFAULT_PRICING` (`core/execution_economics.py`) se custo for exposto.
4. [ ] Teste unitário mínimo em `tests/test_multi_provider_client.py` ou `tests/test_model_router.py`.
5. [ ] Smoke agentic manual (1 trivial + 1 com tool call).
6. [ ] Registrar TPM/limites/contexto na coluna **Nuances**.
7. [ ] Atualizar **Histórico de correções** com data e PR/commit.

---

## Índice de testes automatizados

| Arquivo | Cobertura |
|---------|-----------|
| `tests/test_model_router.py` | Complexidade, pools, fallback, Groq TPM/escalada |
| `tests/test_multi_provider_client.py` | Transporte OpenAI/Groq/Gemini, compact Groq |
| `tests/test_agentic_provider.py` | Credentials, registry, agentic OpenAI |
| `tests/test_execution_economics.py` | Custo por modelo |

---

*Última revisão estrutural: 2026-05-15 — inclui integração Groq e política padrão pago + fallback free tier.*
