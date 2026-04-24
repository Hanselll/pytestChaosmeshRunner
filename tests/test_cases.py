# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from chaos_runner.runner import execute_case


def _build_out_path(case_path, runner_out_dir):
    if not runner_out_dir:
        return ""
    return str(Path(runner_out_dir) / "{}.rendered.yaml".format(case_path.stem))


def test_case_execution(case_path, runner_config_file, runner_dry_run, runner_out_dir, runner_log_dir):
    if case_path is None:
        pytest.skip("use --case to select one or more case yaml files for pytest execution")

    result = execute_case(
        case_path=str(case_path),
        config_file=runner_config_file,
        dry_run=runner_dry_run,
        out=_build_out_path(case_path, runner_out_dir),
        log_dir=runner_log_dir,
        echo_stdout=True,
    )

    assert result["case_name"]
    assert result["workflow_name"]
    assert Path(result["workflow_yaml_path"]).exists()
    assert Path(result["case_log_format_path"]).exists()
    assert Path(result["case_log_json_path"]).exists()
    assert Path(result["case_log_json_fixed_path"]).exists()
