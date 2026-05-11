import asyncio
import pytest
from nodes_reflection_node import ReflectionNode


@pytest.mark.asyncio
async def test_reflection_detects_missing_file(tmp_path):
    node = ReflectionNode('reflection')
    state = {'inputs': {'expectations': {'files_created': [str(tmp_path / 'nope.txt')] }}, 'history': {}}
    res = await node.execute(state)
    rep = res.get('reflection')
    assert rep['verdict'] in ('retry', 'failed')
    assert 'files_created' in rep['checks']
