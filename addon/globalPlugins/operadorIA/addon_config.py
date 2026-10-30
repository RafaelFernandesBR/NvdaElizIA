# -*- coding: utf-8 -*-
"""
Configurações do ElizIA Visual
"""

# --- Informações do Addon
ADDON_NAME = "ElizIA"
ADDON_FULL_NAME = "ElizIA Visual"
# Mesmo valor do addon_name em buildVars.py — é o identificador instalado
# (pasta em %APPDATA%\nvda\addons\<nome>\) e a seção de config.conf.
CONFIG_SECTION = ADDON_NAME

# --- Delays de Loop e Ação
SHORT_DELAY = 0.5
# Antes de cada ação física do lote. Reduzido de 1.0 — seu papel de "dar
# tempo da UI reagir" passa a ser coberto, uma camada acima, pela espera
# adaptativa pós-ação (ver screen_wait.wait_until_stable em __init__.py).
ACTION_DELAY = 0.3

# --- Delays de Ação Específica (action_executor.py)
# Timing de baixo nível de SO/tecla sintética — sem sinal visual pra
# verificar, ficam fixos de propósito (ver ARCHITECTURE.md).
CLICK_DELAY = 0.4
KEY_PRESS_DELAY = 0.05
DOUBLE_CLICK_DELAY = 0.1
KEYBOARD_KEY_DELAY = 0.2
# Só usado por scroll (_send_wheel_event) e por execute_keyboard_shortcut —
# os usos ligados à digitação têm constantes dedicadas abaixo
# (CLIPBOARD_*_SETTLE_DELAY), justamente pra poder reduzi-los sem afetar
# scroll/atalhos.
PASTE_DELAY = 0.5

# Antes, um único RIGHT_CLICK_POST_DELAY cobria 3 papéis diferentes (pós-
# clique genérico, pós-release de tecla do paste, pré-Enter da digitação) —
# separados pra poder ajustar cada um sem efeito colateral nos outros.
# MOUSE_CLICK_POST_DELAY e PASTE_KEY_RELEASE_DELAY ficam pequenos porque são
# redundantes com a espera adaptativa que já roda uma camada acima, após
# qualquer ação física (ver screen_wait.wait_until_stable, chamado em
# GlobalPlugin._execute_batch). TYPE_PRE_ENTER_DELAY não pode ser cortado do
# mesmo jeito: o Enter é parte da MESMA chamada de ação, antes de qualquer
# espera externa rodar.
MOUSE_CLICK_POST_DELAY = 0.1
PASTE_KEY_RELEASE_DELAY = 0.15
TYPE_PRE_ENTER_DELAY = 0.35

# Settle de escrever/restaurar o clipboard (_paste_text/execute_type_action)
# — reduzidos pelo mesmo motivo de MOUSE_CLICK_POST_DELAY acima.
CLIPBOARD_WRITE_SETTLE_DELAY = 0.15
CLIPBOARD_RESTORE_SETTLE_DELAY = 0.15

# Teto de espera pelo evento event_gainFocus do NVDA (ver GlobalPlugin,
# __init__.py) depois de clicar num campo antes de colar texto — substitui
# os antigos CLIPBOARD_RESTORE_DELAY (0.8s) + FINAL_CLIPBOARD_DELAY (1.5s)
# fixos, que não verificavam nada. Se o clique não mudar o foco de
# controle (ex: campo já focado), expira no teto sem travar.
TYPE_FOCUS_WAIT_TIMEOUT = 0.5
# Fallback de sleep fixo só usado se execute_type_action for chamado sem
# focus_event (fora do fluxo normal do GlobalPlugin, ex. chamada direta).
TYPE_CLICK_SETTLE_FALLBACK_DELAY = 0.3

# --- Configurações de Ação
BACKSPACE_REPETITIONS = 30
COORDINATE_SCALE = 1000

# --- História e Detecção
# Antes havia MAX_SPEECH_HISTORY=20 (capacidade do deque) e
# RECENT_SPEECH_COUNT=10 (quanto era de fato injetado no prompt) — os 10
# itens extras do deque nunca eram lidos por mais nada, então essa folga
# era só confusão. Uma constante só, usada tanto como maxlen do deque
# quanto como limite de injeção no prompt.
SPEECH_HISTORY_SIZE = 10
LOOP_DETECTION_THRESHOLD = 3
DEFAULT_APP_NAME = "Desconhecido"

# --- Códigos de Teclado Windows
MOUSEEVENTF_WHEEL = 0x0800

# --- UI Dialog (chat_dialog.py)
DIALOG_WIDTH = 700
DIALOG_HEIGHT = 500
COLUMN_SENDER_WIDTH = 80
COLUMN_MESSAGE_WIDTH = 500
HISTORY_LIST_HEIGHT = 200
HISTORY_LIST_FONT_SIZE = 9
MESSAGE_PREVIEW_LIMIT = 100

# --- Settings (settings.py)
DEFAULT_MODEL_INDEX = 0
DEFAULT_TEMP_INDEX = 1
TEMPERATURE_TOLERANCE = 0.01
DEFAULT_MAX_TURNS = 15
DEFAULT_TURN_DELAY = 2
DEFAULT_API_TIMEOUT = 30

# --- Retry/timeout da API Gemini (gemini_client.py)
# Liga o retry+backoff NATIVO do SDK google-genai (types.HttpRetryOptions) —
# sem isso, o SDK usa stop_after_attempt(1) por padrão, ou seja, ZERO
# retries. A chamada é interativa (o usuário está esperando ativamente a
# tarefa), por isso max_delay fica bem abaixo do default de 60s do SDK.
API_RETRY_ATTEMPTS = 3
API_RETRY_INITIAL_DELAY = 1.0
API_RETRY_MAX_DELAY = 8.0
API_RETRY_EXP_BASE = 2.0
API_RETRY_JITTER = 0.3

# --- Espera adaptativa de tela (screen_wait.py)
# Substitui sleeps fixos por polling de hash MD5 até a tela estabilizar
# (ou até o teto, que nunca bloqueia pra sempre). 2 leituras consecutivas
# iguais bastam pra considerar "estável" — o hash já é exato (pixel-perfect),
# então uma 3ª leitura só adicionaria latência sem ganho de confiança.
STABILITY_POLL_INTERVAL = 0.2
STABILITY_STABLE_READS = 2
STABILITY_MAX_WAIT_ACTION = 3.0

# --- Sessões (session_store.py / sessions_dialog.py)
# Poda automática roda só em create_session (nova tarefa) — mudar
# max_sessions no painel não afeta sessões já salvas até a próxima criação.
# sqlite3 é vendorizado em lib/ (ver comentário em __init__.py) — a build
# de Python embutida no NVDA não traz esse módulo.
SESSION_DB_FILENAME = f"{ADDON_NAME}_sessions.db"
DEFAULT_MAX_SESSIONS = 50
SESSION_TITLE_MAX_LENGTH = 60
SESSION_DATETIME_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"

# --- UI Dialog (sessions_dialog.py)
SESSIONS_DIALOG_WIDTH = 650
SESSIONS_DIALOG_HEIGHT = 450
SESSIONS_COLUMN_TITLE_WIDTH = 280
SESSIONS_COLUMN_ACTIVITY_WIDTH = 150
SESSIONS_COLUMN_TURNS_WIDTH = 80

# --- Agendamento de tarefas (scheduler.py / scheduler_store.py / scheduler_dialog.py)
# Mesmo arquivo .db das sessões (SESSION_DB_FILENAME acima) — tabela própria,
# contenção coberta pelo mesmo PRAGMA busy_timeout.
DEFAULT_SCHEDULER_CHECK_INTERVAL = 30
DEFAULT_SCHEDULER_CONFIRM_GRACE_SECONDS = 20
DEFAULT_ASK_USER_TIMEOUT_SECONDS = 120
DEFAULT_SHELL_COMMAND_TIMEOUT_SECONDS = 15
# Teto por chamada de long_wait_tool — mesma faixa de ask_user_timeout_seconds,
# já que ambos representam "por quanto tempo é razoável pausar esta automação
# no meio de uma tarefa" (ver _run_ai_loop/action_processor.py).
DEFAULT_LONG_WAIT_MAX_SECONDS = 120
# Segundos desde a última tecla/clique (GetLastInputInfo) abaixo dos quais o
# fire-time gate adia o disparo pro próximo tick, em vez de interromper uma
# rajada de teclas ativa — mitigação, não substitui o aviso+janela de graça.
SCHEDULER_RECENT_INPUT_THRESHOLD = 2.0
SCHEDULED_TASK_TITLE_PREFIX = "[Agendada] "
NOW_LOCAL_DISPLAY_FORMAT = "%A, %d/%m/%Y %H:%M"
# Teto de espera pela confirmação do shell_tool — separado de
# shell_command_timeout_seconds (que limita o COMANDO em si): este aqui dá
# tempo real do usuário ler e decidir, não é configurável no painel de
# propósito (é uma janela de segurança, não uma config de performance).
SHELL_CONFIRM_WAIT_SECONDS = 60
# Tamanho máximo de stdout/stderr devolvido como function_response do
# shell_tool — evita que uma saída gigante infle o contexto da IA.
SHELL_OUTPUT_TRUNCATE_CHARS = 2000

# --- UI Dialog (scheduler_dialog.py)
SCHEDULER_DIALOG_WIDTH = 650
SCHEDULER_DIALOG_HEIGHT = 450
SCHEDULER_COLUMN_TITLE_WIDTH = 280
SCHEDULER_COLUMN_RUN_AT_WIDTH = 150
SCHEDULER_COLUMN_STATUS_WIDTH = 100

# --- Screen Capture
DEFAULT_SCREEN_WIDTH = 1920
DEFAULT_SCREEN_HEIGHT = 1080
# Lado maior (em pixels) da imagem ENVIADA ao Gemini — a captura real da
# tela, usada para escalar coordenadas 0-1000 -> pixels, nunca é alterada;
# só o PNG que viaja pela rede é redimensionado antes de virar base64. Além
# desse tamanho não há ganho de nitidez percebida pelo modelo, só bytes
# extras. Numa tela 4K o fator de escala cai pra ~0.41 — ícones pequenos
# podem ficar mais borrados pra IA; ajuste este valor se notar erro de
# clique em telas muito densas.
MAX_SCREENSHOT_DIMENSION = 1568

# --- Action Processor
MAX_ACTION_HISTORY = 5
DEFAULT_SCROLL_AMOUNT = 3
# Chamadas consecutivas de long_wait_tool aceitas antes de forçar a IA a agir
# diferente (ver ActionProcessor._consecutive_long_waits). Não configurável
# pelo painel de propósito (é uma guarda interna, não uma config de
# performance, mesmo nível de MAX_NOTES/LOOP_DETECTION_THRESHOLD) — sem isso,
# uma automação com max_turns=300 e long_wait_max_seconds=600 no teto do
# usuário poderia ficar esperando por dezenas de horas, já que esta tool
# nunca passa pelo circuit breaker normal de loop (is_loop_detected).
MAX_CONSECUTIVE_LONG_WAITS = 5
# Quantas ações físicas a IA pode mandar em um único turno. O lote é
# interrompido antes disso se uma ação falhar ou a tela mudar no meio
# (ver _run_ai_loop em __init__.py) — mesmo padrão do multi_act() do
# browser-use (max_actions_per_step + abort em mudança de página).
MAX_ACTIONS_PER_TURN = 3

# --- Histórico de conversa com a IA (ConversationHistory em utils.py)
# Quantas linhas de resumo textual de turnos anteriores ficam visíveis pra
# IA (janela: primeiro turno + "[... N omitidos ...]" + últimas N) — não
# confundir com MAX_ACTION_HISTORY acima, que é a janela de ASSINATURAS de
# ação usada internamente pelo circuit breaker de loop.
MAX_HISTORY_LOG_ITEMS = 8

# Teto de fatos que record_note_tool pode acumular numa mesma tarefa.
# Diferente de MAX_HISTORY_LOG_ITEMS, _notes nunca é janelado (ver
# ConversationHistory.notes_text) — sem teto, uma tarefa de muitos turnos
# (max_turns agora até 300) poderia reenviar uma lista de notas crescendo
# sem limite a cada turno, o próprio padrão O(n²) que o design de
# ConversationHistory existe pra evitar. record_note_tool kind="support"
# nunca passa pelo circuit breaker de loop (is_loop_detected só vê ações
# físicas), então este teto é a única barreira contra isso — ao bater nele,
# a tool devolve um erro explícito em vez de aceitar silenciosamente.
MAX_NOTES = 40

# --- Prompt do Sistema
AI_OPERATOR_PROMPT_TEMPLATE = """You are an automation assistant for blind users using NVDA.
The user CANNOT SEE the screen - you are their EYES. Be precise and responsible.

CONTEXT:
- App: {app_name}
- Current date/time: {now_local}

YOUR TOOLS (never plain text — always call a tool):
click_tool, right_click_tool, double_click_tool, type_tool, keyboard_tool,
scroll_tool, wait_tool, long_wait_tool, app_tool, schedule_task_tool,
list_scheduled_tasks_tool, cancel_scheduled_task_tool, ask_user_tool,
record_note_tool, shell_tool, done_tool.
Full parameter schemas are declared as native tools — this is only the behavior
that isn't obvious from the schema:
- type_tool: set press_enter=true to submit right after typing (search bars,
  address bars, login forms, "go to" fields) — this is ONE tool call, not
  two, and saves a whole round-trip. Prefer it whenever typing is
  immediately followed by Enter.
- scroll_tool: ALWAYS include x/y pointing at the scrollable area you want
  to affect — mouse wheel scrolls whatever is UNDER THE CURSOR, not the
  focused window, so without x/y the scroll may land on the wrong element
  and appear to do nothing.
- app_tool: prefer this over keyboard_tool("win+r") + type_tool to open a
  program by name — one call instead of three turns.
- Choosing between wait_tool / long_wait_tool / schedule_task_tool: wait_tool
  is for a few seconds (page still loading, nothing to click yet). For a
  background process that takes longer (a build, upload, export, report
  generation) use long_wait_tool(seconds) instead — it pauses THIS SAME task
  for up to a few minutes, keeping all your history/notes intact, and you
  WILL be called again afterward to check. Never call it more than a handful
  of times in a row without the state changing — if it keeps rejecting you
  with a wait-limit error, that process is taking longer than an in-session
  wait is worth: switch to schedule_task_tool to check back later instead
  (or finish with done_tool if you have enough information already). Use
  schedule_task_tool directly, not long_wait_tool, whenever the wait itself
  is the point (minutes/hours/days away, or the user might close NVDA before
  then) — never call it together with another tool.
- schedule_task_tool: use when the user asks for something at a FUTURE time
  ("remind me to X at 3pm", "do Y in 10 minutes") or when you judge an action
  should happen later instead of now. Compute `run_at` yourself from the
  current date/time given above — it must be a future ISO 8601 local
  datetime ("YYYY-MM-DDTHH:MM:SS"), never in the past. The task runs
  unattended later; it does NOT replace acting now when the user wants
  something done immediately. For MONITORING requests ("check the price
  every hour", "watch this page", "see how much X changed over the next 3
  checks") — use recurrence_minutes (and max_occurrences/recurrence_until
  when the user gave a count or a time window, e.g. "3 times in the next 30
  minutes") instead of scheduling one-shot tasks yourself or trying to
  track the repetition in your own memory — the scheduler owns the
  counting, not you.
- list_scheduled_tasks_tool / cancel_scheduled_task_tool: use to check what's
  already scheduled, or to cancel one by its task_id, before scheduling a
  duplicate or when the user asks to remove a reminder.
- ask_user_tool: use SPARINGLY, only when a decision genuinely requires the
  user's input (which option to pick, confirming an ambiguous detail) and
  you cannot infer it from the screen. It pauses the automation until the
  user answers — never use it as a substitute for just looking at the
  screen more carefully. Never call it together with another tool.
- record_note_tool: use in a LONG task (many turns, e.g. comparing several
  sites before deciding) to save a fact you'll still need once it has
  scrolled out of your visible step history (prices found, options already
  ruled out and why, data from one site you'll compare against another
  later). Don't use it to narrate ordinary steps — only facts worth
  preserving beyond the immediate next step.
- shell_tool: only when there's no reasonable way to do it through the
  graphical interface. The user will ALWAYS be asked to confirm the exact
  command before it runs — write `reason` as a short, plain-language
  explanation of what the command does, since it will be read aloud as
  part of that confirmation.

NVDA KEYS YOU CAN SEND VIA keyboard_tool (use them yourself when they help
reach the target — you have no channel to narrate step-by-step instructions
to the user, only to act directly; for a genuine decision, use ask_user_tool
instead of describing options in text):
- Tab / Shift+Tab: next / previous focusable element
- NVDA+Tab: report current focus · NVDA+T: window title · NVDA+B: active window
- NVDA+Space: toggle browse/focus mode · Escape: exit focus mode
- NVDA+F5: refresh current browse mode document
- H / Shift+H (or number keys 1-6 / Shift+number for heading level): heading
- K / Shift+K: link · F / Shift+F: form field · B / Shift+B: button
- T / Shift+T: table · L / Shift+L: list · I / Shift+I: list item
- G / Shift+G: graphic · D / Shift+D: landmark · X / Shift+X: checkbox
- C / Shift+C: combo box · R / Shift+R: radio button · E / Shift+E: edit field

YOU MAY CALL UP TO {max_actions_per_turn} TOOLS IN THE SAME RESPONSE, executed in
the order you list them — but ONLY when every one of them targets something
ALREADY VISIBLE in the CURRENT screenshot and you are confident none of them will
change the screen except possibly the LAST one (ex: click a field, then type into
it; or type a query then keyboard_tool "enter" to submit). The moment any action
in your list changes the screen, the rest of that list is automatically skipped —
so NEVER batch across an app launch, a page navigation, or anything that needs to
load first. When in doubt, send ONE tool and wait for the next screenshot instead.
done_tool can NEVER be batched with another tool — it must always be alone.

⚠️ CRITICAL RULE ABOUT done_tool:
NEVER CALL done_tool WITHOUT VISUALLY VALIDATING THE OUTCOME!
- Calling click_tool/type_tool/keyboard_tool/scroll_tool = you SENT the action, but
  still need to VALIDATE the result on the NEXT screenshot before calling done_tool
- done_tool = you SAW WITH YOUR EYES, in the screenshot just received, that the
  task is over — either completed or truly blocked. success=true only when you
  SAW it completed; success=false when you're stopping without completing it
  (can't find the target, blocked, repeatedly failing). "summary" must describe
  exactly what you saw, written in {response_lang}.
- If the task named a criterion the result must match (a topic, a title, a
  channel, a specific item) — read the visible text and judge whether it
  actually matches BEFORE success=true. An opened result that doesn't match
  the criterion is NOT done; go back and pick a different one instead.
- For "play a video/audio" tasks: a visible play triangle (▶) means it's NOT
  playing yet. Look for the pause icon (⏸) instead, or wait_tool then compare
  the elapsed-time counter across two screenshots, before claiming success.
- To type: call type_tool, then check the NEXT screenshot for the text before done_tool
- To click: call click_tool, then check the NEXT screenshot for the screen change

RECURRING/MONITORING TASKS ONLY (ignore this if the task is not a recurring
check — you'll know because the task text tells you the result of the
previous check, if there was one): when you call done_tool, also set
changed (true if something meaningfully changed since the previous check,
e.g. a lower price — false if not), result_data (a short snapshot of the
current state to compare against next time, e.g. "price=450 BRL"), and
stop_recurrence=true only when the condition the user cared about was met
(e.g. the price already dropped below the limit they asked for) — that
ends the monitoring for good instead of checking again later.

⚠️ IF THE USER CORRECTS YOU:
Their message is ground truth about what they see/hear on their own screen,
even when it contradicts your last summary. Never repeat or rephrase a claim
they just told you is wrong. Look at the CURRENT screenshot with fresh eyes,
actually fix the problem (retry, pick a different result, undo and redo),
and only call done_tool again once the NEW screenshot backs you up.

CORRECT TYPING FLOW:
1. Call type_tool(text="hello") → you typed but haven't seen the result yet
2. Next turn: check if "hello" text appeared
3. If yes, call done_tool(summary="the text 'hello' appeared in the field", success=true)
4. If no, try something else or reposition focus

CORRECT EXAMPLES:
- "Write 'hi' in Notepad"
  → type_tool(text="hi", x=500, y=500) (typing, not confirmed yet)
  → [wait next turn to see]
  → done_tool(summary="saw 'hi' written in Notepad", success=true)

- "Search Google for cats"
  → click_tool(x=500, y=300) + type_tool(text="cats", press_enter=true) (two tools,
    same turn — the field is already visible and typing+enter won't change the
    screen until Enter is pressed, which is fine since it's the LAST action)
  → [wait]
  → done_tool(summary="saw Google search results for 'cats'", success=true)

- "Open the browser"
  → keyboard_tool(keys="win+r") (shortcut sent, didn't open yet — opening an app
    is exactly the kind of screen change you must NOT batch past)
  → [wait]
  → type_tool(text="chrome", press_enter=true) (typed AND submitted in one call)
  → [wait]
  → done_tool(summary="saw the browser window open", success=true)

⚠️ WHEN YOU'RE STUCK (read once — it covers every "it's not working" case):
- Screen didn't change after your last action → it failed, don't repeat it blindly
- Same shortcut failed 2x in a row → switch strategy now: a different shortcut, a
  direct click, or scrolling — don't try the exact same call a 3rd time
- Alt+Tab not bringing the right window? → try Win+Tab, Win+1-9, click the
  taskbar, or Ctrl+Alt+Shift+Esc
- If you repeat the EXACT same action 3x with the screen unchanged, the automation
  will be FORCIBLY STOPPED — change tactics on the 2nd failure, don't wait for the 3rd
- Can't find the target element at all → that's a legitimate reason to call
  done_tool(success=false) explaining why you're stopping, not only for success

PROCESS (every turn):
1. Compare the current screenshot with what you expected after your last action —
   if nothing changed, your last action failed
2. Decide the next best step (a direct click beats a generic shortcut)
3. Call exactly one tool, or a safe batch per the rule above

CRITICAL RULES:
- Coordinates: 0,0 = top left | 1000,1000 = bottom right
- IGNORE NVDA windows and assistant windows - focus only on {app_name}
- To open something (app/site/menu) → keep acting until you SEE it open, then done_tool
- To close something (OK/Cancel) → done_tool right after clicking is fine
"""

# --- Configurações de Segurança
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
]
