# -*- coding: utf-8 -*-
"""
Detecção de Cortina de Tela ativa — a cortina pinta a tela real de preto
(recurso de privacidade do NVDA), o que faz capture_fullscreen() (GDI puro,
ver screen_capture.py) devolver um bitmap preto. A IA não tem como saber
que a tela está sendo propositalmente escondida, então "fica perdida"
alucinando ações sobre uma tela que não existe — este módulo existe pra
__init__.py recusar a automação antes disso acontecer.

A API pra perguntar "a cortina está ativa agora?" mudou na versão 2026.1
do NVDA (a cortina deixou de ser um VisionEnhancementProvider e passou a
ser um módulo próprio, profile-independent) — como o manifest deste addon
suporta de 2019.3 a 2026.4, as duas precisam ser tratadas.
"""


def is_screen_curtain_active():
    """
    True só se a cortina de tela estiver ativa AGORA (não apenas marcada
    como habilitada na config — mesma distinção que o próprio NVDA faz
    entre "habilitada" e "ativa"). Qualquer falha nessa checagem (atributo
    renomeado numa versão de NVDA não prevista, módulo ausente, etc.)
    devolve False: nunca travar o usuário fora do próprio addon por um
    erro NESTA checagem — o pior caso nesse cenário é só voltar ao
    comportamento de hoje.
    """
    try:
        try:
            import screenCurtain  # NVDA >= 2026.1
        except ImportError:
            screenCurtain = None

        if screenCurtain is not None:
            instance = getattr(screenCurtain, "screenCurtain", None)
            return bool(instance is not None and instance.enabled)

        import vision  # NVDA 2019.3 - 2025.3.x
        handler = getattr(vision, "handler", None)
        if handler is None:
            return False
        return any(
            info.providerId == "screenCurtain"
            for info in handler.getActiveProviderInfos()
        )
    except Exception:
        return False
