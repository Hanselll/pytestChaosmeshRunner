# -*- coding: utf-8 -*-
import importlib

RENDERERS = {}


def register(name):
    def deco(fn):
        RENDERERS[name] = fn
        return fn
    return deco


def get(name):
    if name not in RENDERERS:
        raise RuntimeError("Unknown renderer: {} (available={})".format(name, sorted(RENDERERS.keys())))
    return RENDERERS[name]


def _safe_import(module_name):
    try:
        importlib.import_module("{}.{}".format(__name__, module_name))
    except ModuleNotFoundError:
        # Keep runner bootable even when some renderer files are absent in a deployment.
        # Missing renderer will be surfaced later as Unknown renderer when selected by case.
        pass


# Best-effort import built-in renderer modules so their @register side-effects happen.
for _m in (
    "parallel_podkill",
    "podkill_then_network",
    "network_then_parallel_podkill",
    "network_parallel_containerkill",
    "pod_stress",
    "modular_chaos",
):
    _safe_import(_m)
