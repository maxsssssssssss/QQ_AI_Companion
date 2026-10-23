"""A tool-use contract, with no profile values, retrieval engine or extra model."""
import re

MARKER = '[PHASE3_MEMORY_RECALL_V1]'
GUIDANCE = '''[PHASE3_MEMORY_RECALL_V1]
个人资料与记忆核对：自动注入只是有限候选，不是完整档案。当前问题有足够证据就直接回答。
用户问自己的资料、兴趣、习惯等，而当前证据不足时，先用本轮可用的 memory_companion_navigate，action="search"，提炼简短的主题 query/cue 查询，包括经用户批准导入的资料。有主题目录时优先用相关主题的原词作为 query，再从实际结果中作答；目录仅指示可查的主题，不是答案。查询词只表达所问主题，不要把称呼、“只根据记忆”“不要上网”等回答要求拼进查询。
memory_companion_sources 查的是原始聊天，直接导入的资料未必在聊天原文中；只查到追问或查不到原文，不能断言资料不存在。不要复用上一轮“没找到”作为本轮事实。需要核对原话时才继续查 sources，共用原生步数上限，证据足够立即停止。
只使用实际返回且归属正确的证据，区分过去与现在、自述与推测；部分命中不要说成完整清单。检索不可用、失败或仍不足时自然说明缺口，不猜答案，也不要把已提交资料的重填工作交回用户。私人资料只在本地记忆工具中查询，不发往公开搜索。普通寒暄和当前信息已足够的聊天不额外调用工具。'''


async def available_topics(service, ctx, event):
    """Expose only headings of currently visible approved native records.

    Native search applies scope, lifecycle and revision controls. This is a
    bounded topic directory, not a second facts store or an answer generator.
    """
    gate = await service._p5_gate(event=event, sink='memory_recall')
    if not gate.get('ok'):
        return []
    rows = await service.search('', ctx, top_k=64)
    topics = set()
    for row in rows:
        m = row.memory
        meta = m.metadata or {}
        if (m.source_plugin != 'phase3_profile_release'
                or meta.get('profile_layer') not in {'L1', 'L2'}
                or meta.get('confidence_status') != 'user_confirmed'):
            continue
        match = re.match(r'^用户自述（([^\n（）<>]{1,30})）：', m.content)
        if match:
            topics.add(match[1])
    return sorted(topics)[:16]


def append_guidance(req, topics=()):
    """Respect the host's final tool availability; idempotent within one run."""
    tools = getattr(getattr(req, 'func_tool', None), 'tools', ()) or ()
    if not any(getattr(t, 'name', '') == 'memory_companion_navigate'
               and getattr(t, 'active', True) for t in tools):
        return False
    prompt = req.system_prompt or ''
    if MARKER not in prompt:
        directory = ('\n本轮可检索的部分已确认资料主题：' + '；'.join(topics)) if topics else ''
        req.system_prompt = prompt + '\n\n' + GUIDANCE + directory
    return True
