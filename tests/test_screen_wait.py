# -*- coding: utf-8 -*-
"""
Testa screen_wait.wait_until_stable — a espera adaptativa que substitui
os sleeps fixos do loop de automação (ver __init__.py) por polling de hash
até a tela estabilizar, com teto. É o CÓDIGO REAL (não uma réplica):
wait_until_stable recebe `capture_fn`/`clock_fn`/`sleep_fn` injetados
exatamente pra poder ser testado aqui sem o NVDA/tela de verdade.

Rodar: python tests/test_screen_wait.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

screen_wait = load_addon_module("screen_wait")
wait_until_stable = screen_wait.wait_until_stable


class FakeClock:
    """now()/sleep() avançam um contador em vez de dormir de verdade."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def make_capture_fn(screenshots):
    it = iter(screenshots)
    return lambda: next(it)


def test_stabilizes_after_two_equal_reads():
    clock = FakeClock()
    result = wait_until_stable(
        make_capture_fn(["a", "a"]), max_wait=5.0, poll_interval=0.2, stable_reads=2,
        clock_fn=clock.now, sleep_fn=clock.sleep,
    )
    assert result.stabilized is True
    assert result.reads == 2
    assert result.elapsed == 0.2
    assert result.img_base64 == "a"
    print("test_stabilizes_after_two_equal_reads OK")


def test_times_out_when_screen_keeps_changing():
    clock = FakeClock()
    result = wait_until_stable(
        make_capture_fn(["a", "b", "a", "b", "a"]), max_wait=0.3, poll_interval=0.2, stable_reads=2,
        clock_fn=clock.now, sleep_fn=clock.sleep,
    )
    assert result.stabilized is False
    assert result.elapsed >= 0.3
    assert result.img_base64 is not None
    assert result.img_hash is not None
    print("test_times_out_when_screen_keeps_changing OK")


def test_first_capture_failure_returns_immediately():
    clock = FakeClock()
    calls = []
    result = wait_until_stable(
        make_capture_fn([None]), max_wait=5.0, poll_interval=0.2, stable_reads=2,
        clock_fn=clock.now, sleep_fn=lambda s: calls.append(s),
    )
    assert result.img_base64 is None
    assert result.stabilized is False
    assert result.reads == 1
    assert calls == []  # nunca chegou a fazer polling
    print("test_first_capture_failure_returns_immediately OK")


def test_capture_failure_mid_poll_is_tolerated():
    clock = FakeClock()
    result = wait_until_stable(
        make_capture_fn(["a", None, "a"]), max_wait=5.0, poll_interval=0.2, stable_reads=2,
        clock_fn=clock.now, sleep_fn=clock.sleep,
    )
    assert result.stabilized is True
    assert result.img_base64 == "a"
    assert result.reads == 3
    print("test_capture_failure_mid_poll_is_tolerated OK")


def test_zero_max_wait_does_not_loop_forever():
    clock = FakeClock()
    calls = []
    result = wait_until_stable(
        make_capture_fn(["a", "b", "c"]), max_wait=0.0, poll_interval=0.2, stable_reads=2,
        clock_fn=clock.now, sleep_fn=lambda s: calls.append(s),
    )
    assert result.stabilized is False
    assert result.reads == 1
    assert calls == []  # expirou antes de qualquer sleep
    print("test_zero_max_wait_does_not_loop_forever OK")


if __name__ == "__main__":
    test_stabilizes_after_two_equal_reads()
    test_times_out_when_screen_keeps_changing()
    test_first_capture_failure_returns_immediately()
    test_capture_failure_mid_poll_is_tolerated()
    test_zero_max_wait_does_not_loop_forever()
    print("\nTODOS OS TESTES PASSARAM")
