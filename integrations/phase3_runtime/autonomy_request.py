"""Audit/meter the existing DeepSeek client at each real HTTP request boundary.

The instance-local client wrapper is active only in the current autonomous Agent
ContextVar. Passive callers retain their original path. No model/provider/client
is created, and no shared provider retry or sampling configuration is modified.
"""
import asyncio
from contextvars import ContextVar
import inspect
import time


class OpportunityStopped(RuntimeError):
    """Reason codes only: native exception logs must not disclose content."""


_ACTIVE = ContextVar('phase3_autonomy_actual_request', default=None)


def json_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if callable(getattr(value, 'model_dump', None)):
        return json_value(value.model_dump(mode='json'))
    if callable(getattr(value, 'to_dict', None)):
        return json_value(value.to_dict())
    return {'unserialized_type': type(value).__name__}


async def _admit(admit):
    reason=admit()
    if inspect.isawaitable(reason):reason=await reason
    if reason!='allowed':raise OpportunityStopped(str(reason))


async def monitor(awaitable, admit, *, poll=.25):
    task=asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            await _admit(admit)
            await asyncio.wait((task,),timeout=poll)
        result=task.result()
        # A user message/night boundary may arrive in the completion turn.
        await _admit(admit)
        return result
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)


def _usage(raw):
    value=getattr(raw,'usage',None)
    if value is None:return {}
    if isinstance(value,dict):get=lambda key,default=None:value.get(key,default)
    else:get=lambda key,default=None:getattr(value,key,default)
    result={}
    for key in ('prompt_tokens','completion_tokens','total_tokens',
                'prompt_cache_hit_tokens','prompt_cache_miss_tokens'):
        item=get(key)
        if type(item) is int and item>=0:result[key]=item
    details=get('completion_tokens_details')
    reasoning=details.get('reasoning_tokens') if isinstance(details,dict) else getattr(details,'reasoning_tokens',None)
    if type(reasoning) is int and reasoning>=0:result['reasoning_tokens']=reasoning
    details=get('prompt_tokens_details')
    cached=details.get('cached_tokens') if isinstance(details,dict) else getattr(details,'cached_tokens',None)
    if type(cached) is int and cached>=0:result['cached_tokens']=cached
    return result


def _visible_output(raw):
    choices=getattr(raw,'choices',None) or []
    result=[]
    for choice in choices:
        message=getattr(choice,'message',None)
        result.append({'content':getattr(message,'content',None) or '',
                       'tool_calls':json_value(getattr(message,'tool_calls',None) or []),
                       'finish_reason':getattr(choice,'finish_reason',None)})
    return {'choices':result}


def _write_audit(state,evaluation_id,kind,payload):
    result=state.write_audit(evaluation_id,kind,payload)
    if not isinstance(result,dict) or result.get('complete') is not True:
        raise OpportunityStopped('private_audit_unavailable')
    return result


def _install_client_boundary(provider):
    client=getattr(provider,'client',None)
    completions=getattr(getattr(client,'chat',None),'completions',None)
    create=getattr(completions,'create',None)
    if not callable(create):raise OpportunityStopped('pinned_openai_client_boundary_required')
    # SDK retries happen below create() and cannot be individually counted here.
    # Fixed production OpenAI provider already constructs max_retries=0.
    if getattr(client,'max_retries',None)!=0:
        raise OpportunityStopped('sdk_retries_must_be_zero')
    if (getattr(completions,'_phase3_autonomy_metered',False)
            and getattr(completions,'_phase3_autonomy_context',None) is _ACTIVE):return
    # Plugin reload can replace the module/ContextVar while the same configured
    # provider survives. Rebind its original method, never stack wrappers or
    # silently leave the new context outside the old wrapper's meter.
    original=getattr(completions,'_phase3_autonomy_original_create',create)

    async def metered(*args,**kwargs):
        scope=_ACTIVE.get()
        if scope is None:return await original(*args,**kwargs)
        state,evaluation_id,admit,expected_provider=scope
        if client is not getattr(expected_provider,'client',None):
            raise OpportunityStopped('unexpected_autonomy_provider')
        await _admit(admit)
        if kwargs.get('stream') is not False:
            raise OpportunityStopped('autonomy_nonstream_request_required')
        charge=state.reserve_model_request(evaluation_id)
        if not charge['allowed']:raise OpportunityStopped(charge['reason'])
        call_id=charge['id'];started=time.monotonic();status='failed';usage={};metadata={'io_started':False}
        try:
            payload={'request_id':call_id,'model':kwargs.get('model'),
                     'messages':json_value(kwargs.get('messages',[])),
                     'tools':json_value(kwargs.get('tools',[])),
                     'parameters':{k:json_value(v) for k,v in kwargs.items()
                                   if k not in {'messages','tools','extra_headers','api_key'}},
                     'stream':False,'sdk_retries':0,'scope':'actual_openai_create'}
            _write_audit(state,evaluation_id,'request',payload)
            await _admit(admit)
            # The very same configured client issues the actual HTTP request.
            metadata['io_started']=True
            raw=await monitor(original(*args,**kwargs),admit)
            usage=_usage(raw)
            visible=_visible_output(raw)
            _write_audit(state,evaluation_id,'output',{'request_id':call_id,**visible,'usage':usage,
                'model':getattr(raw,'model',None)})
            status='returned' if any(c['content'] or c['tool_calls'] for c in visible['choices']) else 'empty'
            return raw
        except asyncio.CancelledError:
            status='cancelled';raise
        except OpportunityStopped as error:
            status='failed' if str(error)=='private_audit_unavailable' else 'cancelled'
            metadata['stop_reason']=str(error);raise
        except TimeoutError:
            status='timeout';raise OpportunityStopped('provider_timeout') from None
        except Exception as error:
            # Avoid upstream logging arbitrary HTTP bodies/credentials, and do
            # not let its context/key recovery retry unaccounted requests.
            metadata['error_type']=type(error).__name__
            code=getattr(error,'status_code',None)
            if type(code) is int:metadata['status_code']=code
            raise OpportunityStopped('provider_or_audit_failed') from None
        finally:
            state.finish_call(call_id,status,usage=usage,
                              elapsed=time.monotonic()-started,metadata=metadata)
    completions.create=metered
    completions._phase3_autonomy_metered=True
    completions._phase3_autonomy_context=_ACTIVE
    completions._phase3_autonomy_original_create=original


def install_runner(runner, *, state, evaluation_id, admit, interest=None, revision=None, finalize_revision=None):
    """Scope the native Agent to one approved provider and meter actual requests."""
    if getattr(runner,'_phase3_autonomy_metered',False):return
    provider=runner.provider
    _install_client_boundary(provider)
    original=runner._iter_llm_responses
    # AstrBot calls this value max_retries, but its fixed provider interprets it
    # as total attempts. One attempt means zero transport retries.
    runner.request_max_retries=1
    runner.fallback_providers=[]
    runner.streaming=False

    async def responses(*args,**kwargs):
        await _admit(admit)
        if runner.provider is not provider:raise OpportunityStopped('unexpected_autonomy_provider')
        iterator=None
        try:
            iterator=original(*args,**kwargs).__aiter__()
            while True:
                token=_ACTIVE.set((state,evaluation_id,admit,provider))
                try:
                    try:response=await monitor(anext(iterator),admit)
                    except StopAsyncIteration:break
                finally:_ACTIVE.reset(token)
                body=str(getattr(response,'completion_text','') or '')
                if body.startswith('[PHASE3_INTEREST]\n'):
                    visible=body.split('\n',1)[1].strip();result='not_sent'
                    await _admit(admit)
                    if interest is not None and visible and getattr(response,'tools_call_name',[]):
                        result=await interest(visible)
                    if not isinstance(result,str):raise OpportunityStopped('invalid_interest_delivery_receipt')
                    response.completion_text=('兴趣表达发送状态：'+result+'。'+
                        (visible if result=='sent' else '保持静默探索，不要声称已经联系对方。'))
                if revision is not None and not getattr(response, 'tools_call_name', []):
                    instruction = revision(body)
                    if inspect.isawaitable(instruction):
                        instruction = await instruction
                    if instruction:
                        # Keep the SAME native runner, provider, original tools
                        # and protocol-complete messages. This is an internal
                        # revision, never an inbound event or confirmed history.
                        from astrbot.core.agent.message import Message
                        runner.run_context.messages.extend([
                            Message(role='assistant', content=body),
                            Message(role='user', content=instruction)])
                        close = getattr(iterator, 'aclose', None)
                        if callable(close):
                            await close()
                        iterator = original(*args, **kwargs).__aiter__()
                        continue
                    if finalize_revision is not None:
                        response.completion_text = finalize_revision(body)
                yield response
        finally:
            close=getattr(iterator,'aclose',None)
            if callable(close):await close()
    runner._iter_llm_responses=responses
    runner._phase3_autonomy_metered=True
