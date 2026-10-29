# -*- coding: utf-8 -*-
"""
Módulo de captura de tela
"""

import base64
import io
import logging
import wx
from . import addon_config

logger = logging.getLogger(__name__)


def _maybe_downscale(image, max_dim):
    """
    Redimensiona a imagem, preservando aspect ratio, se o lado maior exceder
    max_dim. Retorna a mesma `image` sem cópia se já está dentro do limite.
    """
    largest = max(image.GetWidth(), image.GetHeight())
    if largest <= max_dim:
        return image
    scale = max_dim / float(largest)
    new_w = max(1, round(image.GetWidth() * scale))
    new_h = max(1, round(image.GetHeight() * scale))
    return image.Scale(new_w, new_h, wx.IMAGE_QUALITY_HIGH)


def capture_fullscreen():
    """
    Captura screenshot da tela inteira. A imagem CODIFICADA em base64 pode
    ser redimensionada (ver addon_config.MAX_SCREENSHOT_DIMENSION) antes de
    ser enviada à IA — mas `width`/`height` retornados são sempre a
    resolução REAL da tela, já que ActionProcessor._scale_coordinates
    converte a resposta 0-1000 da IA para pixels reais com base nelas, não
    no tamanho da imagem que a IA efetivamente recebeu.

    Returns:
        tuple: (img_base64, width, height, mime_type) ou (None, 0, 0, "") em caso de erro
    """
    try:
        w, h = wx.GetDisplaySize()
        bmp = wx.Bitmap(w, h)
        dc = wx.MemoryDC(bmp)
        dc.Blit(0, 0, w, h, wx.ScreenDC(), 0, 0)

        image = bmp.ConvertToImage()
        image = _maybe_downscale(image, addon_config.MAX_SCREENSHOT_DIMENSION)

        stream = io.BytesIO()
        image.SaveFile(stream, wx.BITMAP_TYPE_PNG)

        img_bytes = stream.getvalue()
        if not img_bytes:
            logger.warning("Captura de tela gerou bytes vazios")
            return None, 0, 0, ""

        img_base64 = base64.b64encode(img_bytes).decode('utf-8')

        return img_base64, w, h, "image/png"

    except (AttributeError, RuntimeError, OSError) as e:
        logger.error(f"Falha ao capturar a tela: {e}", exc_info=True)
        return None, 0, 0, ""


def get_screen_dimensions():
    """
    Obtém as dimensões da tela.
    
    Returns:
        tuple: (width, height)
    """
    try:
        return wx.GetDisplaySize()
    except (AttributeError, RuntimeError):
        return addon_config.DEFAULT_SCREEN_WIDTH, addon_config.DEFAULT_SCREEN_HEIGHT
