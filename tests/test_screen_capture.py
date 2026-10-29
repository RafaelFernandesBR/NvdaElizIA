# -*- coding: utf-8 -*-
"""
Testa screen_capture._maybe_downscale isolado. wxPython é fornecido pelo
próprio NVDA (não está em lib/, `pip install` não resolve isso num Python
qualquer) — então aqui registramos um `wx` mínimo e uma imagem falsa que só
imita a interface usada (GetWidth/GetHeight/Scale), o suficiente pra validar
a aritmética de redimensionamento sem precisar da lib real nem de um
display.

Rodar: python tests/test_screen_capture.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module, stub_wx_module

stub_wx_module()

addon_config = load_addon_module("addon_config")
screen_capture = load_addon_module("screen_capture")


class FakeImage:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self.scaled_to = None

    def GetWidth(self):
        return self._w

    def GetHeight(self):
        return self._h

    def Scale(self, new_w, new_h, quality):
        self.scaled_to = (new_w, new_h)
        return FakeImage(new_w, new_h)


def test_within_limit_untouched():
    img = FakeImage(800, 600)
    out = screen_capture._maybe_downscale(img, addon_config.MAX_SCREENSHOT_DIMENSION)
    assert out is img, "imagem dentro do limite não deveria ser redimensionada"
    print("test_within_limit_untouched OK")


def test_exactly_at_limit_untouched():
    cap = addon_config.MAX_SCREENSHOT_DIMENSION
    img = FakeImage(cap, cap // 2)
    assert screen_capture._maybe_downscale(img, cap) is img
    print("test_exactly_at_limit_untouched OK")


def test_4k_downscaled_preserving_aspect_ratio():
    img = FakeImage(3840, 2160)  # 16:9
    out = screen_capture._maybe_downscale(img, 1568)
    new_w, new_h = img.scaled_to
    assert new_w == 1568
    assert abs((new_w / new_h) - (3840 / 2160)) < 0.01, (new_w, new_h)
    assert out.GetWidth() == new_w and out.GetHeight() == new_h
    print(f"test_4k_downscaled_preserving_aspect_ratio OK (3840x2160 -> {new_w}x{new_h})")


def test_ultrawide_downscale_on_width():
    img = FakeImage(5120, 1440)
    out = screen_capture._maybe_downscale(img, 1568)
    new_w, new_h = img.scaled_to
    assert new_w == 1568 and new_h < 1440
    print(f"test_ultrawide_downscale_on_width OK (5120x1440 -> {new_w}x{new_h})")


def test_portrait_downscale_on_height():
    img = FakeImage(1080, 1920)  # monitor rotacionado
    out = screen_capture._maybe_downscale(img, 1568)
    new_w, new_h = img.scaled_to
    assert new_h == 1568 and new_w < 1080
    print(f"test_portrait_downscale_on_height OK (1080x1920 -> {new_w}x{new_h})")


def test_never_scales_to_zero():
    img = FakeImage(100000, 1)  # caso extremo degenerado
    out = screen_capture._maybe_downscale(img, 1568)
    new_w, new_h = img.scaled_to
    assert new_w >= 1 and new_h >= 1
    print(f"test_never_scales_to_zero OK (100000x1 -> {new_w}x{new_h})")


if __name__ == "__main__":
    test_within_limit_untouched()
    test_exactly_at_limit_untouched()
    test_4k_downscaled_preserving_aspect_ratio()
    test_ultrawide_downscale_on_width()
    test_portrait_downscale_on_height()
    test_never_scales_to_zero()
    print("\nTODOS OS TESTES PASSARAM")
