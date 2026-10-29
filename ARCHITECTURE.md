# Arquitetura do operadorIA

Este documento detalha como o addon decide o que fazer e como gerencia
contexto — a parte que mudou bastante numa sessão de refatoração e que o
[`CLAUDE.md`](CLAUDE.md) só resume. Se você for alterar `action_tools.py`,
`action_processor.py`, `gemini_client.py`, `utils.py` ou o loop em
`__init__.py`, leia isto primeiro.

## De onde viemos

Antes desta refatoração, o addon decidia "quais ações a IA pode usar"
inteiramente em prosa: o prompt do sistema listava as ações em texto e
mostrava o formato JSON esperado como exemplos de string — sem schema
formal, sem `tools=` na SDK. A resposta do Gemini era um texto cru de onde
o addon tentava extrair um JSON com 3 estratégias sucessivas de regex/
bracket-matching; se nenhuma encontrasse nada, o código **tratava isso como
"tarefa concluída"**, e qualquer `action` desconhecida virava um clique
esquerdo por padrão. O histórico de conversa era uma lista que só crescia —
todo turno reenviava, inline, TODOS os screenshots já tirados na tarefa.

A refatoração trocou isso por function calling nativo do Gemini, execução
em lote com corte automático, e um histórico que nunca cresce sem limite —
inspirado diretamente no código-fonte real de dois projetos: **Windows-Use**
(`github.com/Jeomon/Windows-Use` — agente de GUI pra Windows, mesmo domínio
deste addon) e **browser-use** (`github.com/browser-use/browser-use` —
agente de navegador, mas com o padrão de execução de ações e gestão de
contexto mais maduro e documentado da categoria). As referências a esses
projetos ao longo deste documento vêm de ter lido o código deles de
verdade, não de suposição.

## Visão geral do pipeline

```
NVDA+Shift+R
  -> GlobalPlugin.script_iniciarOperadorIA()     (__init__.py)
  -> GlobalPlugin._run_ai_loop(command)           roda numa thread de fundo
       para cada turno (até max_turns):
         1. captura screenshot           (screen_capture.capture_fullscreen)
         2. monta contents= O(1)         (ConversationHistory.build_messages_for_api)
         3. chama o Gemini                (GeminiClient.generate_content, function calling)
         4. planeja o lote de ações       (ActionProcessor.process_function_calls -> BatchPlan)
         5. executa o lote                (ActionProcessor.run_batch, via GlobalPlugin._execute_batch)
         6. grava 1 linha de resumo       (ConversationHistory.record_step)
         7. decide: done_tool? circuit breaker disparou? continua?
  -> GlobalPlugin._show_result_in_chat()          abre o chat para perguntas de acompanhamento
```

## `action_tools.py` — as ações como function declarations

Cada ação (`click_tool`, `right_click_tool`, `double_click_tool`,
`type_tool`, `keyboard_tool`, `scroll_tool`, `wait_tool`, `done_tool`) é um
`types.FunctionDeclaration` de verdade, com schema tipado (`types.Schema`,
`required=[...]`, `enum=[...]` quando aplicável) — não mais texto em prosa.

- `get_tool()` agrega tudo num `types.Tool` só.
- `TOOL_NAME_TO_ACTION_TYPE` mapeia o nome da function declaration pro
  `action_type` que `ActionInfo`/`ActionExecutor` entendem.
- `validate_args(tool_name, args)` valida os argumentos ANTES de qualquer
  execução — parâmetro obrigatório faltando, `direction` fora do enum, tool
  desconhecida — tudo isso vira um erro explícito, nunca um clique por
  padrão.
- `done_tool` tem `summary` E `success` (boolean) obrigatórios: nunca só um
  boolean solto, sempre junto de uma frase descrevendo o que viu na tela —
  mas `success` é o que distingue, em código, "concluí de verdade" de "estou
  desistindo", algo que antes só existia implícito no texto do `summary`.
  `process_function_calls` propaga esse valor em `BatchPlan.success`.

## `gemini_client.py` — function calling forçado, sem automatic function calling

`GeminiClient.generate_content()` monta `GenerateContentConfig` com:

```python
tools=[action_tools.get_tool()],
tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
    mode=types.FunctionCallingConfigMode.ANY)),
automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
```

`mode=ANY` força o modelo a sempre devolver uma function call (nunca texto
solto) — isso elimina de raiz o caso "sem JSON = tarefa concluída" do
design antigo. `automatic_function_calling.disable=True` garante que o SDK
nunca tente invocar nada por conta própria — a execução real continua
inteiramente sob controle manual do `ActionExecutor`.

O retorno não é mais uma string — é um `TurnResult` (`NamedTuple`):
`function_calls`, `text`, `error`, `model_content` (o
`response.candidates[0].content` BRUTO — nunca reconstruído a partir das
function_calls já extraídas, porque modelos "thinking" anexam
`thought_signature` em partes da resposta que a API espera receber de volta
inalterado) e `finish_reason`. Uma resposta sem function_calls E sem texto
(bloqueio de safety, corte por `MAX_TOKENS`, etc.) vira um `error` explícito
— nunca é silenciosamente tratada como sucesso.

`GeminiClient` é criado **sob demanda** dentro de `_run_ai_loop`, não no
`GlobalPlugin.__init__` — ver nota de segurança no `CLAUDE.md` sobre por quê
(a SDK valida a API key no construtor e lançaria `ValueError` na
inicialização do NVDA se a chave estivesse vazia).

## `action_processor.py` — planejamento de lote, nunca execução direta

### `BatchItem` / `BatchPlan`

`ActionProcessor.process_function_calls(function_calls, screen_w, screen_h)`
devolve um `BatchPlan(items, is_finished, explanation)`. Cada `BatchItem`
representa UMA function_call recebida no turno, com `kind` igual a:

- `"skip"`: já resolvido (erro de validação, `done_tool` rejeitado por vir
  junto de outra tool, excedente do limite `MAX_ACTIONS_PER_TURN`) —
  `.response` já preenchido, nada a executar.
- `"execute"`: ação física pendente — `.action_info` é a `ActionInfo` já
  convertida (coordenadas 0-1000 → pixels reais via `_scale_coordinates`);
  `.response` só é preenchido depois da execução.

Política de planejamento (sem nenhuma dependência de execução real, por
isso é 100% testável sem NVDA):

1. Lista vazia de function_calls → anômalo (com `mode=ANY` nunca deveria
   acontecer); não finaliza, pede retry no próximo turno.
2. `done_tool` junto de qualquer outra tool → rejeita todas, pede retry.
3. Só `done_tool`, com `summary`/`success` válidos → `is_finished=True`,
   `BatchPlan.success` carrega o `success` recebido, sem ação física.
4. Caso contrário: até `addon_config.MAX_ACTIONS_PER_TURN` (3) items ficam
   `"execute"`; o que exceder o limite vira `"skip"` (truncado, não é
   erro). Cada item é validado individualmente via `action_tools.validate_args`
   — um parâmetro inválido **nunca** vira a ação por padrão, vira `"skip"`
   com erro explícito.

### `ActionProcessor.run_batch(items, pre_batch_hash, execute_fn, capture_fn)`

A execução de verdade do lote — sequencia os itens `"execute"`, chama
`execute_fn(action_info)` pra cada um, tira uma nova screenshot via
`capture_fn()` depois de CADA ação e compara o hash MD5 com a anterior.

Regra de corte do lote (mesmo padrão do `multi_act()` do browser-use —
eles chamam de "page-change guard", aqui é "mudança de tela"): **qualquer
mudança de tela ou falha de execução aborta o restante da fila**, mesmo que
a ação que causou a mudança tenha tido sucesso — o pressuposto é que, se a
tela mudou, o que a IA planejou pra depois pode não fazer mais sentido no
novo estado, e ninguém vai conferir visualmente antes da próxima ação
disparar.

Também aplica o circuit breaker de loop (`is_loop_detected`) a cada ação
executada: se a MESMA assinatura de ação se repetir
`LOOP_DETECTION_THRESHOLD` (3) vezes seguidas sem a tela mudar, `run_batch`
devolve `True` e **toda a automação é interrompida** (não é mais um aviso
em texto que a IA pode ignorar — ver `GlobalPlugin._run_ai_loop`). A
sequência de "sem efeito" zera sempre que a tela muda, pra nunca confundir
uma ação repetida que está de fato progredindo (ex: scroll avançando numa
lista) com um loop de verdade.

`run_batch` recebe `execute_fn`/`capture_fn` como parâmetros — não chama
`ActionExecutor`/`capture_fullscreen` diretamente — exatamente pra poder
ser testado fora do NVDA com dublês simples (ver
[Como testar](#como-testar-sem-o-nvda-rodando)). `GlobalPlugin._execute_batch`
(em `__init__.py`) é um wrapper fino que injeta os delays/beeps reais e as
chamadas de verdade.

## `__init__.py` — `_run_ai_loop`

Por turno: captura screenshot → monta `current_user_parts` (função_response
do lote anterior, se houver, + contexto de fala do NVDA + a screenshot) →
`ConversationHistory.build_messages_for_api(current_user_parts)` → chama o
Gemini → planeja o lote → **executa o lote** (`_execute_batch`) →
**grava o resumo do turno** (`record_step`) → decide se para (circuit
breaker ou `done_tool`) ou continua (`set_last_model_content`).

Dois detalhes que só fazem sentido lendo o código com este contexto:

- A ordem entre `_execute_batch` e `record_step` é proposital: primeiro
  executa (preenche `.response` de cada item), depois resume — resumir
  antes produziria uma linha de histórico sem saber o resultado real.
- `set_last_model_content(None)` é chamado explicitamente nos dois casos
  terminais (circuit breaker disparou, `done_tool` concluiu a tarefa) —
  **nunca** nos outros. Isso existe pra corrigir um bug real que a
  refatoração encontrou: se o `model_content` do turno (que contém a
  function_call) fosse salvo incondicionalmente, um circuit breaker ou um
  `done_tool` deixaria essa function_call sem resposta pendurada no
  histórico — e uma pergunta de continuação depois (`_on_new_question`)
  provavelmente faria a API rejeitar a chamada seguinte por function_call
  sem function_response correspondente.

## `utils.py` — `ConversationHistory`: histórico O(1) por turno

Esta é a peça que resolve o problema central da refatoração: no design
antigo, `conversation_history` era uma lista que só crescia, e cada
screenshot de cada turno da tarefa era reenviado, inline, em TODA chamada
subsequente — O(n²) em bytes de imagem ao longo de uma tarefa de `n`
turnos. Com `max_turns` configurável até 50, isso significava reenviar até
49 screenshots completos no último turno.

A fonte do design atual: **Windows-Use** descarta a mensagem de estado
pesada do turno (screenshot + árvore de UI) imediatamente depois de usá-la
(`self.state.messages.pop()  # Remove the previous state message`), só o
rastro leve (ferramenta + args + resultado) permanece. **browser-use** vai
além: por padrão só a screenshot MAIS RECENTE é enviada como imagem
("Use only the current screenshot"); passos anteriores entram só como texto,
numa janela limitada (`agent_history_description`: primeiro item +
`[... N passos omitidos ...]` + últimos N).

`ConversationHistory` guarda 4 coisas:

- `_task_text`: a âncora da tarefa (`set_task_text`), sempre enviada,
  nunca é omitida pela janela.
- `_log_entries`: uma linha de texto por turno concluído (`record_step`),
  numerada (`"3. click_tool(x=..,y=..) -> ok, tela mudou"`). `history_log_text()`
  devolve tudo se couber em `addon_config.MAX_HISTORY_LOG_ITEMS` (8), ou
  primeiro item + marcador de omissão + os mais recentes, igual ao
  `agent_history_description` do browser-use. **É o resumo técnico de ações
  pra IA — nunca deveria aparecer na tela pro usuário.**
- `_last_model_content`: só o `Content` bruto do turno MAIS RECENTE do
  modelo (`set_last_model_content`) — nunca mais que um.
- `_conversation_log`: a transcrição HUMANA da conversa
  (`record_conversation_message(sender, message)`), pares
  `{"sender": "Você"/{ADDON_NAME}, "message": ...}`. **Nunca** entra em
  `build_messages_for_api`/`history_log_text` — não é contexto pra API,
  existe só pra repopular o `OperatorChatDialog` ao retomar uma seção salva
  (`GlobalPlugin._open_chat_dialog_for_resumed_session`). Não confundir com
  `_log_entries` acima: essa confusão — mostrar o log técnico de ações como
  se fosse a conversa — era exatamente um bug real já corrigido (o replay do
  resume usava `_log_entries` com sender fixo `"Histórico"`).

`build_messages_for_api(current_user_parts)` monta `contents=` com, no
máximo, 3 `Content`:

```
turno 0 (ou depois de done_tool/circuit breaker):
  [ Content(user, [task_text, log_text?] + current_user_parts) ]

turno N>=1 "no meio" de uma automação em curso:
  [ Content(user, [task_text, log_text]),
    last_model_content,                     # role="model"
    Content(user, current_user_parts) ]     # function_response + screenshot ATUAL
```

Isso é O(1) em imagens — não importa se é o turno 2 ou o turno 50, só a
screenshot do turno atual viaja na requisição. **Por que o pairing
function_call/function_response não quebra**: a API do Gemini só exige que
o `Content` do modelo com a function_call seja IMEDIATAMENTE seguido pelo
`Content` de usuário com o function_response correspondente — não exige que
todo o histórico anterior esteja presente. Isso foi confirmado contra a API
real, não só deduzido (ver `tests/test_real_api_integration.py`).

## `screen_capture.py` — downscale antes de codificar

`capture_fullscreen()` redimensiona a imagem (preservando aspect ratio) se
o lado maior exceder `addon_config.MAX_SCREENSHOT_DIMENSION` (1568px) —
`_maybe_downscale`. As dimensões REAIS da tela (`w, h` de
`wx.GetDisplaySize()`) continuam sendo retornadas sem alteração, porque
`ActionProcessor._scale_coordinates` converte a resposta 0-1000 da IA pra
pixels reais com base nelas, não no tamanho da imagem efetivamente enviada
— o modelo sempre responde na escala normalizada independente de quantos
pixels a imagem tinha.

Trade-off documentado no código: numa tela 4K o fator de escala cai pra
~0.41 — ícones pequenos/texto denso podem ficar mais borrados pra IA, e o
usuário não tem como compensar visualmente um clique errado. Ajuste
`MAX_SCREENSHOT_DIMENSION` se notar isso na prática.

## Contexto de fala do NVDA

`_speech_history` (deque, `maxlen=addon_config.SPEECH_HISTORY_SIZE`) é
escrito pela thread do `eventQueue` do NVDA (via `queueFunction` em
`_capture_speech`) e lido pela thread de automação em `_get_speech_context`
— protegido por `self._speech_history_lock` (`threading.Lock`).
`_append_to_speech_history` ignora a frase se for idêntica à última
registrada, pra não poluir o prompt com bullets repetidos quando o NVDA
fala a mesma coisa várias vezes seguidas (comum).

## `session_store.py` / `sessions_dialog.py` — seções persistidas (estilo Claude Code)

`ConversationHistory` já era "leve" por design (nunca guarda screenshots —
ver seção anterior), o que tornou a persistência de seções barata: salvar
uma conversa em disco é só serializar os mesmos 4 campos que já vivem na
memória (`_task_text`, `_log_entries`, `_last_model_content`, `_conversation_log`).

- **`session_store.py`** é uma camada SQLite pura (`sqlite3`/`json`/`time`
  — nunca importa `wx`, `google.genai`, `config` nem `globalVars`), o que
  a deixa testável fora do NVDA sem nenhum stub (ver
  `tests/test_session_store.py`). Cada método público abre/fecha sua
  própria conexão (em vez de uma conexão compartilhada entre a thread de
  automação e a GUI thread, que exigiria `check_same_thread=False`).
- O banco (`operadorIA_sessions.db`) fica no perfil ativo do NVDA
  (`globalVars.appArgs.configPath`, ao lado do `nvda.ini`) — resolvido por
  `GlobalPlugin._resolve_session_db_path()`, nunca pelo módulo em si, pelo
  mesmo motivo de `ActionProcessor.run_batch` receber `execute_fn`/
  `capture_fn` como parâmetros em vez de tocar o NVDA diretamente. Fica
  **fora da pasta do addon** de propósito: essa pasta é recriada do zero
  em updates do addon.
- **`sqlite3` precisou ser vendorizado em `lib/`** — a build de Python
  embutida no NVDA não traz esse módulo de fábrica (nem a parte pura
  Python, nem a extensão compilada); um `import sqlite3` no nível do
  arquivo quebrava a inicialização do addon inteiro com
  `ModuleNotFoundError: No module named 'sqlite3'` — erro real encontrado
  testando dentro do NVDA, não hipotético. A correção: copiar, de um
  CPython 3.13 win_amd64 comum (mesmo ABI já usado pelas outras extensões
  compiladas bundled aqui, ex. `_cffi_backend.cp313-win_amd64.pyd`), os 3
  pedaços que faltam pra `addon/globalPlugins/operadorIA/lib/`:
  - `lib/sqlite3/` (o pacote puro Python: `__init__.py`, `dbapi2.py`,
    `dump.py`, `__main__.py`);
  - `lib/_sqlite3.pyd` (a extensão compilada que `sqlite3/dbapi2.py`
    importa);
  - `lib/sqlite3.dll` (a biblioteca nativa que `_sqlite3.pyd` carrega em
    runtime — é isso que faltava de fato; o pacote Python por si só não
    seria suficiente sem ela).
  `__init__.py` chama `os.add_dll_directory(lib_path)` logo depois de
  acrescentar `lib_path` ao `sys.path`, como reforço defensivo pro Windows
  achar `sqlite3.dll` (na prática, a extensão `.pyd` já é carregada com
  `LOAD_WITH_ALTERED_SEARCH_PATH`, que já busca DLLs na própria pasta do
  `.pyd` — confirmado escondendo o `sqlite3` real do Python do sistema e
  testando a cópia vendorizada isolada; `add_dll_directory` é só
  cinto-e-suspensório, não o que resolve de fato). Validado também
  simulando a ausência completa do `sqlite3` do sistema (renomeando os
  arquivos reais temporariamente) e confirmando que a cópia em `lib/`
  funciona sozinha, com round-trip de leitura/escrita real.
- `ConversationHistory.to_dict()`/`from_dict()` (em `utils.py`) fazem a
  ponte: `_last_model_content` (um `types.Content` pydantic do SDK) vira
  dict puro via `model_dump(mode="json")` e volta via
  `types.Content.model_validate()` — inclusive `thought_signature` (bytes),
  que o pydantic já serializa/desserializa como base64 sem encoder
  customizado.
- **Autosave respeita o mesmo invariante de `set_last_model_content(None)`**
  documentado na seção do `_run_ai_loop` acima: `GlobalPlugin._autosave_session()`
  só é chamado DEPOIS que o valor final de `_last_model_content` do turno já
  foi decidido (circuit breaker, `done_tool`, ou caminho normal) — nunca
  entre essa decisão e o autosave, pra nunca persistir uma function_call
  sem function_response possível.
- **Modo seguro do NVDA** (tela de login do Windows): `self.session_store`
  fica `None` nesse modo (mesma guarda de `globalVars.appArgs.secure` já
  usada pro `SettingsPanel`) — nenhum comando digitado na tela de login é
  persistido.
- **Escopo deliberado**: uma sessão ativa por vez, igual ao resto do
  `GlobalPlugin` (singleton). Retomar uma sessão salva
  (`GlobalPlugin._resume_session`) reconstrói `self.conversation_history`
  e reabre o `OperatorChatDialog` reusando `self._on_new_question` como
  callback — nenhuma mudança em `chat_dialog.py` foi necessária. Não há
  suporte a múltiplas automações concorrentes.
- **O replay do resume usa `_conversation_log`, nunca `_log_entries`**:
  `_open_chat_dialog_for_resumed_session` itera
  `record.conversation_log` (pares `{"sender", "message"}` reais) —
  correção de um bug onde o replay usava `record.log_entries` com sender
  fixo `"Histórico"`, mostrando o log técnico de ações (ex:
  `"1. rightclick(x=622,y=257) -> ok, tela mudou"`) em vez da conversa.
  Gravação da transcrição acontece em exatamente 2 pontos de `__init__.py`:
  logo após `set_task_text` em `_run_ai_loop` (grava "Você", com autosave
  imediato pra não perder esse registro se o primeiro turno falhar), e em
  `_show_result_in_chat` (grava a resposta final da IA, cobrindo sucesso,
  erro, circuit breaker e exceção — todos convergem pra essa função).
- **Poda automática**: `max_sessions` (config do painel, padrão 50) só é
  aplicada em `create_session` (nova tarefa) — reduzir o limite não afeta
  retroativamente sessões já salvas até a próxima sessão ser criada.
- **Migração de schema aditiva**: a tabela `sessions` ganhou a coluna
  `conversation_log_json` depois que o recurso de seções já estava em uso
  (usuários podem ter `.db` reais só com as 3 colunas originais).
  `_ensure_schema()` confere via `PRAGMA table_info(sessions)` e roda
  `ALTER TABLE ... ADD COLUMN ... DEFAULT '[]'` só se a coluna ainda não
  existir — preserva `task_text`/`log_entries`/`last_model_content` já
  salvos; sessões migradas carregam com `conversation_log=[]` (replay vazio
  ao retomar, não é erro). Testado explicitamente recriando o schema antigo
  via SQL direto (`tests/test_session_store.py`,
  `test_migrates_old_schema_db_without_conversation_log_column`).

## Agendamento de tarefas e tools de apoio

Adicionado depois da refatoração original (ver seções acima) pra dar à IA
funções além de mouse/teclado/visão: agendar execuções futuras, abrir apps
por nome, perguntar ao usuário, e (opt-in) rodar comandos de shell
confirmados. A pesquisa que levou a este design (3 agentes de exploração
sobre tools/action_processor, SQLite/session_store e threading/lifecycle do
`__init__.py`) identificou riscos concretos que moldam tudo abaixo — em
especial que `_run_ai_loop` sempre termina roubando o foco
(`_show_result_in_chat` → `.Raise()`/`.SetFocus()`) e que `self.processing`
era um bool sem lock lido/escrito de 3 threads diferentes.

### Pré-requisito: `_automation_lock` em vez de `self.processing`

Um 4º disparador de automação (o scheduler) exigia fechar o TOCTOU que já
existia entre os 3 pontos de entrada anteriores. `self._automation_lock`
(`threading.Lock`) substitui o bool solto: `_try_start_automation()`
(`acquire(blocking=False)`) é o único jeito de reservar a automação,
`_finish_automation()` (`release()`) o único jeito de liberar — chamado
dentro do `finally` de `_run_ai_loop`, **na thread que de fato rodou a
automação**, não em quem a iniciou (`threading.Lock.release()` de uma
thread diferente de quem deu `acquire()` é válido, diferente de `RLock`).
`.locked()` continua disponível pra um aviso prévio não-consumptivo (ex:
`script_iniciarOperadorIA` recusando antes de abrir o diálogo de comando).

### `kind="support"`: tools que não mexem na tela

`click_tool`/`type_tool`/etc. sempre passavam por `run_batch`, que tira uma
screenshot e compara hash MD5 depois de CADA item executado (ver seção
"`action_processor.py`" acima). Uma tool como `schedule_task_tool` não
muda a tela — rotear por esse caminho seria, no mínimo, latência
desperdiçada (`wait_until_stable` até `STABILITY_MAX_WAIT_ACTION`), e, pior,
`ActionInfo.signature()` caía no fallback genérico (`self.action_type` cru)
pra tipos desconhecidos — três chamadas de `schedule_task_tool` com
argumentos DIFERENTES colidiriam na mesma assinatura e disparariam o
circuit breaker de loop por engano.

A correção: um terceiro `kind` de `BatchItem`, `"support"`, resolvido
inteiramente dentro de `ActionProcessor.process_function_calls` — nunca
chega a `run_batch`. `process_function_calls` ganhou um parâmetro
`support_handlers: dict[str, Callable[[dict], dict]]`, mesma injeção de
dependência que `run_batch` já usa pra `execute_fn`/`capture_fn` (motivo
idêntico: manter o planejador puro e testável sem SQLite real — ver
`tests/test_action_processor.py`). Os handlers de verdade
(`GlobalPlugin._build_support_handlers`) chamam `scheduler_store` direto.
`run_batch` ganhou um ramo `if item.kind == "support": continue` — já
resolvido, nunca aborta o lote nem conta pro circuit breaker, mesmo em
falha (um `schedule_task_tool` com data inválida não deveria interromper
um `click_tool` no mesmo turno). `ActionInfo.signature()` ganhou ramos
explícitos pra `launch_app`/`shell_command` pelo mesmo motivo do parágrafo
anterior — qualquer `action_type` novo precisa de um ramo aqui, nunca cair
no fallback genérico.

### `ask_user_tool`: a IA ganha um canal de pergunta, não só de ação

O prompt dizia literalmente "you have no channel to lecture the user, only
to act" — verdade até aqui, mas um obstáculo real pra tarefas que exigem
uma decisão do usuário (qual das opções escolher). `ask_user_tool` não é
`kind="execute"` nem `"support"`: é tratada no nível do `BatchPlan`, igual
ao `done_tool` (mesma regra de exclusividade — nunca combinada com outra
tool no turno), com um novo campo `BatchPlan.awaiting_question` (default
`None`, não quebra chamador nenhum). Quando presente, `_run_ai_loop` pula
`_execute_batch` inteiramente e chama `_prompt_user_sync`: fala a pergunta,
abre um `wx.TextEntryDialog` (reaproveitado, mesmo padrão de
`_start_new_task_flow` — nenhuma classe de diálogo nova) via `wx.CallAfter`,
e bloqueia num `threading.Event.wait(timeout=ask_user_timeout_seconds)` —
nunca trava pra sempre; no timeout, fecha o diálogo remotamente
(`dlg.EndModal(...)` via `wx.CallAfter`, técnica padrão pra resolver um
`ShowModal()` de fora da GUI thread) e trata como
`done_tool(success=False)`. A resposta obtida vira o `function_response` do
próximo turno, exatamente como qualquer outro item resolvido.

### `shell_tool`: confirmação obrigatória, nunca no planejador puro

Diferente das tools "support", `shell_tool` passa por `kind="execute"`
normal (`action_type="shell_command"`) — mas a confirmação bloqueante do
usuário (comando + motivo lidos em voz alta, foco padrão em "Não") precisa
de `wx`/GUI thread, que o planejador puro não deveria tocar. Por isso ela
é interceptada em `GlobalPlugin._execute_batch`'s `execute_fn` closure
(a camada NVDA-aware que já injeta delays/beeps reais), nunca dentro de
`ActionProcessor`. `ActionExecutor.execute_action` tem um guard explícito
que RECUSA `shell_command` se chegar até ele sem ter passado por essa
confirmação — defesa contra um bug de wiring futuro, nunca um caminho
silencioso. `shell_tool` só é incluída na lista de tools enviada ao Gemini
(`action_tools.get_tool(include_shell=...)`) se `enable_shell_tool` estiver
ligado no painel (default `False`) — a confirmação continua obrigatória
independente disso, é só uma camada extra pra não nem oferecer a
capacidade em instalações que não vão querer usá-la.

### `app_tool`: por que NÃO precisou de nada novo na arquitetura

Ao contrário de `schedule_task_tool`/`ask_user_tool`/`shell_tool`,
`app_tool` (`action_type="launch_app"`) é um `kind="execute"` comum, sem
nenhuma interceptação especial — abrir um app muda a tela de verdade, então
o abort-on-change e o circuit breaker de `run_batch` já fazem sentido sem
alteração nenhuma. `ActionExecutor.execute_app_launch` tenta
`os.startfile(app_name)` e cai pra `subprocess.Popen(["cmd","/c","start","",
app_name])` (mesma resolução de PATH/App Paths do registro que o diálogo
Executar do Windows usa) — sucesso aqui é "comando de abertura enviado",
não "app confirmado aberto"; a IA valida visualmente no próximo turno,
mesmo contrato de confiança que `click_tool` já tem.

### `scheduler_store.py` / `scheduler.py` / `scheduler_dialog.py`

`SchedulerStore` é uma cópia estrutural de `SessionStore` (mesma
disciplina: nunca importa `wx`/`config`/`globalVars`, conexão-por-chamada,
`busy_timeout=2000`) guardando uma tabela `scheduled_tasks` **no mesmo
arquivo** `operadorIA_sessions.db` — duas classes de store, duas tabelas,
um arquivo, contenção coberta pelo mesmo `busy_timeout` já usado.

`TaskScheduler` (`scheduler.py`) é só o timer+polling, propositalmente
pequeno: `gui.NonReEntrantTimer` criado/iniciado/parado SÓ na main thread
(via `wx.CallAfter`), cujo callback dispara um `threading.Thread(daemon=
True)` pra consultar `get_due_tasks` — nunca I/O síncrono dentro do próprio
timer. O idiom inteiro foi copiado do `AutoUpdateChecker` do próprio NVDA
core (`D:\repo\nvda\source\updateCheck.py`), confirmado lendo o código-fonte
real, não suposto. `TaskScheduler` não decide SE uma tarefa deve disparar —
isso é 100% responsabilidade do callback injetado
(`GlobalPlugin._on_scheduled_task_due`), o que mantém `scheduler.py`
testável sem NVDA (`tests/test_scheduler.py` exercita `_check_due_tasks`
com um store/callback falsos; só `start()`/`stop()`/o timer de verdade não
têm teste automatizado, mesmo motivo de `sessions_dialog.py`).

**O fire-time gate** (`_on_scheduled_task_due`) é o centro de todo esse
desenho — não o polling em si. Nenhuma automação agendada roda sem passar,
em ordem, por: modo seguro → cortina de tela ativa → entrada recente de
teclado/mouse (`seconds_since_last_input`, `GetLastInputInfo` via ctypes,
heurística barata, não garantia) → `_try_start_automation()` (lock) →
**aviso sonoro + janela de graça cancelável**
(`GraceCountdownDialog`, `scheduler_confirm_grace_seconds`) → só então
`_begin_new_automation` de verdade. Cancelar na janela de graça marca a
tarefa `cancelled` (não refaz tentativa — o usuário agenda de novo se
ainda quiser). Qualquer falha nesse caminho (exceção, tarefa não
consumida) libera o lock no `finally` — nunca deixa a automação "presa"
reservada sem rodar.

**Conclusão sem roubar foco**: `_run_ai_loop`/`_show_result_in_chat`
ganharam um parâmetro `steal_focus` (default `True`); o caminho do
scheduler passa `False`, que pula `.Show()`/`.Raise()`/`.SetFocus()` e o
beep de "novo chat" — o `OperatorChatDialog` é criado/populado oculto,
pronto pra ser aberto depois. A fala/beep do RESULTADO em si nunca é
suprimida (já acontece turno a turno, dentro de `_run_ai_loop`, antes de
`_show_result_in_chat` ser chamado) — só o sequestro visual de foco é
evitado.

**Tarefas perdidas** (NVDA fechado no horário): checado 1x no `__init__`
via `wx.CallAfter` (adiado pra não competir com a inicialização do NVDA) —
`get_missed_tasks` usa uma folga de `scheduler_check_interval` pra não
confundir "vencida há 2 segundos, o polling ao vivo já ia pegar" com
"vencida há horas, o NVDA estava fechado". As que contam como perdidas são
marcadas `missed` (nunca rodam nem são descartadas sozinhas) e aparecem em
`SchedulerDialog(missed_mode=True)` pro usuário decidir, por tarefa, "Rodar
agora" ou "Descartar".

## Fase futura (não implementada)

- **Compactação via LLM** (como `Context.need_compaction`/`compact()` do
  Windows-Use, ou `maybe_compact_messages()` do browser-use): resumir o log
  textual via uma chamada extra ao modelo quando o uso de tokens se
  aproxima da janela de contexto. Avaliado e descartado por ora — o design
  atual já é O(1) por turno e `max_turns` tem teto de 50, então estourar a
  janela de contexto de um modelo Gemini (>1M tokens) não é um cenário
  plausível no regime atual. `response.usage_metadata.total_token_count`
  já vem de graça em cada resposta da SDK, então a instrumentação seria
  barata de adicionar se isso mudar.
- **UI Automation complementando clique por coordenada** (como o
  `click_tool`/`type_tool` do Windows-Use fazem via árvore de acessibilidade
  do Windows, não só visão): reduziria erro de clique em controles pequenos
  ou telas de alta densidade. Escopo bem maior (wrapper UIA, mapeamento de
  controles, fallback pra coordenadas) — ficaria como um design separado.
- **Tarefas longas de verdade** (ex: pesquisar e comparar passagens de
  viagem): agendamento (`schedule_task_tool`), `ask_user_tool` e `app_tool`
  (ver seção acima) são os primeiros degraus nessa direção — dão à IA um
  jeito de agir fora do turno-a-turno síncrono e de pedir uma decisão no
  meio do caminho — mas não resolvem o problema inteiro. Uma tarefa que
  genuinamente precisa de mais que `max_turns` (teto 50) ou que precisa
  sobreviver a múltiplas chamadas de `_run_ai_loop` ao longo de horas/dias
  exigiria checkpointing explícito de progresso (o que já foi tentado, o
  que falta, o que foi aprendido) persistido entre chamadas — combinado
  com a compactação via LLM do item acima. Avaliado e deixado pra um design
  separado; não confundir com o agendamento em si, que é só o "gatilho".
- **Tarefas agendadas recorrentes** (diária/semanal): `scheduler_store.py`
  comporta adicionar uma coluna `recurrence_rule` depois sem migração
  destrutiva (mesmo padrão aditivo de `session_store._ensure_schema`) — não
  implementado porque não foi pedido ainda, só agendamento de horário único.

## Como testar sem o NVDA rodando

A maior parte da lógica do addon (tudo que não seja mouse/teclado/clipboard
reais) não depende de nenhuma API exclusiva do NVDA — só da SDK
`google-genai`, que já está bundled em `addon/globalPlugins/operadorIA/lib/`.
Isso significa que dá pra testar fora do NVDA, com o Python normal do
sistema, sem instalar nada. Esse padrão nunca tinha sido usado neste
projeto antes desta refatoração; os scripts ficam em [`tests/`](tests/).

### A receita

1. **Colocar `lib/` no `sys.path`** — mesma pasta que o `__init__.py` do
   addon usa (`addon/globalPlugins/operadorIA/lib/`). Dá acesso a `google.genai`
   sem precisar instalar nada.
2. **Fingir que o módulo faz parte do pacote do addon** — os módulos usam
   import relativo (`from . import addon_config`). Pra carregar um deles
   isolado, registra-se um pacote falso em `sys.modules` com `__path__`
   apontando pra pasta do addon, e carrega-se o módulo via
   `importlib.util.spec_from_file_location` dentro desse pacote falso. Essa
   mecânica inteira já está pronta em [`tests/_harness.py`](tests/_harness.py)
   (`load_addon_module`) — não precisa reescrever isso em cada teste novo.
3. **Stubar os módulos exclusivos do NVDA/Windows que o módulo sob teste
   importa no nível do arquivo** — antes de carregar o módulo real,
   registra um módulo falso em `sys.modules[nome]`. O Python nunca percebe
   a diferença.
   - `api` (usado por `utils.py` e `action_executor.py`) — `stub_nvda_modules()`.
   - `config` (o `config.conf` do NVDA — NÃO confundir com `addon_config.py`
     do próprio addon; usado por `gemini_client.py` e `__init__.py`) —
     `stub_nvda_modules(include_config=True)`, com um `.conf` dict-like
     fake se o teste for exercitar `GeminiClient` de verdade.
   - `wx` (wxPython — fornecido pelo próprio NVDA, não está em `lib/`,
     `pip install` não resolve isso) — `stub_wx_module()`. Só é suficiente
     pra testar lógica pura como `_maybe_downscale`; não dá pra capturar
     uma tela de verdade fora do NVDA.
   - `winUser`, `mouseHandler` (usados por `action_executor.py`) — ninguém
     testa `action_executor.py` isolado ainda (ver limitações abaixo); se
     for fazer, precisa stubar esses dois também.
   - `gui` (o `gui` do NVDA, usado por `scheduler.py` pra `gui.NonReEntrantTimer`)
     — stub mínimo registrado ad-hoc em `tests/test_scheduler.py` (nem
     precisa de `NonReEntrantTimer` de verdade, já que os testes só
     exercitam `_check_due_tasks`, que nunca toca o timer).
4. **Dublês de dados, não de comportamento, pros testes de lógica pura** —
   `FakeActionInfo`, `FakeImage` nos testes existentes são só objetos com os
   atributos/métodos que o código sob teste acessa (duck typing), não mocks
   de framework. Mantém os testes legíveis e não exige nenhuma lib de
   mocking.
5. **Pra validar contra a API de verdade** (quando o risco é sobre o
   COMPORTAMENTO da API, não da sua lógica — ex: "a API aceita este
   formato de mensagens?"): leia a API key já configurada no painel do
   NVDA direto do `nvda.ini` do usuário (`read_gemini_api_key_from_nvda_ini()`
   no harness) — **nunca** hardcode ou imprima a chave em lugar nenhum.
   Esse tipo de teste é opt-in (ver `test_real_api_integration.py`), porque
   gasta cota da API e depende de rede/latência — não deve rodar
   automaticamente junto dos outros.

### Exemplo mínimo

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module, stub_nvda_modules

stub_nvda_modules()  # só se o módulo carregado importar `api`
addon_config = load_addon_module("addon_config")
action_tools = load_addon_module("action_tools")

assert action_tools.validate_args("click_tool", {"x": 1, "y": 2}) is None
```

### Limitações conhecidas (sem teste automatizado)

- `action_executor.py` — mouse/teclado/clipboard reais via `winUser`/
  `mouseHandler`/`api`. Dependem de Windows de verdade, não só de stubs.
- O fluxo completo de `_run_ai_loop` dentro de `__init__.py` — a parte que
  conecta tudo (thread, captura de tela real, diálogos wx). `_execute_batch`
  já é testável de verdade via `ActionProcessor.run_batch` (ver acima); o
  resto do loop (como ele decide avançar pro próximo turno, etc.) só é
  verificável rodando o addon dentro do NVDA mesmo.
- `sessions_dialog.py` (a UI wx de gerenciamento de seções) — igual a
  `chat_dialog.py`, não há teste automatizado de diálogo wx neste projeto;
  `session_store.py` por trás dela é testado isoladamente (ver
  `tests/test_session_store.py`).
- `scheduler_dialog.py` (`SchedulerDialog`, `GraceCountdownDialog`) e o
  timer de verdade de `TaskScheduler` (`start()`/`stop()`/`_on_tick`) —
  mesmo motivo de `sessions_dialog.py`/`chat_dialog.py`; `scheduler_store.py`
  e o polling puro de `TaskScheduler._check_due_tasks` são testados
  isoladamente (`tests/test_scheduler_store.py`, `tests/test_scheduler.py`).
- O fire-time gate completo (`GlobalPlugin._on_scheduled_task_due`,
  `_announce_and_wait_grace`, `_prompt_user_sync`, `_confirm_shell_command`)
  — depende de diálogos wx reais e de tempo de verdade passando; só manual.

### Checklist de teste manual dentro do NVDA

Depois de qualquer mudança em `__init__.py`, `action_executor.py` ou no
pipeline de principal fim-a-fim, rodar manualmente com `NVDA+Shift+R`:

0. **Reiniciar o NVDA e checar o log (`NVDA+F1` ou `nvda.log`) por erro na
   inicialização do addon** — principalmente depois de tocar em `lib/` ou
   em imports no topo de `__init__.py`. Regressão real já aconteceu aqui:
   um `import sqlite3` sem a cópia vendorizada quebrava a carga do addon
   inteiro com `ModuleNotFoundError`, silenciosamente (o NVDA inicializa
   normalmente, só esse addon específico não carrega) — fácil de não notar
   sem olhar o log.
1. Uma tarefa simples do início ao fim, terminando em `done_tool`
   (ex: "abra o bloco de notas e escreva oi").
2. Um scroll numa lista/página longa (confirma que `scroll_tool` sem x/y
   explícito usa o centro da tela, e que a detecção de mudança funciona).
3. Uma tarefa deliberadamente ambígua ou impossível, pra forçar o circuit
   breaker — confirmar que a automação REALMENTE para (não só avisa) e que
   uma pergunta de continuação depois não quebra (function_call órfã).
4. Uma pergunta de continuação via chat depois de uma tarefa concluída.
5. Conferir no log de debug do NVDA que `n_mensagens` (logado em
   `gemini_client.py`) fica estável (≤3) a partir do 2º turno, em vez de
   crescer a cada turno.
6. `NVDA+Shift+O` abre a lista de sessões; tarefas criadas via
   `NVDA+Shift+R` aparecem lá, ordenadas pela mais recente primeiro.
7. Retomar uma sessão salva: o `OperatorChatDialog` reabre com o histórico
   replay visível, e uma pergunta de continuação funciona normalmente —
   inclusive numa sessão que tinha terminado por circuit breaker (sem erro
   de function_call órfã na API).
8. Renomear e excluir uma sessão pela lista; confirmar que o foco volta
   pra lista/campo correto depois de cada operação.
9. Configurar `max_sessions=2` no painel, criar 3 tarefas novas, confirmar
   que a sessão mais antiga foi removida automaticamente.
10. Com o NVDA em modo seguro (tela de login): `NVDA+Shift+O` não deve
    quebrar nem persistir nada.
11. Ativar a Cortina de Tela (Preferências > Configurações do NVDA >
    Visão) e disparar `NVDA+Shift+R` com qualquer comando — deve recusar
    na hora, falando a mensagem nova, sem nenhum beep de captura de tela.
    Desativar a cortina e repetir a mesma tarefa — deve rodar normal, do
    início ao fim. Repetir também com uma pergunta de continuação no chat
    (`_on_new_question` reusa `_run_ai_loop`, então deve recusar do mesmo
    jeito). Esse comportamento não tem teste automatizado possível — só
    `tests/test_screen_curtain.py` cobre a lógica de detecção com módulos
    `vision`/`screenCurtain` falsos, não a cortina real.
12. **Agendamento**: pedir no chat "agende uma tarefa pra daqui 1 minuto:
    abra o bloco de notas". Confirmar que `NVDA+Shift+A` lista a tarefa.
    Esperar o horário chegar enquanto digita em outra janela — confirmar
    beep+fala de aviso, a janela de graça aparecendo, e que digitar não é
    interrompido no meio de uma rajada (heurística de
    `seconds_since_last_input`). Deixar expirar sem cancelar — confirmar
    que roda, fala o resultado turno a turno, e **não** rouba o foco da
    janela em uso. Repetir cancelando na janela de graça (botão e Esc) —
    confirmar que a tarefa fica `cancelled` e não dispara de novo.
13. **Tarefas perdidas**: agendar uma tarefa pra <1 min, fechar o NVDA
    antes de vencer, esperar passar o horário, reabrir o NVDA — confirmar
    que aparece a revisão de tarefas perdidas (não dispara nem descarta
    sozinho), e que "Rodar agora"/"Descartar" funcionam por tarefa.
14. **`ask_user_tool`**: pedir algo ambíguo o bastante pra forçar uma
    pergunta (ex: duas opções parecidas na tela) — confirmar a fala da
    pergunta, o diálogo de resposta, e que o loop continua usando a
    resposta como contexto. Repetir sem responder até expirar
    `ask_user_timeout_seconds` — confirmar que para graciosamente (mesmo
    beep/mensagem de "não concluí" do circuit breaker).
15. **`app_tool`**: "abra a calculadora" — confirmar que vira 1 chamada
    (`app_tool`) em vez do antigo Win+R → digitar → Enter em 3 turnos.
16. **`shell_tool`**: ligar `enable_shell_tool` no painel, pedir algo que
    exija um comando de terminal — confirmar que o diálogo de confirmação
    mostra o comando E o motivo (lidos em voz alta), que "Não"/fechar/Esc
    nunca executa, e que "Sim" executa e devolve a saída (truncada) pra
    IA. Com `enable_shell_tool` desligado (padrão), confirmar que a IA
    nunca tenta chamar `shell_tool` (nem está na lista de tools).
17. Repetir o teste de circuit breaker do item 3, mas intercalando
    chamadas de `schedule_task_tool`/`list_scheduled_tasks_tool` com
    argumentos DIFERENTES entre ações físicas sem efeito — confirmar que
    essas chamadas "support" nunca contam pro circuit breaker (ver
    `ActionInfo.signature()` e o ramo `kind="support"` em `run_batch`).
