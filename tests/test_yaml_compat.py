# -*- coding: utf-8 -*-
from chaos_runner import yaml_compat as yaml


def test_safe_dump_quotes_numeric_like_strings_for_roundtrip():
    text = yaml.safe_dump(
        {
            "loss": "10",
            "correlation": "0",
            "deadline": "42s",
            "plain": "abc",
        },
        sort_keys=False,
    )

    data = yaml.safe_load(text)

    assert data["loss"] == "10"
    assert data["correlation"] == "0"
    assert data["deadline"] == "42s"
    assert data["plain"] == "abc"
    assert isinstance(data["loss"], str)
    assert isinstance(data["correlation"], str)
