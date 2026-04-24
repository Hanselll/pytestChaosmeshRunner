# -*- coding: utf-8 -*-
"""
Small YAML compatibility layer.

Uses PyYAML when available. Otherwise falls back to a minimal subset parser and
dumper that supports the YAML shapes used by this project:
- mappings
- lists
- strings / ints / floats / booleans / null
"""

import ast
import json
import re

try:
    import yaml as _yaml  # type: ignore
except Exception:
    _yaml = None


_NUMERIC_LIKE_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def _strip_comments(line):
    in_single = False
    in_double = False
    out = []
    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
    return "".join(out).rstrip()


def _prepare_lines(text):
    lines = []
    for raw in str(text or "").splitlines():
        cleaned = _strip_comments(raw.rstrip())
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        lines.append((indent, cleaned.strip()))
    return lines


def _parse_scalar(text):
    s = str(text).strip()
    if s == "":
        return ""
    if s in ("null", "Null", "NULL", "~"):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        try:
            return ast.literal_eval(s)
        except Exception:
            return s[1:-1]
    if s in ("[]", "{}"):
        return ast.literal_eval(s)
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
        try:
            return json.loads(s)
        except Exception:
            try:
                return ast.literal_eval(s)
            except Exception:
                return s
    try:
        if s.startswith("0") and len(s) > 1 and s[1].isdigit():
            raise ValueError
        return int(s)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        return s


def _needs_quoted_string(value):
    s = str(value)
    if s == "" or s != s.strip():
        return True
    if s in ("null", "Null", "NULL", "~", "true", "True", "TRUE", "false", "False", "FALSE"):
        return True
    if _NUMERIC_LIKE_RE.match(s):
        return True
    if any(ch in s for ch in [":", "#", "{", "}", "[", "]", ","]):
        return True
    return False


def _split_key_value(text):
    in_single = False
    in_double = False
    for idx, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            return text[:idx].strip(), text[idx + 1 :].strip()
    raise RuntimeError("invalid yaml line: {}".format(text))


def _parse_map(lines, i, indent, first_pair=None):
    out = {}
    pending_key = None
    if first_pair is not None:
        key, rest = first_pair
        if rest == "":
            pending_key = key
            out[key] = None
        else:
            out[key] = _parse_scalar(rest)

    while i < len(lines):
        cur_indent, content = lines[i]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            if pending_key is None:
                raise RuntimeError("unexpected indentation near '{}'".format(content))
            value, i = _parse_node(lines, i, cur_indent)
            out[pending_key] = value
            pending_key = None
            continue
        if content.startswith("- "):
            break
        key, rest = _split_key_value(content)
        i += 1
        if rest == "":
            pending_key = key
            out[key] = None
        else:
            out[key] = _parse_scalar(rest)
            pending_key = None
    return out, i


def _parse_list(lines, i, indent):
    out = []
    while i < len(lines):
        cur_indent, content = lines[i]
        if cur_indent < indent:
            break
        if cur_indent != indent or not (content == "-" or content.startswith("- ")):
            break
        rest = "" if content == "-" else content[2:].strip()
        i += 1
        if rest == "":
            value, i = _parse_node(lines, i, indent + 2)
            out.append(value)
            continue
        if ":" in rest:
            key, value = _split_key_value(rest)
            item, i = _parse_map(lines, i, indent + 2, first_pair=(key, value))
            out.append(item)
            continue
        out.append(_parse_scalar(rest))
    return out, i


def _parse_node(lines, i, indent):
    if i >= len(lines):
        return None, i
    cur_indent, content = lines[i]
    if cur_indent < indent:
        return None, i
    if content == "-" or content.startswith("- "):
        return _parse_list(lines, i, cur_indent)
    return _parse_map(lines, i, cur_indent)


def _fallback_safe_load(text):
    lines = _prepare_lines(text)
    if not lines:
        return None
    obj, _ = _parse_node(lines, 0, lines[0][0])
    return obj


def _dump_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if _needs_quoted_string(s):
        return json.dumps(s, ensure_ascii=False)
    return s


def _fallback_safe_dump(obj, indent=0, sort_keys=False):
    prefix = " " * indent
    if isinstance(obj, dict):
        keys = list(obj.keys())
        if sort_keys:
            keys = sorted(keys)
        lines = []
        for key in keys:
            value = obj[key]
            if isinstance(value, (dict, list)):
                lines.append("{}{}:".format(prefix, key))
                lines.append(_fallback_safe_dump(value, indent + 2, sort_keys=sort_keys))
            else:
                lines.append("{}{}: {}".format(prefix, key, _dump_scalar(value)))
        return "\n".join(lines)
    if isinstance(obj, list):
        lines = []
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.append("{}-".format(prefix))
                lines.append(_fallback_safe_dump(item, indent + 2, sort_keys=sort_keys))
            else:
                lines.append("{}- {}".format(prefix, _dump_scalar(item)))
        return "\n".join(lines)
    return "{}{}".format(prefix, _dump_scalar(obj))


def _normalize_load_input(src):
    if hasattr(src, "read"):
        return src.read()
    if isinstance(src, (bytes, bytearray)):
        return src.decode("utf-8", errors="replace")
    return src


def safe_load(text):
    payload = _normalize_load_input(text)
    if _yaml is not None:
        return _yaml.safe_load(payload)
    return _fallback_safe_load(payload)


def safe_dump(data, allow_unicode=True, sort_keys=False, stream=None):
    if _yaml is not None:
        class _StringQuotingSafeDumper(_yaml.SafeDumper):
            pass

        def _represent_str(dumper, value):
            style = "'" if _needs_quoted_string(value) else None
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

        _StringQuotingSafeDumper.add_representer(str, _represent_str)
        try:
            return _yaml.dump(
                data,
                stream=stream,
                allow_unicode=allow_unicode,
                sort_keys=sort_keys,
                Dumper=_StringQuotingSafeDumper,
            )
        except TypeError:
            return _yaml.dump(
                data,
                stream=stream,
                allow_unicode=allow_unicode,
                Dumper=_StringQuotingSafeDumper,
            )
    dumped = _fallback_safe_dump(data, indent=0, sort_keys=sort_keys)
    dumped = dumped + ("\n" if dumped and not dumped.endswith("\n") else "")
    if stream is not None:
        stream.write(dumped)
        return None
    return dumped
