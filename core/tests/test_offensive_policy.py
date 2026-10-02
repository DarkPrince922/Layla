"""The UI switch is a server-side gate, including previously planned steps."""
import pytest

from tests.test_agent_api import _setup


@pytest.mark.asyncio
async def test_default_and_authorization_requirement(client):
    await client.post('/api/auth/register', json={'email': 'policy@example.com', 'password': 'hunter2hunter2'})
    e = (await client.post('/api/engagements', json={'target': 'example.com'})).json()
    assert e['offensive_enabled'] is False
    assert (await client.put(f"/api/engagements/{e['id']}/offensive", json={'enabled': True})).status_code == 400


@pytest.mark.asyncio
async def test_switch_blocks_already_planned_step(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    run = (await client.post(f'/api/engagements/{eid}/agent/runs', json={'task': 'check'})).json()
    step = next(s for s in run['steps'] if s['command'] and 'example.com' in s['command'])
    assert (await client.put(f'/api/engagements/{eid}/offensive', json={'enabled': False})).status_code == 200
    result = (await client.post(f"/api/agent/steps/{step['id']}/approve")).json()
    assert result['status'] == 'blocked'
    assert 'выключены' in result['output']
    await client.put(f'/api/engagements/{eid}/offensive', json={'enabled': True})
    result = (await client.post(f"/api/agent/steps/{step['id']}/approve")).json()
    assert 'исполнитель' in result['output'].lower()


@pytest.mark.asyncio
async def test_scope_and_authorization_changes_revoke_permission(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    assert (await client.post(f'/api/engagements/{eid}/authorize?authorized=false')).json()['offensive_enabled'] is False
    await client.post(f'/api/engagements/{eid}/authorize')
    await client.put(f'/api/engagements/{eid}/offensive', json={'enabled': True})
    e = (await client.put(f'/api/engagements/{eid}/scope', json={'allow': ['example.com'], 'deny': []})).json()
    assert e['offensive_enabled'] is False


@pytest.mark.asyncio
async def test_another_account_cannot_enable_actions(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    await client.post('/api/auth/register', json={'email': 'other-policy@example.com', 'password': 'hunter2hunter2'})
    assert (await client.put(f'/api/engagements/{eid}/offensive', json={'enabled': True})).status_code == 404
