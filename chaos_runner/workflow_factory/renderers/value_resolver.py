# -*- coding: utf-8 -*-
import random
import re

_DURATION_UNITS = {
    "ns": 1e-9,
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1,
    "m": 60,
    "h": 3600,
}


def _parse_duration_seconds(val, field_name):
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)?$", s, re.IGNORECASE)
    if not m:
        raise RuntimeError("invalid {} duration: {}".format(field_name, val))
    num = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    return num * _DURATION_UNITS[unit]


def _duration_to_str(seconds):
    if abs(seconds) < 1:
        ms = seconds * 1000
        text = "{:.3f}".format(ms).rstrip("0").rstrip(".")
        return "{}ms".format(text or "0")
    text = "{:.3f}".format(seconds).rstrip("0").rstrip(".")
    return "{}s".format(text or "0")


def resolve_duration(value, field_name, default=None):
    """
    Resolve duration with random-range support.

    Supported range forms:
    - "100ms~500ms"
    - {min: 100ms, max: 500ms}
    """
    if value is None or value == "":
        value = default
    if value is None or value == "":
        return "0s"

    if isinstance(value, dict):
        if "min" in value and "max" in value:
            low = _parse_duration_seconds(value.get("min"), field_name)
            high = _parse_duration_seconds(value.get("max"), field_name)
            if low > high:
                raise RuntimeError("{} range min > max".format(field_name))
            return _duration_to_str(random.uniform(low, high))
        raise RuntimeError("{} range dict must include min and max".format(field_name))

    if isinstance(value, str) and "~" in value:
        left, right = [x.strip() for x in value.split("~", 1)]
        low = _parse_duration_seconds(left, field_name)
        high = _parse_duration_seconds(right, field_name)
        if low > high:
            raise RuntimeError("{} range min > max".format(field_name))
        return _duration_to_str(random.uniform(low, high))

    return _duration_to_str(_parse_duration_seconds(value, field_name))


def resolve_percent(value, field_name, default=None):
    """
    Resolve numeric/percent value with random-range support.

    Supported range forms:
    - "1~10"
    - {min: 1, max: 10}
    """
    if value is None or value == "":
        value = default
    if value is None or value == "":
        return "0"

    if isinstance(value, dict):
        if "min" not in value or "max" not in value:
            raise RuntimeError("{} range dict must include min and max".format(field_name))
        low = float(value.get("min"))
        high = float(value.get("max"))
    elif isinstance(value, str) and "~" in value:
        left, right = [x.strip() for x in value.split("~", 1)]
        low = float(left)
        high = float(right)
    else:
        return str(value)

    if low > high:
        raise RuntimeError("{} range min > max".format(field_name))
    sampled = random.uniform(low, high)
    if abs(sampled - round(sampled)) < 1e-9:
        return str(int(round(sampled)))
    return "{:.2f}".format(sampled).rstrip("0").rstrip(".")
