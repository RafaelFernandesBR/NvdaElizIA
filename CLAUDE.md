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
parte do addon instalado. Detalhes de build/CI (buildVars.py, sconstruct, prek.toml,
workflows) estão documentados no `readme.md` da raiz e nos comentários desses próprios
arquivos — não duplicados aqui.

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
ARCHITECTURE.md                     # Detalhamento técnico do pipeline de IA e gestão de contexto
tests/                              # Testes standalone (sem NVDA, sem pytest) — ver tests/README.md
```

## Como Funciona

**Atalho:** `NVDA+Shift+R` (padrão) → abre diálogo de entrada → inicia `_run_ai_loop()` em thread separada. Como qualquer comando NVDA, é remapeável pelo usuário em **Preferências > Gestos de entrada** (categoria "ElizIA Visual") — não é fixo no código (ver "Gestos de teclado" nas Convenções abaixo).

**Loop de automação** (máx. 50 iterações, configurável, padrão 15), por turno:
1. Captura screenshot (com downscale se exceder `MAX_SCREENSHOT_DIMENSION`) → base64 PNG
2. Monta `contents=` com, no máximo, 3 `Content` — só a screenshot ATUAL viaja na requisição; turnos anteriores entram só como um log textual janelado (`ConversationHistory`)
3. Chama o Gemini com as tools de `action_tools.py` e `mode=ANY` (o modelo sempre devolve uma ou mais function calls, nunca texto solto)
4. `ActionProcessor.process_function_calls()` planeja um lote de até `MAX_ACTIONS_PER_TURN` ações, validando parâmetros antes de executar
5. `ActionProcessor.run_batch()` executa o lote em sequência, abortando o restante se uma ação falhar ou a tela mudar no meio
6. O resumo do turno é gravado no histórico; se a mesma ação se repetir sem a tela mudar, um circuit breaker **interrompe a automação de verdade** (não é só um aviso)
7. Repete até `done_tool` ser chamado (com um resumo do que foi visto) ou atingir `max_turns`

**Após conclusão:** abre `OperatorChatDialog` para perguntas de acompanhamento (reusa o mesmo `_run_ai_loop`, incluindo o histórico já acumulado da tarefa).

**Seções (sessions):** cada tarefa nova vira uma linha em `session_store.py` (SQLite, no perfil ativo do NVDA); a cada turno, `ConversationHistory` é persistida automaticamente (autosave). `NVDA+Shift+O` (padrão, também remapeável) abre `SessionsDialog` — lista as seções salvas e permite abrir/retomar, renomear ou excluir. Retomar uma seção reconstrói `ConversationHistory` a partir do disco e reabre o chat já preenchido com a **transcrição humana da conversa** ("Você: ..." / "Operador IA: ..."), pronto para uma pergunta de continuação — não com o log técnico de ações internas, que é um campo separado (`_log_entries`) usado só como contexto para a IA.

**Agendamento de tarefas:** a IA (ou o usuário, pedindo no chat) pode chamar `schedule_task_tool` pra persistir um comando + horário futuro em `scheduler_store.py` (mesmo arquivo `.db` das sessões, tabela própria). Um `TaskScheduler` (`scheduler.py`) verifica periodicamente (`scheduler_check_interval`, padrão 30s) se alguma venceu; quando vence, o **fire-time gate** em `GlobalPlugin._on_scheduled_task_due` (`__init__.py`) decide se é seguro disparar (cortina de tela, automação já em andamento, entrada recente do teclado) e, se for, avisa por voz+beep e espera uma **janela de graça cancelável** (`GraceCountdownDialog`, `scheduler_confirm_grace_seconds`) antes de rodar `_run_ai_loop` de verdade — nunca silenciosamente. Tarefas que venceram enquanto o NVDA estava fechado nunca rodam nem são descartadas sozinhas: ficam marcadas `missed` e aparecem numa revisão (`SchedulerDialog` em modo `missed_mode`) no próximo início do NVDA. `NVDA+Shift+A` abre `SchedulerDialog` pra gerenciar (cancelar/excluir) tarefas pendentes.

## Ações Disponíveis (ver `action_tools.py`)

| Tool | Execução |
|------|----------|
| `click_tool` / `right_click_tool` / `double_click_tool` | `mouseHandler` do NVDA |
| `type_tool` (com `press_enter` opcional) | Cola via clipboard (Ctrl+V), restaura clipboard original |
| `keyboard_tool` | `keybd_event` — mapeamento de 80+ teclas em `action_executor.py` |
| `scroll_tool` | `mouse_event` com fallback para Page Up/Down |
| `wait_tool` | Não faz nada além de esperar (evita ações espúrias enquanto a tela carrega) |
| `app_tool` | Abre um programa pelo nome (`os.startfile`, com fallback `cmd /c start`) — 1 chamada em vez de Win+R + digitar + Enter |
| `schedule_task_tool` / `list_scheduled_tasks_tool` / `cancel_scheduled_task_tool` | `kind="support"` — gravam/leem `scheduler_store.py` direto, nunca mexem na tela (ver ARCHITECTURE.md) |
| `ask_user_tool` | Pausa a automação e pergunta ao usuário num `wx.TextEntryDialog` síncrono — nunca combinada com outra tool |
| `shell_tool` | Executa um comando no `cmd.exe` **só depois de confirmação explícita obrigatória** (diálogo Sim/Não, foco padrão em Não); desligada por padrão (`enable_shell_tool=False`) |
| `done_tool` | Declara a tarefa concluída, com um `summary` obrigatório do que foi visto |

## Arquivos-Chave

Detalhamento de cada um (com trechos de código e o raciocínio por trás) está em [ARCHITECTURE.md](ARCHITECTURE.md). Resumo:

- **`addon_config.py`** — constantes internas (delays, `MAX_ACTIONS_PER_TURN`, `MAX_HISTORY_LOG_ITEMS`, `MAX_SCREENSHOT_DIMENSION`, `AI_OPERATOR_PROMPT_TEMPLATE`, `SAFETY_SETTINGS`) + leitura das configurações do usuário via `config.conf["ElizIA"]` (= `addon_config.CONFIG_SECTION`, igual a `ADDON_NAME`).
- **`action_tools.py`** — schema tipado de cada tool (`get_tool()`, `validate_args()`, `TOOL_NAME_TO_ACTION_TYPE`).
- **`gemini_client.py`** — `GeminiClient.generate_content()` devolve um `TurnResult` (function_calls, text, error, model_content, finish_reason); nunca usa automatic function calling da SDK. Criado sob demanda em `_run_ai_loop`, não no `__init__` do plugin (a SDK valida a API key no construtor).
- **`action_processor.py`** — `process_function_calls()` planeja (`BatchPlan`/`BatchItem`); `run_batch()` executa com abort-on-change e circuit breaker de loop; ambos puros/testáveis sem NVDA.
- **`__init__.py`** — `_run_ai_loop()` orquestra; `_execute_batch()` é um wrapper fino em volta de `ActionProcessor.run_batch`.
- **`utils.py`** — `ConversationHistory`: histórico O(1) por turno (task + log textual janelado + último `Content` do modelo), nunca reenvia screenshots antigos — **mais** a transcrição humana (`_conversation_log`, via `record_conversation_message`), que nunca entra no contexto da IA e existe só pra repopular o chat ao retomar uma seção. `to_dict()`/`from_dict()` serializam os 4 campos pra persistência de seções.
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

## Configurações Disponíveis (via Painel NVDA)

| Chave config.conf | Padrão | Descrição |
|-------------------|--------|-----------|
| `gemini_api_key` | `""` | Chave da API Google Gemini |
| `gemini_model` | `gemini-2.5-flash` | Modelo Gemini |
| `ai_temperature` | `0.4` | Criatividade (0.0–2.0) |
| `max_turns` | `15` | Máximo de iterações por tarefa (1-50) |
| `turn_delay` | `2.0` | Delay entre iterações (segundos) |
| `api_timeout` | `30` | Timeout das chamadas (segundos) |
| `response_language` | `pt-br` | Idioma das respostas do AI |
| `max_sessions` | `50` | Máximo de seções salvas (1-500); poda automática ao exceder, aplicada só na próxima sessão criada |
| `scheduler_check_interval` | `30` | Intervalo de verificação de tarefas agendadas (5-3600 seg) |
| `scheduler_confirm_grace_seconds` | `20` | Janela de aviso cancelável antes de rodar uma tarefa agendada (5-120 seg) |
| `ask_user_timeout_seconds` | `120` | Teto de espera por resposta do usuário em `ask_user_tool` (10-600 seg) |
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
- **Tarefas agendadas disparando sem o usuário presente** — `ActionExecutor` move o mouse/teclado de verdade; um disparo silencioso atropelaria o que o usuário estiver fazendo. Por isso o fire-time gate (`GlobalPlugin._on_scheduled_task_due`) nunca dispara direto: checa cortina de tela, automação já em andamento (`_automation_lock`, sem TOCTOU — ver Convenções) e entrada recente de teclado/mouse (`seconds_since_last_input`), e SEMPRE passa por um aviso sonoro + janela de graça cancelável antes de rodar. Tarefas perdidas (NVDA fechado no horário) nunca rodam nem são descartadas sozinhas — ficam pra revisão manual.
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
