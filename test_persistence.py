import asyncio
import pytest
from agent_persistence import Persistence


@pytest.mark.asyncio
async def test_persistence_kv(tmp_path):
    p = Persistence(db_path=str(tmp_path / 'test.db'))
    # ensure singleton override for test instance
    Persistence._instance = p
    await p._ensure()
    await p.save_kv('k1', {'a':1})
    v = await p.get_kv('k1')
    assert v == {'a':1}
    await p.delete_kv('k1')
    v2 = await p.get_kv('k1')
    assert v2 is None
