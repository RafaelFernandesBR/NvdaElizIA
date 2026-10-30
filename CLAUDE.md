# ElizIA (operadorIA) — NVDA Addon

> Nomenclatura: o identificador instalado/manifest (`addon_name` em `buildVars.py`,
> `config.conf["ElizIA"]`, a pasta `%APPDATA%\nvda\addons\ElizIA\`) é **ElizIA**. O pacote
> Python interno em `addon/globalPlugins/operadorIA/` manteve o nome histórico
> `operadorIA` — é só uma pasta/módulo, invisível pro NVDA e pro usuário final, sem relação
> com a identidade do addon instalado.

Addon NVDA que permite a usuários cegos controlar o Windows via comandos em linguagem natural, usando a API de visão do Google Gemini. O usuário descreve o que quer fazer; o addon captura a tela, envia ao Gemini (via **function calling nativo**, não JSON em prosa), executa as ações escolhidas (cliques, teclado, digitação, scroll) em lotes de até algumas ações por turno, e repete até a IA declarar a tarefa concluída.

> Para o funcionamento interno detalhado (por que o design é assim, o que mudou numa refatoração recente, e como testar sem o NVDA rodando), ver **[ARCHITECTURE.md](ARCHITECTURE.md)**.

Este repositório usa o [nvda-addon-template](https://github.com/accessolutions/nvda-addon-template)
pra build (SCons) e CI (GitHub Actions). Por isso a raiz do repositório **não** é a raiz
do addon: o conteúdo que o NVDA carrega fica em `addon/`, e tudo abaixo (código,
`globalPlugins/operadorIA/...`) é relativo a essa pasta, não à raiz do repo. `CLAUDE.md`,
`ARCHITECTURE.md` e `tests/` ficam na raiz do repo, fora de `addon/`, porque não fazem
parte do addon instalado. Detalhes adicionais de build/CI (metadados do addon, targets do
SCons, hooks de lint) estão nos comentários de `buildVars.py`, `sconstruct` e `prek.toml`;
os comandos do dia a dia estão resumidos abaixo.

## Comandos

```bash
uv sync                      # instala as ferramentas de build/lint (scons, ruff, pyright, prek) num .venv local
uv run scons                 # gera <addon_name>-<versão>.nvda-addon na raiz do repo (ex: ElizIA-1.8.0.nvda-addon)
uv run scons pot             # gera o template de tradução <addon_name>.pot — precisa de GNU gettext no PATH
python tests/run_all.py      # roda os testes standalone (sem NVDA, sem uv) — ver tests/README.md
```

- Requer `uv` instalado (`pip install --user uv`, ou ver https://docs.astral.sh/uv/) — `scons` e as outras ferramentas de build/lint vêm via `uv sync`, sem instalação global.
- O `.nvda-addon` gerado por `uv run scons` é instalável direto: NVDA > Ferramentas > Gerenciar complementos > Instalar a partir de um arquivo externo.
- Antes de empurrar pro repositório, rode também `uv run prek run --all-files --skip no-commit-to-branch --skip pyright` — os mesmos checks que a CI roda em `build_addon.yml`. Código do addon (`addon/globalPlugins/operadorIA/`) e `tests/` ficam fora desses checks de propósito (ver `exclude` em `prek.toml`).
- **Workflow obrigatório depois de qualquer mudança de código**, nesta ordem: (1) `python tests/run_all.py`, sem falhas (ver cobertura em [`tests/README.md`](tests/README.md)); (2) rodar o agent `code-reviewer` sobre o diff gerado e corrigir os achados legítimos; (3) só depois dos dois anteriores limpos, `uv run scons` sem erro como último gate antes de considerar a mudança pronta. Não precisa rodar o build a cada iteração só pra "conferir" — os dois primeiros passos já pegam a maioria dos problemas, e o build é o passo mais lento.

## Estrutura do Projeto

```
buildVars.py                        # Metadados do addon pro build (nome, versão, autor, urls) — gera manifest.ini
addon/
└── globalPlugins/operadorIA/
    ├── __init__.py                 # GlobalPlugin: atalhos, loop principal de automação, fire-time gate do scheduler
    ├── addon_config.py             # Constantes internas, prompt do sistema, safety settings
    ├── action_tools.py             # Ações da IA como types.FunctionDeclaration (function calling nativo)
    ├── gemini_client.py            # Cliente SDK oficial google-genai, com tools/mode=ANY forçado
    ├── action_processor.py         # Planeja (BatchPlan/BatchItem) e executa (run_batch) lotes de ação
    ├── action_executor.py          # Execução real: mouse, teclado, clipboard, app/shell (ctypes/winUser/subprocess)
    ├── screen_capture.py           # Captura fullscreen via wxPython → base64 PNG (com downscale)
    ├── chat_dialog.py              # UI de chat (wxPython) para interação contínua
    ├── sessions_dialog.py          # UI (wxPython) de gerenciamento de seções salvas
    ├── session_store.py           # Persistência de seções em SQLite (sqlite3 vendorizado em lib/, não vem com o NVDA)
    ├── scheduler.py                # Timer + polling de tarefas agendadas (gui.NonReEntrantTimer, idiom do AutoUpdateChecker do NVDA core)
    ├── scheduler_store.py          # Persistência de tarefas agendadas em SQLite (mesmo arquivo de session_store.py, tabela própria)
    ├── scheduler_dialog.py         # UI (wxPython): lista de agendamentos + janela de aviso/graça cancelável
    ├── settings.py                 # Painel de configurações integrado ao NVDA
    ├── utils.py                    # ConversationHistory (histórico O(1) por turno), clean_markdown
    └── lib/                        # Dependências bundled (google-genai SDK + deps)
        ├── google/genai/           # SDK oficial do Google Gemini
        ├── anyio/                  # Async I/O library
        ├── pydantic/               # Data validation
        └── ... (outras libs de suporte)
addon/locale/pt_BR/LC_MESSAGES/nvda.po  # Catálogo de tradução pt-BR (ver "Tradução (i18n)" abaixo)
ARCHITECTURE.md                     # Detalhamento técnico do pipeline de IA e gestão de contexto
tests/                              # Testes standalone (sem NVDA, sem pytest) — ver tests/README.md
```

## Como Funciona

**Atalho:** `NVDA+Shift+R` (padrão) → abre diálogo de entrada → inicia `_run_ai_loop()` em thread separada. Como qualquer comando NVDA, é remapeável pelo usuário em **Preferências > Gestos de entrada** (categoria "ElizIA Visual") — não é fixo no código (ver "Gestos de teclado" nas Convenções abaixo). `NVDA+Shift+C` (padrão) cancela uma automação em execução a qualquer momento — único gesto deste addon que exige o lock **preso** em vez de livre.

**Loop de automação** (máx. 300 iterações, configurável, padrão 15), por turno:
1. Checa se `NVDA+Shift+C` pediu cancelamento (checado de novo depois do passo 5) — se sim, para como um terceiro desfecho, distinto de sucesso/falha
2. Captura screenshot (com downscale se exceder `MAX_SCREENSHOT_DIMENSION`) → base64 PNG
3. Monta `contents=` com, no máximo, 3 `Content` — só a screenshot ATUAL viaja na requisição; turnos anteriores entram só como texto (`ConversationHistory`): a transcrição humana da conversa (janelada), um log técnico de ações (também janelado), e as notas registradas via `record_note_tool`, que nunca saem da janela
4. Chama o Gemini com as tools de `action_tools.py` e `mode=ANY` (o modelo sempre devolve uma ou mais function calls, nunca texto solto)
5. `ActionProcessor.process_function_calls()` planeja um lote de até `MAX_ACTIONS_PER_TURN` ações, validando parâmetros antes de executar
6. `ActionProcessor.run_batch()` executa o lote em sequência, abortando o restante se uma ação falhar, a tela mudar, ou um cancelamento for pedido no meio
7. O resumo do turno é gravado no histórico; se a mesma ação se repetir sem a tela mudar, um circuit breaker **interrompe a automação de verdade** (não é só um aviso)
8. Repete até `done_tool` ser chamado (com um resumo do que foi visto) ou atingir `max_turns`

**Após conclusão:** abre `OperatorChatDialog` para perguntas de acompanhamento (reusa o mesmo `_run_ai_loop`, incluindo o histórico já acumulado da tarefa).

**Pausa longa dentro da mesma tarefa:** `long_wait_tool(seconds)` — nível do `BatchPlan`, como `ask_user_tool`/`done_tool` (nunca `kind="execute"`/`"support"`, nunca passa por `run_batch`/circuit breaker de loop) — pausa a MESMA automação por até `long_wait_max_seconds` (padrão 120s), via `self._cancel_event.wait(timeout=...)`, interrompível quase instantaneamente por `NVDA+Shift+C`. Serve pra esperar um processo de fundo (build, upload, exportação) terminar sem perder histórico/notas nem reagendar nada — diferente de `schedule_task_tool`, que termina esta automação e cria uma nova sem memória bem mais tarde. Um teto interno de chamadas consecutivas (`addon_config.MAX_CONSECUTIVE_LONG_WAITS`, não configurável) evita espera sem fim, já que esta tool nunca aciona o circuit breaker normal — ao bater nele, a IA recebe um erro explícito pedindo pra agir diferente (concluir, ou usar `schedule_task_tool`).

**Seções (sessions):** cada tarefa nova vira uma linha em `session_store.py` (SQLite, no perfil ativo do NVDA); a cada turno, `ConversationHistory` é persistida automaticamente (autosave). `NVDA+Shift+O` (padrão, também remapeável) abre `SessionsDialog` — lista as seções salvas e permite abrir/retomar, renomear ou excluir. Retomar uma seção reconstrói `ConversationHistory` a partir do disco e reabre o chat já preenchido com a **transcrição humana da conversa** ("Você: ..." / "Operador IA: ..."), pronto para uma pergunta de continuação — não com o log técnico de ações internas, que é um campo separado (`_log_entries`). Essa mesma transcrição humana (janelada) também volta a entrar no contexto enviado à IA na pergunta de continuação seguinte (ver `utils.py` em "Arquivos-Chave") — sem ela, a IA não tinha como saber o que já havia sido perguntado/respondido antes de retomar.

**Agendamento de tarefas:** a IA (ou o usuário, pedindo no chat) pode chamar `schedule_task_tool` pra persistir um comando + horário futuro em `scheduler_store.py` (mesmo arquivo `.db` das sessões, tabela própria). Um `TaskScheduler` (`scheduler.py`) verifica periodicamente (`scheduler_check_interval`, padrão 30s) se alguma venceu; quando vence, o **fire-time gate** em `GlobalPlugin._on_scheduled_task_due` (`__init__.py`) decide se é seguro disparar (cortina de tela, automação já em andamento, entrada recente do teclado) e, se for, avisa por voz+beep e espera uma **janela de graça cancelável** (`GraceCountdownDialog`, `scheduler_confirm_grace_seconds`) antes de rodar `_run_ai_loop` de verdade — nunca silenciosamente. Tarefas que venceram enquanto o NVDA estava fechado nunca rodam nem são descartadas sozinhas: ficam marcadas `missed` e aparecem numa revisão (`SchedulerDialog` em modo `missed_mode`) no próximo início do NVDA (tarefas **recorrentes**, ver abaixo, nunca entram nessa revisão — disparam normalmente na próxima vez que o scheduler rodar, recalculando a partir de agora). `NVDA+Shift+A` abre `SchedulerDialog` pra gerenciar (cancelar/excluir) tarefas pendentes.

**Monitoramento recorrente:** `schedule_task_tool` aceita `recurrence_minutes` opcional — em vez de terminar depois de rodar, a linha em `scheduler_store.py` volta a ficar `pending` com um novo `run_at` (`reschedule_occurrence()`), até bater `max_occurrences`/`recurrence_until` (contados pelo próprio scheduler, nunca pela IA) ou a IA chamar `done_tool(stop_recurrence=true)`. Cada ocorrência recebe o resultado estruturado da checagem anterior (`last_result_json`, injetado no texto do comando) pra comparar; se `notify_only_on_change=true` e `done_tool(changed=false)`, o resultado só é registrado — sem abrir o chat nem falar o resumo (uma falha, porém, é **sempre** anunciada). O aviso sonoro + janela de graça de pré-disparo continua ocorrendo em toda ocorrência — não há como saber de antemão se vai mudar algo.

**Cancelamento:** `NVDA+Shift+A` lista/cancela/exclui tarefas **antes** de disparar; `NVDA+Shift+C` cancela uma automação **em execução** a qualquer momento (`_cancel_event`, checado no topo de cada turno e dentro de `ActionProcessor.run_batch` via `should_cancel`) — único gesto deste addon que não existia antes do suporte a tarefas longas. Terceiro desfecho possível (distinto de sucesso/falha): não soa o beep de falha, e uma tarefa agendada cancelada em pleno turno vira `cancelled`, nunca `failed`.

## Ações Disponíveis (ver `action_tools.py`)

| Tool | Execução |
|------|----------|
| `click_tool` / `right_click_tool` / `double_click_tool` | `mouseHandler` do NVDA |
| `type_tool` (com `press_enter` opcional) | Cola via clipboard (Ctrl+V), restaura clipboard original |
| `keyboard_tool` | `keybd_event` — mapeamento de 80+ teclas em `action_executor.py` |
| `scroll_tool` | `mouse_event` com fallback para Page Up/Down |
| `wait_tool` | Não faz nada além de esperar, até 10s (evita ações espúrias enquanto a tela carrega) |
| `long_wait_tool` | Nível do `BatchPlan` (como `ask_user_tool`/`done_tool`) — pausa a MESMA automação por até `long_wait_max_seconds` (padrão 120s), interrompível por `NVDA+Shift+C`, sem passar por `run_batch`/circuit breaker. Pra esperar um processo de fundo (build, upload) terminar sem perder histórico/notas nem reagendar — ver ARCHITECTURE.md |
| `app_tool` | Abre um programa pelo nome (`os.startfile`, com fallback `cmd /c start`) — 1 chamada em vez de Win+R + digitar + Enter |
| `schedule_task_tool` / `list_scheduled_tasks_tool` / `cancel_scheduled_task_tool` | `kind="support"` — gravam/leem `scheduler_store.py` direto, nunca mexem na tela (ver ARCHITECTURE.md). `schedule_task_tool` aceita `recurrence_minutes`/`notify_only_on_change`/`max_occurrences`/`recurrence_until` opcionais, pra monitoramento recorrente (ex: "veja o preço 3x em 30min") em vez de só agendamento único |
| `record_note_tool` | `kind="support"` — anota um fato numa tarefa longa pra ele nunca ser esquecido, mesmo depois de sair da janela do log textual (ver `ConversationHistory._notes`) |
| `ask_user_tool` | Pausa a automação e pergunta ao usuário num `wx.TextEntryDialog` síncrono — nunca combinada com outra tool |
| `shell_tool` | Executa um comando no `cmd.exe` **só depois de confirmação explícita obrigatória** (diálogo Sim/Não, foco padrão em Não); desligada por padrão (`enable_shell_tool=False`) |
| `done_tool` | Declara a tarefa concluída, com um `summary` obrigatório do que foi visto. Em checagem recorrente, também aceita `changed`/`result_data`/`stop_recurrence` opcionais |

## Arquivos-Chave

Detalhamento de cada um (com trechos de código e o raciocínio por trás) está em [ARCHITECTURE.md](ARCHITECTURE.md). Resumo:

- **`addon_config.py`** — constantes internas (delays, `MAX_ACTIONS_PER_TURN`, `MAX_HISTORY_LOG_ITEMS`, `MAX_SCREENSHOT_DIMENSION`, `AI_OPERATOR_PROMPT_TEMPLATE`, `SAFETY_SETTINGS`) + leitura das configurações do usuário via `config.conf["ElizIA"]` (= `addon_config.CONFIG_SECTION`, igual a `ADDON_NAME`).
- **`action_tools.py`** — schema tipado de cada tool (`get_tool()`, `validate_args()`, `TOOL_NAME_TO_ACTION_TYPE`).
- **`gemini_client.py`** — `GeminiClient.generate_content()` devolve um `TurnResult` (function_calls, text, error, model_content, finish_reason); nunca usa automatic function calling da SDK. Criado sob demanda em `_run_ai_loop`, não no `__init__` do plugin (a SDK valida a API key no construtor).
- **`action_processor.py`** — `process_function_calls()` planeja (`BatchPlan`/`BatchItem`); `run_batch()` executa com abort-on-change e circuit breaker de loop; ambos puros/testáveis sem NVDA.
- **`__init__.py`** — `_run_ai_loop()` orquestra; `_execute_batch()` é um wrapper fino em volta de `ActionProcessor.run_batch`.
- **`utils.py`** — `ConversationHistory`: histórico O(1) por turno (task + log textual janelado + último `Content` do modelo), nunca reenvia screenshots antigos — **mais** a transcrição humana (`_conversation_log`, via `record_conversation_message`), que repopula o chat ao retomar uma seção **e** entra no contexto da IA via `conversation_log_text()` (mesma janela de primeiro-item+omitidos+recentes de `history_log_text()`, teto `MAX_CONVERSATION_LOG_ITEMS`) — sem isso, uma pergunta de continuação sobre a própria conversa (ex: "qual foi minha primeira mensagem?") não tinha como ter resposta correta, já que `_task_text` é substituído a cada chamada de `_run_ai_loop`, nunca acrescentado. E as notas de `record_note_tool` (`_notes`/`notes_text()`), que ao contrário do log textual **nunca** são janeladas — sobrevivem em tarefas com mais turnos do que `MAX_HISTORY_LOG_ITEMS`. `to_dict()`/`from_dict()` serializam os 5 campos pra persistência de seções.
- **`screen_capture.py`** — `capture_fullscreen()` com downscale (`_maybe_downscale`), mantendo as dimensões reais da tela pra conversão de coordenadas.
- **`session_store.py`** — `SessionStore`: CRUD de seções em SQLite (`create_session`, `update_session`, `list_sessions`, `load_session`, `rename_session`, `delete_session`), com poda automática pelo limite `max_sessions`. Puro stdlib, testável sem NVDA — mas `sqlite3` precisou ser vendorizado em `lib/` (ver "Dependências bundled" abaixo), já que não vem na build de Python do NVDA.
- **`sessions_dialog.py`** — `SessionsDialog`: lista (`wx.ListCtrl`) + botões Abrir/Retomar, Nova sessão, Renomear, Excluir — segue os mesmos padrões de acessibilidade de `chat_dialog.py`.
- **`scheduler_store.py`** — `SchedulerStore`: CRUD de tarefas agendadas em SQLite (mesma disciplina de conexão-por-chamada de `session_store.py`, mesmo arquivo `.db`, tabela `scheduled_tasks` própria). `get_due_tasks`/`get_missed_tasks` são as duas consultas usadas pelo polling ao vivo e pelo catch-up de inicialização, respectivamente.
- **`scheduler.py`** — `TaskScheduler`: só o timer+polling (`gui.NonReEntrantTimer` manipulado via `wx.CallAfter`, idiom copiado do `AutoUpdateChecker` do próprio NVDA core); delega toda decisão de "disparar agora?" pro callback `on_due_task` injetado (`GlobalPlugin._on_scheduled_task_due`). `seconds_since_last_input()` (ctypes/`GetLastInputInfo`) é a heurística de "usuário digitando agora" usada no fire-time gate.
- **`scheduler_dialog.py`** — `SchedulerDialog` (gerenciamento normal ou, com `missed_mode=True`, revisão de tarefas perdidas) e `GraceCountdownDialog` (janela de aviso + contagem regressiva cancelável, Esc ou botão cancela).

## Convenções de Desenvolvimento

- **Dependências bundled** — SDK oficial `google-genai` incluído em `addon/globalPlugins/operadorIA/lib/` com todas as dependências (anyio, cffi, cryptography, pydantic, etc.). wxPython (`wx`) **não** está aqui — é fornecido pelo próprio NVDA. **`sqlite3` também precisou ser vendorizado** (`lib/sqlite3/` + `lib/_sqlite3.pyd` + `lib/sqlite3.dll`) — diferente das outras libs, não é algo baixado via pip; são os 3 arquivos equivalentes copiados de um CPython 3.13 win_amd64 comum (mesmo ABI já usado pelos outros `.pyd` bundled aqui), porque a build de Python embutida no NVDA não inclui esse módulo de fábrica. Se atualizar o Python usado para gerar os outros `.pyd` (ex: pular pra cp314), os 3 arquivos do sqlite3 precisam ser recopiados de uma instalação da MESMA versão, senão o ABI não casa.
- **Path de imports** — `__init__.py` adiciona `lib/` ao `sys.path` e chama `os.add_dll_directory(lib_path)` antes de importar; permite usar `google.genai`/`sqlite3` sem instalação global.
- **Thread safety** — toda atualização de UI usa `wx.CallAfter()`; nenhuma chamada de API no thread principal. `_speech_history` é protegido por `threading.Lock` (escrito pela thread do `eventQueue` do NVDA, lido pela thread de automação). `self._automation_lock` (`threading.Lock`) é o único jeito de reservar uma automação — nunca leia/escreva um bool solto pra isso: os 4 pontos de entrada (atalho, chat, retomada de sessão, scheduler) chamam `_try_start_automation()`/`_finish_automation()`; `.locked()` serve só pra um aviso prévio não-consumptivo antes de abrir um diálogo.
- **Coordenadas** — a IA sempre responde em escala 0-1000; a conversão para pixels reais ocorre em `ActionProcessor._scale_coordinates`, com base na resolução REAL da tela (não na imagem, possivelmente redimensionada, que a IA recebeu).
- **Imports** — use `from . import addon_config` (nunca `import config`, que é o módulo global do NVDA); todos os módulos usam `addon_config` para constantes locais.
- **Gestos de teclado (atalhos)** — seguem o padrão clássico de scriptable object do NVDA (o mesmo usado por outros addons, ex. AI Content Describer), não `@scriptHandler.script(gesture=...)`: `scriptCategory` (atributo de classe) agrupa os comandos na tela de Gestos de Entrada; cada `script_xxx(self, gesture)` tem sua descrição via `script_xxx.__doc__ = "..."` (não um parâmetro de decorator); as teclas padrão ficam só no dict `__gestures` no fim da classe (`"kb:NVDA+shift+r": "iniciarOperadorIA"`, etc.) — isso é só o valor INICIAL, o usuário reatribui livremente em Preferências > Gestos de entrada sem precisar editar código. Pra adicionar um atalho novo: criar o `script_*`, dar um `__doc__`, e acrescentar uma entrada em `__gestures`.
- **Nunca** deixe uma ação desconhecida/mal validada cair num comportamento padrão silencioso (ex: "virar clique") — para um usuário cego, nenhuma ação é mais seguro que uma ação errada. Sempre recuse explicitamente (`validate_args`) e devolva o erro pra IA tentar de novo.
- **Testes** — ver [`tests/README.md`](tests/README.md) e a seção "Como testar" do [ARCHITECTURE.md](ARCHITECTURE.md#como-testar-sem-o-nvda-rodando). A maior parte da lógica (tools, planejamento/execução de lote, histórico, downscale) tem teste standalone; só mouse/teclado/clipboard reais e o loop fim-a-fim exigem o NVDA rodando.
- **Investigação do comportamento do NVDA** — durante a etapa de investigação, se for preciso saber como o NVDA faz algo internamente, ou se existe alguma função/API exposta pelo NVDA para uso em addons, consultar o código-fonte do NVDA em `D:\repo\nvda` (clone local do repositório oficial) em vez de supor ou confiar só em memória/documentação externa.

## Tradução (i18n)

O addon usa a convenção oficial do NVDA para tradução: `addonHandler.initTranslation()` no
topo de cada módulo que tenha strings visíveis ao usuário, injetando `_`/`ngettext`/`pgettext`/
`npgettext` como globals **daquele módulo** (não globalmente — cada módulo chama de novo). A
infra de build (SCons) já vinha pronta do template (`site_scons/site_tools/gettexttool/`, alias
`pot`, builders `gettextMoFile`/`NVDATranslatedManifest`) — só faltava usá-la.

- **Convenção de idioma** — os literais no código são em **inglês** (msgid), convenção gettext
  padrão, não em português (apesar de ser o idioma nativo do autor e o `baseLanguage` de
  `buildVars.py`, que é um sistema separado — ver abaixo). A tradução pra português mora em
  `addon/locale/pt_BR/LC_MESSAGES/nvda.po`, mantida à mão (sem Crowdin configurado ainda, apesar
  do grupo de dependências `l10n` no `pyproject.toml` já prever isso).
- **Módulos com `addonHandler.initTranslation()`** (têm string de UI real): `__init__.py`,
  `action_tools.py` (só as mensagens de erro de `validate_args()`, não as descrições de tool
  mandadas pra IA), `action_processor.py`, `gemini_client.py`, `chat_dialog.py`,
  `sessions_dialog.py`, `scheduler_dialog.py`, `settings.py`. A chamada vai logo depois do bloco
  de imports, **antes** de qualquer lista/dict de nível de módulo que já use `_()` — crítico em
  `settings.py` (`TEMPERATURE_PRESETS`) e em `scheduler_dialog.py` (`_STATUS_LABELS`).
- **O que NUNCA é traduzido** — strings que só a IA vê (schema de `FunctionDeclaration` em
  `action_tools.py`, o prompt de sistema em `addon_config.py`, o log textual de `utils.py`, os
  erros de `action_executor.py`, que só viram `function_response`/log, nunca `plan.explanation`
  nem `ui.message`); valores de banco como `scheduler_store.STATUS_*` (ficam em inglês, são dado
  persistido no SQLite — `scheduler_dialog.py` tem um `_STATUS_LABELS` só pra exibição, com
  fallback pro valor cru se um status não estiver mapeado); e os nomes de idioma em
  `settings.py:LANGUAGES` (convenção: nome nativo, não traduzido — não confundir com o catálogo
  gettext da UI, que é um sistema totalmente separado de `response_language`, o idioma em que a
  IA responde).
- **Exceção deliberada** — `ConversationHistory._conversation_log` (sender/message gravados via
  `record_conversation_message`) usa rótulos JÁ TRADUZIDOS (`_("You")`, `addon_config.ADDON_NAME`)
  porque o mesmo campo também popula a tela do chat ao retomar uma seção. Desde que
  `conversation_log_text()` passou a entrar no contexto da IA (ver `utils.py` em
  "Arquivos-Chave"), esse rótulo viaja no idioma da UI do NVDA no momento em que a mensagem foi
  registrada, não necessariamente no idioma de `response_language` — aceito de propósito em vez
  de duplicar o campo em dois formatos só pela etiqueta do remetente; a IA não depende do idioma
  do rótulo para entender quem disse o quê.
- **Zona cinzenta** — mensagens de `action_processor.py`/`action_tools.py` que alimentam
  `BatchPlan.explanation` (ex: `_describe_action()`, erros de `validate_args()`) SÃO traduzidas,
  mesmo sendo também lidas pela IA via `function_response`: elas acabam faladas ao usuário via
  `ui.message` sempre que a IA não gera texto livre no turno (comum, já que a API roda em
  `mode=ANY`). Já os erros de `action_executor.py` (aparência superficial igual) nunca passam por
  `plan.explanation`, só por `function_response`/log — por isso ficam intocados. A distinção é de
  fluxo de dados, não de onde a string está no código.
- **Controle de fluxo nunca depende de texto traduzido** — `ActionProcessor.run_batch()` usa um
  marcador explícito (`item.response["truncated"]`) pra diferenciar "ação truncada pelo limite do
  turno" de "ação que falhou", exatamente pra não quebrar quando a mensagem de erro for traduzida
  (antes checava a substring `"limite de"` dentro do texto — uma armadilha clássica de i18n).
- **Mnemônicos (`&X`) de botão** — cada diálogo (chat, sessões, agendamento normal/perdido) tem
  seu próprio conjunto; precisam ser únicos DENTRO de cada diálogo, em TODOS os idiomas
  suportados (inglês incluído, já que é o fallback sem catálogo) — um msgid como `_("&Close")`
  compartilhado entre 3 diálogos facilmente colide com outro botão do mesmo diálogo se não for
  checado manualmente a cada idioma.
- **Comandos locais** — `uv run scons pot` gera `<addon_name>.pot` (precisa de `xgettext`/`msgfmt`
  do GNU gettext no PATH; no Windows, `winget install mlocati.GetText` resolve, sem equivalente
  nativo). Validar um `.po` à mão: `msgfmt --check -o NUL addon/locale/<lang>/LC_MESSAGES/nvda.po`
  (confere plural/formatação `%`) e `msgcmp addon/locale/<lang>/LC_MESSAGES/nvda.po
  <addon_name>.pot` (confere que nenhum msgid do código ficou sem tradução — a única diferença
  esperada hoje são as 3 strings de `buildVars.py`, que usam um `_()` de extração só pra isso, já
  em português, deliberadamente fora deste catálogo).
- **Testes sem NVDA** — `addonHandler.initTranslation()` de verdade depende do registro de addons
  carregados pelo NVDA (`getCodeAddon`), que não existe fora dele. `tests/_harness.py` registra um
  stub de `addonHandler` automaticamente (incondicional, no import do próprio `_harness.py`) com
  `_`/`ngettext`/etc como identidade — nenhum `test_*.py` precisa chamar nada a mais.

## Configurações Disponíveis (via Painel NVDA)

| Chave config.conf | Padrão | Descrição |
|-------------------|--------|-----------|
| `gemini_api_key` | `""` | Chave da API Google Gemini |
| `gemini_model` | `gemini-2.5-flash` | Modelo Gemini |
| `ai_temperature` | `0.4` | Criatividade (0.0–2.0) |
| `max_turns` | `15` | Máximo de iterações por tarefa (1-300) |
| `turn_delay` | `2.0` | Delay entre iterações (segundos) |
| `api_timeout` | `30` | Timeout das chamadas (segundos) |
| `response_language` | `pt-br` | Idioma das respostas do AI |
| `max_sessions` | `50` | Máximo de seções salvas (1-500); poda automática ao exceder, aplicada só na próxima sessão criada |
| `scheduler_check_interval` | `30` | Intervalo de verificação de tarefas agendadas (5-3600 seg) |
| `scheduler_confirm_grace_seconds` | `20` | Janela de aviso cancelável antes de rodar uma tarefa agendada (5-120 seg) |
| `ask_user_timeout_seconds` | `120` | Teto de espera por resposta do usuário em `ask_user_tool` (10-600 seg) |
| `long_wait_max_seconds` | `120` | Teto por chamada de `long_wait_tool`, a pausa longa dentro da mesma tarefa (10-600 seg) |
| `enable_shell_tool` | `False` | Se `shell_tool` é oferecida ao modelo — confirmação obrigatória continua valendo independentemente disso |
| `shell_command_timeout_seconds` | `15` | Teto de execução de um comando de `shell_tool` já confirmado (1-120 seg) |

## Segurança

- **Configurações sensíveis** lidas do painel NVDA via `config.conf["ElizIA"]` (não hardcoded). Se a chave estiver vazia, o addon avisa e não trava a inicialização do NVDA.
- **Clipboard** é salvo e restaurado antes/depois de cada `execute_type_action()` para evitar perda de dados.
- **Histórico de fala** é temporário (deque em memória, com deduplicação de frases repetidas consecutivas, não persiste em disco).
- **`BLOCK_NONE`** nas safety settings do Gemini é intencional — o addon precisa lidar com qualquer conteúdo de tela.
- **Cortina de Tela do NVDA** — a automação recusa iniciar (ou continuar numa pergunta de chat) enquanto a Cortina de Tela estiver ativa, porque `capture_fullscreen()` (GDI puro, via `wx.ScreenDC`) captura o preto da cortina como se fosse a tela real, e a IA "ficaria perdida" alucinando ações sobre uma tela que não existe. Checado em `screen_curtain.is_screen_curtain_active()`, chamado só no início de cada `_run_ai_loop` (não a cada turno) — cobre as duas APIs do NVDA pra isso (`vision.handler` até 2025.3.x, módulo `screenCurtain` a partir de 2026.1), com fail-open (`False`) se a checagem em si falhar.
- **Nenhuma ação é executada sem validação de schema primeiro** (`action_tools.validate_args`) — ver convenção acima.
- **Seções (conversas)** são persistidas deliberadamente em `session_store.py` (SQLite, no perfil do NVDA) — diferente do histórico de fala, isso é intencional e é o ponto central desta funcionalidade. Fica **desabilitado em modo seguro** (`globalVars.appArgs.secure`, tela de login do Windows) pelo mesmo motivo do `SettingsPanel`: nunca persistir comandos digitados nessa tela.
- **Tarefas agendadas disparando sem o usuário presente** — `ActionExecutor` move o mouse/teclado de verdade; um disparo silencioso atropelaria o que o usuário estiver fazendo. Por isso o fire-time gate (`GlobalPlugin._on_scheduled_task_due`) nunca dispara direto: checa cortina de tela, automação já em andamento (`_automation_lock`, sem TOCTOU — ver Convenções) e entrada recente de teclado/mouse (`seconds_since_last_input`), e SEMPRE passa por um aviso sonoro + janela de graça cancelável antes de rodar — inclusive em cada ocorrência de uma tarefa **recorrente** (não existe caminho silencioso de verificação nesta arquitetura, só a notificação do RESULTADO pode ser condicional via `notify_only_on_change`). Tarefas perdidas (NVDA fechado no horário) nunca rodam nem são descartadas sozinhas — ficam pra revisão manual; tarefas recorrentes nunca entram nessa revisão, só disparam normalmente na próxima vez que o scheduler rodar.
- **Cancelamento de automação em execução** (`NVDA+Shift+C`) — fail-safe, nunca bypassa nenhuma outra checagem de segurança: só sinaliza `_cancel_event`, que `_run_ai_loop`/`run_batch` checam nos mesmos pontos onde já checam falha/mudança de tela. Não interrompe uma confirmação de `shell_tool` já em andamento (o usuário já tem a opção "Não" ali) nem a janela de graça de uma tarefa agendada (que já tem seu próprio Cancelar/Esc).
- **`shell_tool`** — execução de comando arbitrário é o item de maior risco desta superfície: desligada por padrão (`enable_shell_tool`) e, mesmo ligada, nunca roda sem uma confirmação explícita (comando + motivo lidos em voz alta, foco padrão em "Não") — ver `GlobalPlugin._confirm_shell_command`. `ActionExecutor.execute_action` recusa `shell_command` explicitamente se chegar até ele sem essa confirmação (defesa contra bug de wiring, nunca um caminho silencioso).
- **Modo seguro e ferramentas de apoio** — `scheduler_store`/`SchedulerStore` seguem a mesma guarda de `session_store` (`None` em modo seguro). Se a IA tentar chamar uma tool "support" (`schedule_task_tool` etc.) nesse contexto, o handler simplesmente não está disponível e `process_function_calls` devolve um erro explícito — nunca uma falha silenciosa.

## Compatibilidade

- NVDA mínimo: 2026.1 (primeira versão com Python 3.13/64-bit — os binários cp313
  vendorizados em `lib/`, como `_sqlite3.pyd` e `_pydantic_core*.pyd`, não carregam em
  versões anteriores, que usavam Python 3.7/32-bit; ver `source/addonAPIVersion.py` no
  repositório do NVDA)
- Última versão testada: 2026.4
- Python: versão embutida no NVDA (3.13, 64-bit)
- Windows: qualquer versão suportada pelo NVDA 2026.1+
