# -*- coding: utf-8 -*-
import glob
from pathlib import Path

import pytest


_REPO_DIR = Path(__file__).resolve().parent
_DEFAULT_ARTIFACTS_DIR = _REPO_DIR.parent / "artifacts"
_DEFAULT_CASE_PATTERNS = ("*.yaml", "*.yml")


def pytest_addoption(parser):
    group = parser.getgroup("chaos-runner")
    group.addoption(
        "--case",
        action="append",
        default=[],
        help="case yaml path, directory, or glob pattern; can be specified multiple times",
    )
    group.addoption(
        "--case-dir",
        action="append",
        default=[],
        help="directory containing case yaml files; recursively collects *.yaml and *.yml; can be specified multiple times",
    )
    group.addoption(
        "--runner-config-file",
        action="store",
        default="",
        help="optional runner config file passed through to chaos_runner",
    )
    group.addoption(
        "--dry-run",
        action="store_true",
        default=False,
        help="only render workflow yaml and collect observer output; do not apply workflow",
    )
    group.addoption(
        "--runner-out-dir",
        action="store",
        default="",
        help="optional directory for rendered workflow yaml files",
    )
    group.addoption(
        "--runner-log-dir",
        action="store",
        default="",
        help="optional directory for case logs; defaults to the system temp directory",
    )


def _expand_case_input(raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return []

    candidate = Path(text)
    if candidate.is_file():
        return [candidate]
    if candidate.is_dir():
        return _collect_case_files(candidate)

    return [Path(item) for item in sorted(glob.glob(text, recursive=True))]


def _collect_case_files(directory):
    results = []
    for pattern in _DEFAULT_CASE_PATTERNS:
        results.extend(directory.rglob(pattern))
    return sorted(results)


def _resolve_case_paths(config):
    seen = set()
    resolved = []
    for raw_value in config.getoption("case"):
        for path in _expand_case_input(raw_value):
            normalized = path.resolve()
            key = str(normalized)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(normalized)
    for raw_value in config.getoption("case_dir"):
        text = str(raw_value or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if not candidate.exists():
            raise pytest.UsageError("--case-dir does not exist: {}".format(text))
        if not candidate.is_dir():
            raise pytest.UsageError("--case-dir requires a directory: {}".format(text))
        for path in _collect_case_files(candidate):
            normalized = path.resolve()
            key = str(normalized)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(normalized)
    return resolved


def _case_id(path):
    try:
        return path.resolve().relative_to(_REPO_DIR).as_posix()
    except ValueError:
        return path.name


def pytest_generate_tests(metafunc):
    if "case_path" not in metafunc.fixturenames:
        return

    case_paths = _resolve_case_paths(metafunc.config)
    if not case_paths:
        metafunc.parametrize("case_path", [None], ids=["no-case-selected"])
        return

    ids = [_case_id(path) for path in case_paths]
    metafunc.parametrize("case_path", case_paths, ids=ids)


@pytest.fixture
def runner_config_file(pytestconfig):
    return str(pytestconfig.getoption("runner_config_file") or "")


@pytest.fixture
def runner_dry_run(pytestconfig):
    return bool(pytestconfig.getoption("dry_run"))


@pytest.fixture
def runner_out_dir(pytestconfig):
    value = str(pytestconfig.getoption("runner_out_dir") or "").strip()
    if not value:
        return ""
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


@pytest.fixture
def runner_log_dir(pytestconfig):
    value = str(pytestconfig.getoption("runner_log_dir") or "").strip()
    path = Path(value) if value else (_DEFAULT_ARTIFACTS_DIR / "logs")
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())
