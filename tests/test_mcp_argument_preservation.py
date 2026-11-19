from mc.mcp import normalize_config


def test_explicit_arguments_preserve_empty_and_whitespace_values():
    config = normalize_config("stdio", {"command": "example", "args": ["--empty", "", " "]})

    assert config["args"] == ["--empty", "", " "]
