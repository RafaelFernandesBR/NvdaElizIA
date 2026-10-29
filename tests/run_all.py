# -*- coding: utf-8 -*-
"""
Roda todos os testes standalone (sem rede, sem NVDA) em sequência.
test_real_api_integration.py fica de fora de propósito — é opt-in, só pra
rodar manualmente quando se quer validar contra a API de verdade.

Rodar: python tests/run_all.py
"""
import os
import runpy
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_TEST_FILES = [
    "test_action_tools.py",
    "test_action_processor.py",
    "test_execute_batch.py",
    "test_conversation_history.py",
    "test_screen_capture.py",
    "test_screen_curtain.py",
    "test_session_store.py",
    "test_scheduler_store.py",
    "test_scheduler.py",
    "test_gemini_http_options.py",
    "test_screen_wait.py",
]

if __name__ == "__main__":
    failures = []
    for filename in LOCAL_TEST_FILES:
        path = os.path.join(TESTS_DIR, filename)
        print(f"\n=== {filename} ===")
        try:
            runpy.run_path(path, run_name="__main__")
        except SystemExit as e:
            if e.code not in (0, None):
                failures.append(filename)
        except Exception as e:
            print(f"FALHOU: {filename}: {e}")
            failures.append(filename)

    print("\n" + "=" * 60)
    if failures:
        print(f"FALHAS EM: {', '.join(failures)}")
        sys.exit(1)
    print("TODOS OS ARQUIVOS DE TESTE PASSARAM")
