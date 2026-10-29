# -*- coding: utf-8 -*-
"""
Cliente da API do Google Gemini usando google-genai oficial, com function
calling nativo (as ações disponíveis são declaradas em action_tools.py).
"""

import logging
import threading
from typing import List, NamedTuple, Optional

import addonHandler
import config
from . import addon_config
from . import action_tools

addonHandler.initTranslation()

try:
    import google.genai
    from google.genai import types
    from google.genai import errors as genai_errors
    import httpx
except ImportError as e:
    raise ImportError(f"google-genai nao foi encontrado: {e}")

logger = logging.getLogger(__name__)

# Erros HTTP que o SDK já re-tenta sozinho (ver build_http_options) — só
# usados aqui para compor uma mensagem final mais clara caso todas as
# tentativas se esgotem.
_TRANSIENT_HTTP_CODES = (408, 500, 502, 503, 504)


def _describe_api_error(e):
    """
    Traduz uma exceção da chamada à API Gemini numa mensagem amigável.
    Compartilhado entre generate_content() e list_models() — o SDK já
    re-tentou sozinho (ver build_http_options) antes de deixar a exceção
    chegar até aqui, então se chegou, é definitiva.
    """
    if isinstance(e, genai_errors.APIError):
        code = getattr(e, "code", None)
        if code in (401, 403):
            return _("Invalid or unauthorized Gemini API key (error %(code)s).") % {"code": code}
        if code == 429:
            return _("Gemini API request limit exceeded (error 429).")
        if code in _TRANSIENT_HTTP_CODES:
            return ngettext(
                "Gemini service unavailable after %(attempts)d attempt (error %(code)s).",
                "Gemini service unavailable after %(attempts)d attempts (error %(code)s).",
                addon_config.API_RETRY_ATTEMPTS,
            ) % {"attempts": addon_config.API_RETRY_ATTEMPTS, "code": code}
        return str(e)
    if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
        return ngettext(
            "Timeout or connection failure with the Gemini API after %(attempts)d attempt: %(error)s",
            "Timeout or connection failure with the Gemini API after %(attempts)d attempts: %(error)s",
            addon_config.API_RETRY_ATTEMPTS,
        ) % {"attempts": addon_config.API_RETRY_ATTEMPTS, "error": e}
    return str(e)


def build_http_options(api_timeout_seconds):
    """
    Monta as opções de timeout + retry do SDK a partir do `api_timeout`
    configurável pelo usuário (painel do NVDA). Pura — só monta objetos de
    config, sem rede — por isso é testável isolada (ver
    tests/test_gemini_http_options.py).

    Sem isso, `google.genai.Client` não aplica timeout algum à chamada HTTP
    (pode travar a thread de automação indefinidamente numa rede ruim) e usa
    `stop_after_attempt(1)` por padrão — ou seja, zero retries mesmo em
    erros claramente transitórios (429, 5xx, timeout de conexão).
    """
    return types.HttpOptions(
        timeout=int(api_timeout_seconds) * 1000,  # SDK espera milissegundos
        retry_options=types.HttpRetryOptions(
            attempts=addon_config.API_RETRY_ATTEMPTS,
            initial_delay=addon_config.API_RETRY_INITIAL_DELAY,
            max_delay=addon_config.API_RETRY_MAX_DELAY,
            exp_base=addon_config.API_RETRY_EXP_BASE,
            jitter=addon_config.API_RETRY_JITTER,
        ),
    )


class TurnResult(NamedTuple):
    """Resultado de um turno de geração de conteúdo."""
    function_calls: Optional[List["types.FunctionCall"]]
    text: Optional[str]
    error: Optional[str]
    model_content: Optional["types.Content"]
    finish_reason: Optional[str]


class GeminiClient:
    """Cliente para interacao com a API do Google Gemini usando SDK oficial"""

    def __init__(self, api_key=None, model=None):
        """
        Inicializa o cliente Gemini.

        Args:
            api_key: Chave da API (usa config.conf)
            model: Modelo a usar (usa config.conf)
        """
        self.api_key = api_key or config.conf[addon_config.CONFIG_SECTION]["gemini_api_key"]
        self.model = model or config.conf[addon_config.CONFIG_SECTION]["gemini_model"]
        self.api_timeout = config.conf[addon_config.CONFIG_SECTION]["api_timeout"]

        # Inicializar cliente com API key, timeout e retry/backoff nativos
        # (ver build_http_options) — antes, o Client era criado sem
        # http_options, deixando api_timeout configurado na UI sem efeito
        # real e sem nenhum retry em erros transitórios de rede.
        self.client = google.genai.Client(
            api_key=self.api_key,
            http_options=build_http_options(self.api_timeout),
        )

    def generate_content(self, messages, temperature=None, system_instruction=None, on_slow_response=None):
        """
        Gera conteudo usando a API Gemini, forçando o modelo a responder
        sempre através de uma das tools declaradas em action_tools.py
        (nunca texto livre solto).

        Args:
            messages: Lista de types.Content no formato da API
            temperature: Temperatura da geracao (usa config.conf se nao fornecida)
            system_instruction: Texto de instrucao de sistema (opcional)
            on_slow_response: callable() opcional, chamado no máximo uma vez
                se a resposta ainda não chegou após api_timeout segundos —
                nesse ponto o SDK já está, com certeza, numa tentativa de
                retry (ver build_http_options). Permite ao chamador avisar o
                usuário (beep/fala) que a automação não travou, só está
                demorando. Nunca bloqueia nem levanta exceção por si só.

        Returns:
            TurnResult
        """
        slow_response_timer = None
        try:
            if on_slow_response is not None:
                slow_response_timer = threading.Timer(self.api_timeout, on_slow_response)
                slow_response_timer.daemon = True
                slow_response_timer.start()

            temp = temperature or config.conf[addon_config.CONFIG_SECTION]["ai_temperature"]

            # Converter safety settings do addon_config
            safety_settings = self._convert_safety_settings(addon_config.SAFETY_SETTINGS)

            # shell_tool só é OFERECIDA ao modelo se o usuário ligou
            # enable_shell_tool no painel — a confirmação bloqueante antes
            # de cada execução (ver __init__.py) é obrigatória de qualquer
            # forma, isso só evita incluir a tool em instalações que nunca
            # vão querer usá-la.
            include_shell = bool(config.conf[addon_config.CONFIG_SECTION]["enable_shell_tool"])

            # Criar config com todos os parametros, incluindo as tools nativas.
            # automatic_function_calling=disable garante que o SDK nunca tente
            # invocar nada por conta própria — a execução real continua
            # passando pelo ActionExecutor, controlado manualmente.
            generate_config = types.GenerateContentConfig(
                temperature=temp,
                safety_settings=safety_settings,
                system_instruction=system_instruction,
                tools=[action_tools.get_tool(include_shell=include_shell)],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY
                    )
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            logger.debug(f"Chamando Gemini: model={self.model} temperature={temp} n_mensagens={len(messages)}")

            response = self.client.models.generate_content(
                model=self.model,
                contents=messages,
                config=generate_config
            )

            if not response or not response.candidates:
                logger.warning("Gemini API retornou resposta vazia (sem candidates)")
                return TurnResult(None, None, _("API did not return a response"), None, None)

            candidate = response.candidates[0]
            finish_reason = getattr(candidate, "finish_reason", None)
            function_calls = response.function_calls
            text = response.text

            error = None
            if not function_calls and not text:
                # Nenhuma function_call e nenhum texto: resposta vazia de
                # verdade (bloqueio de safety, corte por MAX_TOKENS, etc).
                # Nunca deve ser interpretado como "tarefa concluída".
                error = _("Empty response from the AI (finish_reason=%s)") % finish_reason
                logger.warning(error)

            logger.debug(
                f"Resposta Gemini recebida: {len(function_calls or [])} function_call(s), "
                f"finish_reason={finish_reason}"
            )

            return TurnResult(
                function_calls=function_calls,
                text=text,
                error=error,
                model_content=candidate.content,
                finish_reason=str(finish_reason) if finish_reason is not None else None,
            )

        except (genai_errors.APIError, httpx.TimeoutException, httpx.ConnectError) as e:
            error_msg = _describe_api_error(e)
            logger.error(f"Erro da API Gemini (model={self.model}): {error_msg}", exc_info=True)
            return TurnResult(None, None, error_msg, None, None)

        except Exception as e:
            logger.error(f"Erro ao chamar a API Gemini (model={self.model}): {e}", exc_info=True)
            return TurnResult(None, None, str(e), None, None)

        finally:
            if slow_response_timer is not None:
                slow_response_timer.cancel()

    def list_models(self):
        """
        Lista os modelos Gemini disponíveis para esta API key, direto da
        API (usado pelo painel de configurações para popular o dropdown de
        modelo sem depender de uma lista hardcoded que fica desatualizada).

        Returns:
            Lista ordenada de nomes curtos de modelo (ex: "gemini-2.5-flash"),
            restrita aos que suportam generateContent (chat/function calling)
            — exclui modelos de embedding e afins.

        Raises:
            RuntimeError: se a chamada falhar (chave inválida, rede, etc).
        """
        try:
            pager = self.client.models.list(
                config=types.ListModelsConfig(query_base=True)
            )
            names = {
                m.name.split("/")[-1]
                for m in pager
                if "generateContent" in (m.supported_actions or [])
            }
            return sorted(names)
        except (genai_errors.APIError, httpx.TimeoutException, httpx.ConnectError) as e:
            error_msg = _describe_api_error(e)
            logger.error(f"Erro ao listar modelos Gemini: {error_msg}", exc_info=True)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            logger.error(f"Erro ao listar modelos Gemini: {e}", exc_info=True)
            raise RuntimeError(str(e)) from e

    def _convert_safety_settings(self, settings):
        """
        Converte safety settings do formato antigo para o novo SDK.

        Args:
            settings: Lista de dicts com category e threshold

        Returns:
            Lista de SafetySetting do novo SDK
        """
        try:
            converted = []
            for setting in settings:
                category = setting.get('category', '')
                threshold = setting.get('threshold', 'BLOCK_NONE')

                # Mapear apenas as categorias que existem no novo SDK
                category_map = {
                    'HARM_CATEGORY_CIVIC_INTEGRITY': types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                    'HARM_CATEGORY_DANGEROUS_CONTENT': types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    'HARM_CATEGORY_HARASSMENT': types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    'HARM_CATEGORY_HATE_SPEECH': types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    'HARM_CATEGORY_SEXUALLY_EXPLICIT': types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    'HARM_CATEGORY_UNSPECIFIED': types.HarmCategory.HARM_CATEGORY_UNSPECIFIED,
                }

                threshold_map = {
                    'HARM_BLOCK_THRESHOLD_UNSPECIFIED': types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED,
                    'BLOCK_NONE': types.HarmBlockThreshold.BLOCK_NONE,
                    'BLOCK_ONLY_HIGH': types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    'BLOCK_MEDIUM_AND_ABOVE': types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    'BLOCK_LOW_AND_ABOVE': types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
                    'OFF': types.HarmBlockThreshold.OFF,
                }

                if category not in category_map:
                    continue

                cat = category_map[category]
                thresh = threshold_map.get(threshold, types.HarmBlockThreshold.BLOCK_NONE)

                converted.append(types.SafetySetting(
                    category=cat,
                    threshold=thresh
                ))

            return converted
        except Exception as e:
            logger.error(f"Erro ao converter safety settings: {e}", exc_info=True)
            return []
