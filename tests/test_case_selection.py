# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

import conftest


class _ConfigStub(object):
    def __init__(self, case=None, case_dir=None):
        self._options = {
            "case": case or [],
            "case_dir": case_dir or [],
        }

    def getoption(self, name):
        return self._options[name]


def test_expand_case_input_directory_collects_yaml_and_yml(tmp_path):
    root = tmp_path / "cases"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()

    yaml_path = root / "alpha.yaml"
    yml_path = nested / "beta.yml"
    ignored = nested / "notes.txt"

    yaml_path.write_text("name: alpha\n", encoding="utf-8")
    yml_path.write_text("name: beta\n", encoding="utf-8")
    ignored.write_text("skip\n", encoding="utf-8")

    result = conftest._expand_case_input(str(root))

    assert result == [yaml_path, yml_path]


def test_resolve_case_paths_merges_case_and_case_dir_without_duplicates(tmp_path):
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    case_a = case_dir / "one.yaml"
    case_b = case_dir / "two.yml"
    case_a.write_text("name: one\n", encoding="utf-8")
    case_b.write_text("name: two\n", encoding="utf-8")

    config = _ConfigStub(case=[str(case_a)], case_dir=[str(case_dir)])

    result = conftest._resolve_case_paths(config)

    assert result == [case_a.resolve(), case_b.resolve()]


def test_resolve_case_paths_rejects_non_directory_case_dir(tmp_path):
    file_path = tmp_path / "case.yaml"
    file_path.write_text("name: single\n", encoding="utf-8")
    config = _ConfigStub(case_dir=[str(file_path)])

    with pytest.raises(pytest.UsageError, match="requires a directory"):
        conftest._resolve_case_paths(config)
