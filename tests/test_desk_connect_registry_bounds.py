"""Large npm version lists have their own bounds; page/config evidence does not."""
import json

import pytest

from mc.desk_connect import custom_npm_registry as registry
from mc.desk_connect import parameter_parsers as parsers
from mc.desk_connect.mcp_errors import ActivationError


def packument():
    return {'name': 'zod', 'versions': {
        f'3.0.{i}': {'dist': {'integrity': 'sha512-fixture', 'tarball': 'https://registry.npmjs.org/zod/-/zod.tgz'}}
        for i in range(6000)}, 'dist-tags': {'latest': '3.0.5999'}}


def test_large_version_list_is_read_without_raising_page_input_limits():
    raw = json.dumps(packument()).encode()
    assert parsers.load_json(raw, allow_dupes=True) is None
    result = registry.fetch('zod', fetch_fn=lambda *a, **kw: raw)
    assert len(result['versions']) == 6000
    assert registry.pick(result, '^3.0.0') == '3.0.5999'
    assert parsers.load_json(raw, allow_dupes=True) is None


@pytest.mark.parametrize('raw', [b'[]', b'{', b'[' * 42 + b'0' + b']' * 42,
    json.dumps({'name': 'zod', 'padding': [0] * registry.MAX_DOCUMENT_NODES}).encode(),
    b' ' * (registry.MAX_DOCUMENT + 1)], ids=['array', 'malformed', 'depth', 'nodes', 'bytes'])
def test_registry_documents_still_have_shape_depth_node_and_byte_limits(raw):
    with pytest.raises(ActivationError, match='could not be read'):
        registry.fetch('zod', fetch_fn=lambda *a, **kw: raw)
