# -*- coding: utf-8 -*-
"""
Harness compartilhado pros testes standalone do operadorIA — carrega
módulos do addon FORA do NVDA, sem precisar instalar nada (usa a SDK
bundled em addon/globalPlugins/operadorIA/lib/) e sem precisar do NVDA
rodando.

`lib/` é inserido no INÍCIO do sys.path (`insert(0, ...)`, não `append`) —
isso significa que módulos vendorizados ali (ex: `sqlite3`, que a build de
Python do NVDA não inclui, ver `session_store.py`) SOMBREIAM a cópia do
Python do sistema durante os testes. Ou seja, `test_session_store.py` já
exercita a cópia exata de `sqlite3` que vai pro NVDA, não a do sistema —
não é só "a lógica funciona com algum sqlite3 qualquer".

Por que isso funciona: a maioria dos módulos do addon só depende da SDK
google-genai (bundled) e de stdlib. Só alguns módulos importam APIs
exclusivas do NVDA/Windows no nível do módulo (`api`, `config`, `wx`,
`winUser`, `mouseHandler`, `addonHandler`) — pra esses, basta registrar um
módulo falso em `sys.modules` ANTES de importar o módulo real; o Python
nunca percebe a diferença, e o módulo sob teste nem precisa saber que está
sendo testado fora do NVDA.

`addonHandler` é stubado automaticamente ao importar este arquivo (ver
`stub_addon_handler_module()` abaixo) — qualquer módulo que chame
`addonHandler.initTranslation()` já funciona sem precisar de nenhuma
chamada extra no seu test_*.py.

Uso típico, no topo de um test_*.py:

    from _harness import ADDON_DIR, load_addon_module, stub_nvda_modules
    stub_nvda_modules()  # só se o módulo que você vai carregar precisar
    addon_config = load_addon_module("addon_config")
    action_tools = load_addon_module("action_tools")
"""

import importlib.util
import inspect
import os
import sys
import types as _pytypes

ADDON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addon", "globalPlugins", "operadorIA"
)

_LIB_DIR = os.path.join(ADDON_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

_PKG_NAME = "_operadorIA_standalone"
if _PKG_NAME not in sys.modules:
    _pkg = _pytypes.ModuleType(_PKG_NAME)
    _pkg.__path__ = [ADDON_DIR]
    sys.modules[_PKG_NAME] = _pkg


def stub_addon_handler_module():
    """
    O addonHandler.initTranslation() real usa getCodeAddon(frameDist=2), que
    depende do registro de addons carregados pelo NVDA — não existe fora do
    NVDA rodando. Este stub replica só a mecânica documentada (injetar
    _/ngettext/pgettext/npgettext como identidade no módulo chamador) sem
    tentar resolver nenhum addon real.

    Registrado incondicionalmente (abaixo, no import deste arquivo) — ao
    contrário de stub_nvda_modules()/stub_wx_module(), não precisa ser
    chamado explicitamente em cada test_*.py, já que é puro Python sem
    efeito colateral.
    """
    if "addonHandler" not in sys.modules:
        addon_handler_stub = _pytypes.ModuleType("addonHandler")

        def _init_translation():
            caller_globals = inspect.currentframe().f_back.f_globals
            caller_globals["_"] = lambda s: s
            caller_globals["ngettext"] = lambda s, p, n: s if n == 1 else p
            caller_globals["pgettext"] = lambda ctx, s: s
            caller_globals["npgettext"] = lambda ctx, s, p, n: s if n == 1 else p

        addon_handler_stub.initTranslation = _init_translation
        sys.modules["addonHandler"] = addon_handler_stub


stub_addon_handler_module()


def load_addon_module(modname):
    """
    Carrega um módulo de globalPlugins/operadorIA/<modname>.py como se
    fosse `from . import <modname>` de dentro do addon — os imports
    relativos dentro dele (`from . import addon_config`, etc.) funcionam
    porque o módulo é registrado sob o pacote falso `_operadorIA_standalone`.

    Chame os stubs de `stub_nvda_modules()` ANTES de carregar um módulo que
    precise deles (ex: utils.py importa `api` no nível do módulo).
    """
    full_name = f"{_PKG_NAME}.{modname}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, os.path.join(ADDON_DIR, f"{modname}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


def stub_nvda_modules(include_config=False):
    """
    Registra módulos falsos pras APIs exclusivas do NVDA que alguns módulos
    do addon importam no nível do módulo. Chamar antes de qualquer
    load_addon_module() que precise deles.

    - `api`: usado por utils.get_focused_app_name(). Só precisa existir;
      o stub devolve None em getForegroundObject(), o que já cai no
      except (AttributeError, TypeError) do próprio addon.
    - `config` (NVDA, não confundir com addon_config.py do addon): usado
      por gemini_client.py e __init__.py pra ler config.conf["ElizIA"].
      Só stube se for testar esses dois (include_config=True) — a maioria
      dos testes (action_tools, action_processor, utils) não precisa.
    """
    if "api" not in sys.modules:
        api_stub = _pytypes.ModuleType("api")
        api_stub.getForegroundObject = lambda: None
        api_stub.getClipData = lambda: ""
        api_stub.copyToClip = lambda text: None
        sys.modules["api"] = api_stub

    if include_config and "config" not in sys.modules:
        sys.modules["config"] = _pytypes.ModuleType("config")


def stub_wx_module():
    """
    screen_capture.py importa `wx` (wxPython, fornecido pelo próprio NVDA —
    NÃO está em lib/, então nem pip install resolve isso num Python
    standalone comum). _maybe_downscale() só usa Image.GetWidth/GetHeight/
    Scale e a constante wx.IMAGE_QUALITY_HIGH — o suficiente pra testar a
    lógica de redimensionamento com uma imagem falsa (ver
    tests/test_screen_capture.py), sem precisar da lib real nem de um
    display de verdade.
    """
    if "wx" not in sys.modules:
        wx_stub = _pytypes.ModuleType("wx")
        wx_stub.IMAGE_QUALITY_HIGH = 2
        sys.modules["wx"] = wx_stub


def read_gemini_api_key_from_nvda_ini():
    """
    Lê a gemini_api_key já configurada no painel de configurações do NVDA,
    direto do nvda.ini do usuário atual — só para os testes de integração
    real (ver test_real_api_integration.py). NUNCA logar/imprimir o valor
    retornado; use só pra construir o cliente da SDK.

    Returns:
        str | None — None se o NVDA nunca foi configurado neste perfil, ou
        se a chave está vazia.
    """
    import re

    ini_path = os.path.join(os.environ.get("APPDATA", ""), "nvda", "nvda.ini")
    if not os.path.isfile(ini_path):
        return None
    with open(ini_path, "r", encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"gemini_api_key\s*=\s*(\S+)", text)
    return match.group(1) if match else None
