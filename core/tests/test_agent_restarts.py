"""Restart only failed model requests; keep token limits and cancellation terminal."""
import asyncio
import httpx
import pytest
from app.services import provider_client, provider_errors, project_agent, tool_chat


@pytest.fixture(autouse=True)
def immediate_retries(monkeypatch):
    monkeypatch.setattr(provider_errors, 'retry_delay', lambda exc, attempt: 0)


@pytest.mark.asyncio
async def test_five_restarts_then_success(monkeypatch):
    calls, notices = [], []
    async def complete(*args):
        calls.append(1)
        if len(calls) <= 5:
            raise ValueError('Malformed provider response')
        return 'complete result'
    async def notice(info):
        notices.append(info)
    monkeypatch.setattr(provider_client, 'complete', complete)
    assert await provider_client.complete_with_retry(None, None, 'model', [], on_retry=notice) == 'complete result'
    assert len(calls) == 6
    assert [item['attempt'] for item in notices] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_six_failures_exhaust_budget():
    calls = 0
    failure = RuntimeError('still broken')
    async def request():
        nonlocal calls
        calls += 1
        raise failure
    with pytest.raises(RuntimeError) as raised:
        await provider_errors.retry_request(request)
    assert raised.value is failure
    assert calls == 6


@pytest.mark.parametrize('failure', [
    provider_errors.OutputLimitError('truncated', has_calls=True),
    provider_errors.CapabilityError('context', capability='context', value=1000),
    provider_errors.CapabilityError('output', capability='max_output', value=1000),
    RuntimeError('maximum context length is 8192 tokens'),
    RuntimeError('token_limit_exceeded'),
    RuntimeError('tokens per minute limit reached'),
    RuntimeError('Превышен лимит токенов'),
    httpx.HTTPStatusError('Bad Request', request=httpx.Request('POST', 'https://example.test'),
        response=httpx.Response(400, text='max_tokens exceeds maximum 4096')),
])
@pytest.mark.asyncio
async def test_token_limits_never_restart(failure):
    calls = 0
    async def request():
        nonlocal calls
        calls += 1
        raise failure
    with pytest.raises(type(failure)):
        await provider_errors.retry_request(request)
    assert calls == 1


@pytest.mark.asyncio
async def test_cancellation_does_not_restart():
    calls = 0
    async def request():
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await provider_errors.retry_request(request)
    assert calls == 1


@pytest.mark.asyncio
async def test_stream_restart_retracts_failed_text(monkeypatch):
    calls = 0
    async def stream(*args):
        nonlocal calls
        calls += 1
        yield 'content', 'partial' if calls == 1 else 'final'
        if calls == 1:
            raise RuntimeError('protocol error')
    monkeypatch.setattr(provider_client, 'stream_chat', stream)
    events = [item async for item in provider_client.stream_chat_with_retry(None, None, 'model', [])]
    assert events[1] == ('retract', 7)
    assert events[2][0] == 'retry'
    assert events[-1] == ('content', 'final')
    assert calls == 2


@pytest.mark.asyncio
async def test_tool_turn_protocol_failures_restart_at_request(monkeypatch):
    calls = 0
    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 5:
            yield 'content', 'partial'
            raise ValueError('Invalid response')
        yield 'content', 'ready'
        yield 'tool_calls', []
    monkeypatch.setattr(tool_chat, 'stream_turn', stream)
    events = [item async for item in project_agent._turn(None, None, 'model', [], [], {})]
    assert calls == 6
    assert sum(kind == 'retract' for kind, _ in events) == 5
    assert [value['attempt'] for kind, value in events if kind == 'retry'] == [1, 2, 3, 4, 5]
    assert events[-1][0] == 'done'
