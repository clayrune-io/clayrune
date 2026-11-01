import json
import subprocess
import re
from pathlib import Path

import pytest
from jinja2 import Environment

from mc import mcp_bridge_goose as bridge


def test_extension_transport_and_fields():
    selected = bridge.translate({'local': {'command': 'python', 'args': ['a b'],
                                           'env': {'KEY': 'value'}, 'cwd': '/work'},
                                 'web': {'type': 'http', 'url': 'https://example/mcp',
                                         'headers': {'X-Test': 'value'}}})
    assert selected[0]['cmd'] == 'python' and selected[0]['type'] == 'stdio'
    assert selected[0]['envs'] == {'KEY': 'value'} and selected[0]['cwd'] == '/work'
    assert selected[1]['type'] == 'streamable_http' and selected[1]['uri'] == 'https://example/mcp'
    assert selected[1]['headers'] == {'X-Test': 'value'}
    with pytest.raises(ValueError, match='SSE'):
        bridge.translate({'web': {'type': 'sse', 'url': 'https://example/sse'}})


@pytest.mark.parametrize('text', ['hello {{secret:name}}', '{% endraw %}{{ missing }}',
                                  '{{ x }} {% raw %} {% endraw %} {% if x %}'])
def test_recipe_keeps_task_context_and_template_syntax_literal(text):
    args = bridge.launch_args({}, text, 'system ' + text)
    recipe = json.loads(Path(args[1]).read_text())
    assert args[0] == '--recipe' and recipe['extensions'] == []
    assert Environment().from_string(recipe['prompt']).render() == text
    assert Environment().from_string(recipe['instructions']).render() == 'system ' + text
    assert '--no-profile' not in args


def test_native_recipe_validation_error_is_visible(monkeypatch):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 1))
    with pytest.raises(ValueError, match='recipe; launch refused'):
        bridge.launch_args({}, 'task', prefix=['goose'], env={}, cwd='.')


def test_whole_recipe_survives_goose_template_preprocessing():
    # Source: recipe/template_recipe.rs preprocess_template_variables and
    # uses_template_inheritance. No invalid variable can be nested in raw.
    text = '{{secret:name}} {% include "example.md" %} {% endraw %} {{ missing }}'
    args = bridge.launch_args({'fixture': {'command': 'fixture', 'args': [text],
                                          'env': {'KEY': text}}}, text, text)
    encoded = Path(args[1]).read_text()
    expressions = re.findall(r'\{\{(.*?)\}\}', encoded)
    assert all(expr.strip().startswith(("'", '"')) for expr in expressions)
    assert not re.search(r'\{%-?\s*(extends|include)', encoded)
    rendered = json.loads(Environment().from_string(encoded).render())
    assert rendered['prompt'] == text and rendered['instructions'] == text
    assert rendered['extensions'][0]['args'] == [text]
    assert rendered['extensions'][0]['envs'] == {'KEY': text}
