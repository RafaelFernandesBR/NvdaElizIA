# -*- coding: utf-8 -*-
"""
Espera adaptativa de tela — substitui os sleeps fixos do loop de automação
(ver __init__.py/_run_ai_loop e _execute_batch) por polling de hash MD5 da
tela até ela estabilizar, com um teto que nunca bloqueia pra sempre.

Função pura: captura/clock/sleep são todos injetados como callables, pelo
mesmo motivo que ActionProcessor.run_batch recebe execute_fn/capture_fn —
poder ser testada sem o NVDA rodando (ver tests/test_screen_wait.py).
"""

import hashlib
import time
from typing import Callable, NamedTuple, Optional

from . import addon_config


class StabilityResult(NamedTuple):
    img_base64: Optional[str]
    img_hash: Optional[str]
    stabilized: bool
    elapsed: float
    reads: int


def wait_until_stable(
    capture_fn: Callable[[], Optional[str]],
    max_wait: float,
    poll_interval: Optional[float] = None,
    stable_reads: Optional[int] = None,
    clock_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> StabilityResult:
    """
    Chama `capture_fn()` repetidamente até obter `stable_reads` leituras
    CONSECUTIVAS com o mesmo hash MD5 (tela "parada"), ou até `max_wait`
    segundos se esgotarem — o que vier primeiro. Nunca bloqueia além do
    teto: ao expirar, devolve a última imagem capturada (não None) com
    `stabilized=False`, para o chamador decidir como avisar o usuário.

    Uma captura `None` isolada no meio do polling (falha transitória) é
    ignorada — não reseta a contagem de estabilidade. Só a PRIMEIRA
    captura `None` retorna imediatamente (sem nenhum poll), delegando a
    falha para o tratamento de erro que o chamador já tem para esse caso.

    Args:
        capture_fn: callable() -> img_base64 (str) | None.
        max_wait: teto de espera em segundos.
        poll_interval: intervalo entre capturas (default:
            addon_config.STABILITY_POLL_INTERVAL).
        stable_reads: leituras consecutivas iguais para considerar estável
            (default: addon_config.STABILITY_STABLE_READS).
        clock_fn/sleep_fn: injetáveis para teste (ver tests/test_screen_wait.py).

    Returns:
        StabilityResult
    """
    if poll_interval is None:
        poll_interval = addon_config.STABILITY_POLL_INTERVAL
    if stable_reads is None:
        stable_reads = addon_config.STABILITY_STABLE_READS

    start = clock_fn()
    img = capture_fn()
    reads = 1
    if img is None:
        return StabilityResult(None, None, False, 0.0, reads)

    last_hash = hashlib.md5(img.encode()).hexdigest()
    consecutive = 1

    while consecutive < stable_reads:
        elapsed = clock_fn() - start
        if elapsed >= max_wait:
            return StabilityResult(img, last_hash, False, elapsed, reads)

        sleep_fn(poll_interval)
        new_img = capture_fn()
        reads += 1
        if new_img is None:
            continue

        img = new_img
        new_hash = hashlib.md5(new_img.encode()).hexdigest()
        if new_hash == last_hash:
            consecutive += 1
        else:
            consecutive = 1
            last_hash = new_hash

    return StabilityResult(img, last_hash, True, clock_fn() - start, reads)
