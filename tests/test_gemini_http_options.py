# -*- coding: utf-8 -*-
"""
Testa gemini_client.build_http_options — a função pura que monta
timeout + retry/backoff (nativos do SDK google-genai) a partir do
`api_timeout` configurável no painel do NVDA. Antes desta função existir,
`GeminiClient` criava o `Client` sem `http_options`: a chamada HTTP não
tinha timeout algum e o SDK usava zero retries por padrão.

Só a montagem dos parâmetros é testável aqui, sem rede — o comportamento
real de retry contra uma rede instável/erros 5xx fica para validação
manual (baixar api_timeout pra 1-2s no painel e observar o beep/mensagem
de "demorando a responder").

Rodar: python tests/test_gemini_http_options.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module, stub_nvda_modules

stub_nvda_modules(include_config=True)
addon_config = load_addon_module("addon_config")
gemini_client = load_addon_module("gemini_client")


def test_timeout_converted_to_milliseconds():
    options = gemini_client.build_http_options(30)
    assert options.timeout == 30000, options.timeout
    print("test_timeout_converted_to_milliseconds OK")


def test_different_timeout_values():
    assert gemini_client.build_http_options(5).timeout == 5000
    assert gemini_client.build_http_options(120).timeout == 120000
    print("test_different_timeout_values OK")


def test_retry_options_match_addon_config_constants():
    options = gemini_client.build_http_options(30)
    retry = options.retry_options
    assert retry.attempts == addon_config.API_RETRY_ATTEMPTS
    assert retry.initial_delay == addon_config.API_RETRY_INITIAL_DELAY
    assert retry.max_delay == addon_config.API_RETRY_MAX_DELAY
    assert retry.exp_base == addon_config.API_RETRY_EXP_BASE
    assert retry.jitter == addon_config.API_RETRY_JITTER
    print("test_retry_options_match_addon_config_constants OK")


if __name__ == "__main__":
    test_timeout_converted_to_milliseconds()
    test_different_timeout_values()
    test_retry_options_match_addon_config_constants()
    print("\nTODOS OS TESTES PASSARAM")
