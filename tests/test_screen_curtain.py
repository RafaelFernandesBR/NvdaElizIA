# -*- coding: utf-8 -*-
"""
Testa screen_curtain.is_screen_curtain_active() contra as duas APIs do
NVDA (vision, 2019.3-2025.3.x; screenCurtain, >= 2026.1), sem precisar do
NVDA rodando. Como os imports de vision/screenCurtain são lazy (dentro da
própria função), não precisa de nenhum stub registrado de antemão em
_harness.py — cada caso injeta o módulo falso que precisa direto em
sys.modules e remove no final.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load_addon_module

screen_curtain = load_addon_module("screen_curtain")


class _FakeProviderInfo:
    def __init__(self, provider_id):
        self.providerId = provider_id


class _FakeVisionHandler:
    def __init__(self, active_ids=None, raise_on_get=False):
        self._active_ids = active_ids or []
        self._raise_on_get = raise_on_get

    def getActiveProviderInfos(self):
        if self._raise_on_get:
            raise RuntimeError("falha simulada")
        return [_FakeProviderInfo(pid) for pid in self._active_ids]


class _FakeScreenCurtainInstance:
    def __init__(self, enabled):
        self.enabled = enabled


def _clear_fake_modules():
    sys.modules.pop("vision", None)
    sys.modules.pop("screenCurtain", None)


def _set_vision(handler):
    _clear_fake_modules()
    vision_mod = types.ModuleType("vision")
    vision_mod.handler = handler
    sys.modules["vision"] = vision_mod


def _set_screen_curtain(instance):
    _clear_fake_modules()
    sc_mod = types.ModuleType("screenCurtain")
    sc_mod.screenCurtain = instance
    sys.modules["screenCurtain"] = sc_mod


def run():
    failures = []

    def check(label, condition):
        if condition:
            print(f"OK: {label}")
        else:
            print(f"FALHOU: {label}")
            failures.append(label)

    try:
        # 1. Nenhuma das duas APIs disponível (NVDA hipotético sem vision/screenCurtain).
        _clear_fake_modules()
        check(
            "sem vision/screenCurtain disponíveis -> False",
            screen_curtain.is_screen_curtain_active() is False,
        )

        # 2-4. API nova (NVDA >= 2026.1).
        _set_screen_curtain(_FakeScreenCurtainInstance(enabled=True))
        check(
            "screenCurtain.enabled=True -> True",
            screen_curtain.is_screen_curtain_active() is True,
        )

        _set_screen_curtain(_FakeScreenCurtainInstance(enabled=False))
        check(
            "screenCurtain.enabled=False -> False",
            screen_curtain.is_screen_curtain_active() is False,
        )

        _set_screen_curtain(None)
        check(
            "screenCurtain.screenCurtain is None (não inicializado) -> False",
            screen_curtain.is_screen_curtain_active() is False,
        )

        # 5-7. API antiga (NVDA 2019.3 - 2025.3.x).
        _set_vision(_FakeVisionHandler(active_ids=["screenCurtain"]))
        check(
            "vision.handler com screenCurtain ativo -> True",
            screen_curtain.is_screen_curtain_active() is True,
        )

        _set_vision(_FakeVisionHandler(active_ids=["magnifier"]))
        check(
            "vision.handler só com outro provider ativo -> False",
            screen_curtain.is_screen_curtain_active() is False,
        )

        _set_vision(_FakeVisionHandler(active_ids=[]))
        check(
            "vision.handler sem nenhum provider ativo -> False",
            screen_curtain.is_screen_curtain_active() is False,
        )

        _set_vision(None)
        check(
            "vision.handler is None (vision.initialize() nunca rodou) -> False",
            screen_curtain.is_screen_curtain_active() is False,
        )

        # 8. Falha inesperada na própria checagem -> fail-open (False), nunca propaga.
        _set_vision(_FakeVisionHandler(raise_on_get=True))
        check(
            "getActiveProviderInfos() lança exceção -> False (fail-open)",
            screen_curtain.is_screen_curtain_active() is False,
        )
    finally:
        _clear_fake_modules()

    if failures:
        print(f"\nFALHAS: {', '.join(failures)}")
        return 1
    print("\nTodos os testes de screen_curtain passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
