import json

from mc.mcp_installer import extract_config


def test_preview_skips_malformed_server_entries(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"broken": "text", "valid": {"command": "example"}}}),
        encoding="utf-8",
    )

    preview = extract_config(str(tmp_path), allow_claude_fallback=False)

    assert preview["servers"] == {"valid": {"command": "example"}}
    assert preview["name_hint"] == "valid"
