# -*- coding: utf-8 -*-
import copy
import json
import os


_CONFIG_FILE_ENV = "CHAOS_RUNNER_CONFIG_FILE"
_ENV_PREFIX = "CHAOS_RUNNER_"
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)


_SPECS = {
    "NS_TARGET": {"default": "ns-dupf"},
    "RC_SVC_NAME": {"default": "dupf-registry-center"},
    "RC_API_PORT": {"default": 8158},
    "RC_CLUSTER_API_PATH": {"default": "/api/paas/v1/maintenance/rc/cluster"},
    "WF_NAMESPACE": {"default": "default"},
    "EXECUTION_MODE": {"default": "local"},
    "REMOTE_APPLY_HOST": {"default": ""},
    "REMOTE_APPLY_PORT": {"default": 22},
    "REMOTE_APPLY_USER": {"default": ""},
    "REMOTE_APPLY_BASE_DIR": {"default": "/tmp/chaos-runner"},
    "REMOTE_APPLY_SSH_EXTRA_OPTS": {"default": "-T"},
    "REMOTE_APPLY_RETRIES": {"default": 2},
    "REMOTE_APPLY_RETRY_DELAY_SECONDS": {"default": 2},
    "DEFAULT_WAIT_SECONDS": {"default": 25},
    "DELETE_WORKFLOW_AFTER": {"default": True},
    "OAM_CONTAINER": {"default": "lmt-cli"},
    "LMT_PASSWORD": {"default": ""},
    "LMT_TABLE": {"default": "upfGetTalkerRole"},
    "LMT_USER": {"default": ""},
    "LMT_IP": {"default": "127.0.0.1"},
    "LMT_PORT": {"default": 8153},
    "LMT_TARGET_MODE": {"default": "auto"},
    "LMT_POD_NAME": {"default": ""},
    "LMT_POD_PREFIX": {"default": "lmt-cli"},
    "LMT_POD_CONTAINER": {"default": ""},
    "LMT_COMMANDS": {"default": []},
    "LMT_PRE_COMMANDS": {"default": []},
    "LMT_POST_COMMANDS": {"default": []},
    "OBSERVER_LOG_MAX_WORKERS": {"default": 4},
    "UPC_PODNAME_HINT": {"default": "upc"},
    "DDB_EXEC_POD": {"default": "dupf-ddb-shd-0-0"},
    "DDB_POD_PREFIX": {"default": "dupf-ddb"},
    "REDIS_PORT": {"default": 17380},
    "REDIS_AUTH": {"default": ""},
    "EXPECTED_MASTER_COUNT": {"default": 3},
    "SDB_POD_PREFIX": {"default": "dupf-sdb"},
    "SDB_PORT": {"default": 17369},
    "SDB_AUTH": {"default": ""},
    "SDB_SENTINEL_POD_PREFIX": {"default": "dupf-sdb-sentinel"},
    "SDB_SENTINEL_PORT": {"default": 26380},
    "RC_HTTP_TIMEOUT": {"default": 5},
    "RC_HTTP_RETRIES": {"default": 2},
    "RC_HTTP_RETRY_BACKOFF_SECONDS": {"default": 0.5},
    "NET_VERIFY_ENABLED": {"default": True},
    "NET_VERIFY_SSH_USER": {"default": "root"},
    "NET_VERIFY_REMOTE_SSH_USER": {"default": "gsta"},
    "NET_VERIFY_LOCAL_EXEC_USER": {"default": "root"},
    "NET_VERIFY_SSH_PORT": {"default": 50163},
    "NET_VERIFY_SSH_EXTRA_OPTS": {"default": "-T"},
    "NET_VERIFY_SSH_CONFIG_FILE": {"default": "/dev/null"},
    "NET_VERIFY_NODE_HOST_MAP": {"default": {}},
    "NET_VERIFY_REMOTE_SUDO_PASSWORD": {"default": ""},
    "NET_VERIFY_LOCAL_SUDO_PASSWORD": {"default": ""},
    "NET_VERIFY_START_DELAY": {"default": 3},
    "NET_VERIFY_TIMEOUT_SECONDS": {"default": 20},
    "NET_VERIFY_POLL_INTERVAL_SECONDS": {"default": 2},
    "NETWORK_PHASE_EXTRA_BUFFER_SECONDS": {"default": 10},
    "NET_VERIFY_SAMPLE_INTERVAL": {"default": 5},
    "NET_VERIFY_LOCAL_NODE_NAMES": {"default": []},
    "NET_VERIFY_PING_COUNT": {"default": 10},
    "NET_VERIFY_PING_INTERVAL_SEC": {"default": 0.2},
    "NET_VERIFY_PING_TIMEOUT_SEC": {"default": 1},
    "NET_VERIFY_MAX_WORKERS": {"default": 8},
}


_CURRENT = {}


def _clone(value):
    return copy.deepcopy(value)


def _boolify(value):
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise RuntimeError("invalid boolean value: {}".format(value))


def _listify(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    s = str(value).strip()
    if not s:
        return []
    if s.startswith("["):
        parsed = json.loads(s)
        if not isinstance(parsed, list):
            raise RuntimeError("expected JSON list, got {}".format(type(parsed).__name__))
        return parsed
    return [x.strip() for x in s.split(",") if x.strip()]


def _dictify(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    s = str(value).strip()
    if not s:
        return {}
    parsed = json.loads(s)
    if not isinstance(parsed, dict):
        raise RuntimeError("expected JSON object, got {}".format(type(parsed).__name__))
    return parsed


def _coerce(key, value):
    default = _SPECS[key]["default"]
    if isinstance(default, bool):
        return _boolify(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, list):
        return _listify(value)
    if isinstance(default, dict):
        return _dictify(value)
    return str(value)


def _normalize_key(key):
    return str(key or "").strip().upper()


def _load_config_file(path):
    if not path:
        return {}
    if not os.path.exists(path):
        raise RuntimeError("config file not found: {}".format(path))
    from chaos_runner import yaml_compat as yaml

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError("config file must contain a mapping: {}".format(path))
    out = {}
    for raw_key, raw_value in data.items():
        key = _normalize_key(raw_key)
        if key in _SPECS:
            out[key] = _coerce(key, raw_value)
    return out


def _default_config_candidates():
    return [
        os.path.join(_PROJECT_ROOT, "config.local.yaml"),
        os.path.join(_PROJECT_ROOT, "config.yaml"),
        os.path.join(_THIS_DIR, "config.local.yaml"),
        os.path.join(_THIS_DIR, "config.yaml"),
    ]


def _resolve_config_file_path(config_file, env):
    explicit = str(config_file or env.get(_CONFIG_FILE_ENV, "")).strip()
    if explicit:
        return explicit
    for candidate in _default_config_candidates():
        if os.path.exists(candidate):
            return candidate
    return ""


def _defaults():
    return {key: _clone(spec["default"]) for key, spec in _SPECS.items()}


def _infer_product_prefix(namespace):
    text = str(namespace or "").strip().lower()
    if not text:
        return "dupf"
    if text.startswith("ns-"):
        text = text[3:]
    out = []
    for ch in text:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out) or "dupf"


def _apply_namespace_profile(merged):
    prefix = _infer_product_prefix(merged.get("NS_TARGET"))
    if prefix == "dupf":
        return merged

    defaults = _defaults()
    derived = {
        "RC_SVC_NAME": "{}-registry-center".format(prefix),
        "DDB_EXEC_POD": "{}-ddb-shd-0-0".format(prefix),
        "DDB_POD_PREFIX": "{}-ddb".format(prefix),
        "SDB_POD_PREFIX": "{}-sdb".format(prefix),
        "SDB_SENTINEL_POD_PREFIX": "{}-sdb-sentinel".format(prefix),
        "UPC_PODNAME_HINT": prefix,
    }
    for key, value in derived.items():
        if merged.get(key) == defaults.get(key):
            merged[key] = value
    return merged


def load(config_file="", env=None):
    env = env or os.environ
    file_path = _resolve_config_file_path(config_file, env)

    merged = _defaults()
    merged.update(_load_config_file(file_path))

    for key in _SPECS:
        env_key = "{}{}".format(_ENV_PREFIX, key)
        if env_key in env:
            merged[key] = _coerce(key, env.get(env_key))

    merged = _apply_namespace_profile(merged)

    merged["ACTIVE_CONFIG_FILE"] = file_path
    _CURRENT.clear()
    _CURRENT.update(merged)
    globals().update(merged)
    return to_dict(mask_secrets=False)


def to_dict(mask_secrets=True):
    data = {}
    for key in _SPECS:
        val = _CURRENT.get(key, _clone(_SPECS[key]["default"]))
        if mask_secrets and key in ("LMT_PASSWORD", "REDIS_AUTH", "SDB_AUTH", "NET_VERIFY_REMOTE_SUDO_PASSWORD", "NET_VERIFY_LOCAL_SUDO_PASSWORD") and val:
            data[key] = "***"
        else:
            data[key] = _clone(val)
    data["ACTIVE_CONFIG_FILE"] = _CURRENT.get("ACTIVE_CONFIG_FILE", "")
    return data


def is_lmt_configured():
    return bool(str(LMT_USER).strip() and str(LMT_PASSWORD).strip())


def is_ddb_configured():
    return bool(str(REDIS_AUTH).strip())


def is_sdb_configured():
    return bool(str(SDB_AUTH).strip())


def validate_case_dependencies(case):
    targets = case.get("targets") or []
    finders = {(item.get("finder") or "").strip() for item in targets if isinstance(item, dict)}

    if any(name.startswith("ddb") for name in finders) and not is_ddb_configured():
        raise RuntimeError(
            "DDB discovery requires REDIS_AUTH. "
            "Provide CHAOS_RUNNER_REDIS_AUTH or set REDIS_AUTH in the config file."
        )

    if any(name.startswith("sdb") for name in finders) and not is_sdb_configured():
        raise RuntimeError(
            "SDB discovery requires SDB_AUTH. "
            "Provide CHAOS_RUNNER_SDB_AUTH or set SDB_AUTH in the config file."
        )

    mode = str(_CURRENT.get("EXECUTION_MODE", "local") or "local").strip().lower()
    if mode == "remote_apply" and not str(_CURRENT.get("REMOTE_APPLY_HOST", "") or "").strip():
        raise RuntimeError(
            "remote_apply execution requires REMOTE_APPLY_HOST. "
            "Provide CHAOS_RUNNER_REMOTE_APPLY_HOST or set REMOTE_APPLY_HOST in the config file."
        )


load()
