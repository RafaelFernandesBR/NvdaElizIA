# Testes do operadorIA

Scripts standalone, sem pytest nem outra dependência — rodam com o `python`
normal do sistema, sem precisar abrir o NVDA. A explicação completa de
*por que* isso é possível e *como* o harness funciona está em
[`../ARCHITECTURE.md`](../ARCHITECTURE.md#como-testar-sem-o-nvda-rodando);
este arquivo é só o guia rápido de uso.

## Rodar tudo (exceto o teste de integração real)

```
python tests/run_all.py
```

## Rodar um arquivo específico

```
python tests/test_action_processor.py
```

## Teste de integração real (opcional, faz chamadas de rede)

```
python tests/test_real_api_integration.py
```

Só roda algo se já houver uma `gemini_api_key` configurada no painel do
NVDA (lida direto do `nvda.ini` — nunca impressa). Sem chave configurada,
termina com `SKIP` em vez de falhar. **Não é chamado por `run_all.py`** de
propósito: é o único teste que gasta cota da API e depende de rede.

## O que cada arquivo cobre

| Arquivo | O que testa | Precisa de rede? |
|---|---|---|
| `test_action_tools.py` | Schema das tools (`action_tools.py`) e `validate_args` — inclusive `record_note_tool`, `long_wait_tool` e os parâmetros de recorrência de `schedule_task_tool`/`done_tool` | Não |
| `test_action_processor.py` | Planejamento de lote (`BatchPlan`/`BatchItem`) e circuit breaker de loop — inclusive `BatchPlan.changed`/`result_data`/`stop_recurrence`, `record_note_tool` como tool "support", e `long_wait_tool` (`awaiting_wait_seconds`, teto de chamadas consecutivas, nunca populando `_last_actions`) | Não |
| `test_execute_batch.py` | `ActionProcessor.run_batch` — abort do lote em falha/mudança de tela/cancelamento (`should_cancel`, NVDA+Shift+C) | Não |
| `test_conversation_history.py` | `ConversationHistory` — payload limitado, janela do log, notas nunca janeladas (`record_note`/`notes_text`), sem function_call órfã, serialização `to_dict`/`from_dict` | Não |
| `test_screen_capture.py` | Downscale de screenshot (`_maybe_downscale`) | Não |
| `test_screen_curtain.py` | Detecção da cortina de tela ativa (API nova `screenCurtain`, NVDA ≥ 2026.1, e API antiga `vision`, 2019.3–2025.3.x), fail-open em qualquer erro | Não |
| `test_session_store.py` | `SessionStore` — CRUD de seções em SQLite, poda automática, `derive_title`, migração aditiva de schema (`conversation_log_json`, `notes_json`) | Não |
| `test_scheduler_store.py` | `SchedulerStore` — CRUD de tarefas agendadas, `get_due_tasks`/`get_missed_tasks` (excluindo recorrentes), cancelamento, guard de `mark_done`, recorrência (`reschedule_occurrence`, `max_occurrences`, `recurrence_until`), migração aditiva de schema | Não |
| `test_scheduler.py` | `TaskScheduler._check_due_tasks` (polling puro) e `seconds_since_last_input` | Não |
| `test_real_api_integration.py` | A API Gemini aceita o formato compactado sem erro de pairing | **Sim** |

## Limitações conhecidas

- `action_executor.py` (mouse/teclado/clipboard reais via `winUser`/`mouseHandler`/`api`) e
  o fluxo completo de `__init__.py:_run_ai_loop` **não têm teste automatizado** — dependem
  de APIs do NVDA/Windows que só existem dentro do NVDA rodando. Testar essa parte é manual:
  `NVDA+Shift+R` dentro do NVDA real. Ver o checklist em
  [`../ARCHITECTURE.md`](../ARCHITECTURE.md#checklist-de-teste-manual-dentro-do-nvda).
- wxPython (`wx`) não está em `globalPlugins/operadorIA/lib/` (é fornecido pelo próprio NVDA) —
  `test_screen_capture.py` usa um `wx` falso mínimo, suficiente só pra testar a aritmética de
  `_maybe_downscale`, não a captura de tela real.
- `sessions_dialog.py` (a UI wx de gerenciamento de seções) não tem teste automatizado, pelo
  mesmo motivo de `chat_dialog.py` — só manual, dentro do NVDA (`NVDA+Shift+O`).
- `scheduler_dialog.py` (lista de agendamentos e janela de aviso/graça) e o timer de verdade
  de `TaskScheduler` (`start()`/`stop()`/`_on_tick`) — mesmo motivo, só manual (`NVDA+Shift+A`).
  O fire-time gate completo (`GlobalPlugin._on_scheduled_task_due` e afins, em `__init__.py`)
  também só manual — ver checklist em `../ARCHITECTURE.md`.
- `test_session_store.py` já exercita a cópia vendorizada de `sqlite3` de
  `globalPlugins/operadorIA/lib/` (não a do Python do sistema) — o harness insere `lib/` no
  INÍCIO do `sys.path` (ver `_harness.py`), então ela sombreia a instalação local. Não precisa
  de teste manual só pra confirmar que essa cópia específica funciona.
