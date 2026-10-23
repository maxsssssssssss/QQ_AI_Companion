"""Entirely invented data for request-size A/B; no production payload input.

Counts and approximate lengths are shape parameters, not owner facts.  Game
names below are fictional, and expected answers come only from this fixture.
"""
from hashlib import sha256
import json

from integrations.phase3_memory.profile_release import identity, digest

COMMON_GAMES = ('雾港漫游', '星砂工坊', '纸舟解谜', '云原列车', '月湾农场')
EXPERIENCED_GAMES = ('铜塔探险', '琥珀棋局')
GAME_BODY = (
    '用户自述（游戏兴趣）：平时常玩的项目是雾港漫游、星砂工坊、纸舟解谜、云原列车和月湾农场。'
    '这五项来自本次合成资料中的常玩清单，不能把其他体验过的项目混进同一个关系。'
    '其中，雾港漫游主要用来探索不同地图；星砂工坊偏向建造与规划；纸舟解谜用于解空间谜题；'
    '云原列车适合安排线路；月湾农场用于轻松种植和整理小屋。'
    '用户也玩过铜塔探险和琥珀棋局，但没有把这两项列为平时常玩。'
    '在明确比较喜爱程度时，用户说最喜欢纸舟解谜，理由是通关后的思路很清楚。'
    '资料没有提供每个项目累计游玩时长，因而不能判断哪项玩得最久，也没有昨天或今天实际游玩的记录。'
    '空闲时通常更愿意选择能随时暂停的内容，和朋友一起体验则看双方当时是否方便。'
    '对长时间重复刷取并不热衷，但这不表示从未接触过此类玩法。'
    '这里记录的是已确认的兴趣关系，不是当前在线状态、最近一次游玩时间或完整生涯列表。'
    '独处时偏好探索和解谜，与朋友交流则愿意分享规划思路；是否安装或付费没有记录，不能据兴趣推断设备平台、账户信息与消费水平。'
    '后续如纠正某个关系，应保留原文出处并以新版本为准。'
)


def synthetic_ref(index):
    prefix = f'synthetic-shape:document-section-{index:02d}:'
    return (prefix + sha256(f'invented-source-{index}'.encode()).hexdigest() + '-reference')[:94]


def core_body(topic, lead):
    return f'用户自述（{topic}）：{lead}日常聊天喜欢先说清楚眼前的问题，再讨论可以采取的小步骤。' \
        '需要比较方案时，希望区分已经知道的情况、暂时没有记录的部分和待核对的猜测。' \
        '当问题只需要简短回答时，不希望额外增加长篇解释；涉及复杂过程时则愿意阅读有结构的说明。' \
        '讨论过去发生的事情时，允许引用已保存的来源，但不应把假设当作确定的经历。'


def manifest(ctx):
    rows = [('games', 'L2', GAME_BODY, [synthetic_ref(i) for i in range(33)]),
            ('core_style', 'L1', core_body('交流方式', '喜欢清楚、温和且不重复的表达。'), ['synthetic-core:style']),
            ('core_work', 'L1', core_body('做事习惯', '习惯把复杂任务拆成若干可检查的步骤。'), ['synthetic-core:work']),
            ('reading', 'L2', '用户自述（阅读）：合成参与者喜欢航海故事，也会看介绍植物的短文。', ['synthetic-background:reading']),
            ('food', 'L2', '用户自述（饮食）：合成参与者喜欢红豆汤，口味偏清淡。', ['synthetic-background:food'])]
    return {'schema': 'qq-companion.profile-reviewed.v1', 'reviewed': True,
        'batch_id': 'synthetic-shape-recall', 'identity': identity(ctx),
        'source_document_sha256': 'c' * 64,
        'entries': [{'id': name, 'layer': layer, 'text': text,
            'permission_scope': name, 'confidence_status': 'user_confirmed',
            'shared_experience': False, 'source_refs': refs} for name, layer, text, refs in rows]}


def approval(m):
    return {'approved': True, 'environment': 'isolated', 'batch_id': m['batch_id'],
        'manifest_hash': digest(m), 'identity': m['identity'],
        'grants': {row['permission_scope']: {'write_memory': True, 'model_use': True,
            'native_proactive_recall': True} for row in m['entries']}}


def unrelated_tool_body(index, target):
    # Valid, independently readable JSON; no tool result is missing its call.
    value = {'status': 'partial', 'coverage': 'partial', 'fixture': 'synthetic-shape',
        'evidence': [{'id': f'synthetic-unrelated-{index}', 'text': (
            '合成记录：参与者会整理书桌、观察天气、阅读航海故事和记录植物。'
            '周末偶尔制作纸模型，选材料时比较重量和是否容易保存。'
            '厨房里使用白色杯子，书架按主题分区，实验笔记会保留修订日期。'),
            'source_refs': [synthetic_ref(index + 40)]}], 'archive_excerpt': ''}
    base = len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
    filler = ('合成存档记录：整理目录时使用分区编号，观察植物时记录叶片颜色，'
              '阅读之后写下章节位置，制作纸模型时保留材料清单。')
    value['archive_excerpt'] = (filler * ((target - base) // len(filler) + 1))[:target - base]
    rendered = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    assert len(rendered) == target
    return rendered


def failed_tool_history():
    def user(n):
        return {'role': 'user', 'content': f'合成旧轮{n}：我平时玩哪些游戏？只根据已保存资料回答。'}
    def call(n, action='search'):
        return {'role': 'assistant', 'content': '我先核对一下已有资料。', 'tool_calls': [{
            'id': f'synthetic-shape-call-{n}', 'type': 'function', 'function': {
                'name': 'memory_companion_navigate',
                'arguments': json.dumps({'action': action, 'query': '游戏资料', 'limit': 3}, ensure_ascii=False)}}]}
    def result(n, length):
        return {'role': 'tool', 'tool_call_id': f'synthetic-shape-call-{n}', 'content': unrelated_tool_body(n, length)}
    def final(n):
        return {'role': 'assistant', 'content': f'合成旧轮{n}：这次没有核对到具体清单，不能把其他资料当成答案。'}
    # Exact role shape supplied as aggregate metadata: 16 old rows / 3 pairs.
    return [user(1), call(1), result(1, 4619), call(2, 'read'), result(2, 4710), final(1),
            user(2), call(3), result(3, 1883), final(2), user(3), final(3),
            user(4), final(4), user(5), final(5)]


async def seed_unrelated_candidates(service, ctx):
    """Use the same native store, solely inside the synthetic marked snapshot."""
    import importlib
    module = importlib.import_module(type(service).__module__.rsplit('.', 1)[0] + '.models')
    for index, topic in enumerate(('书桌整理', '航海阅读', '植物观察', '纸模型制作')):
        content = (f'合成日常资料：平时在{topic}方面，会先整理现有记录再安排下一步。'
            '资料中保留了日期、来源编号和修改缘由。讨论这些日常情况时，'
            '已确认的细节可以直接引用；尚未记录的部分会留待以后确认。')
        record = module.MemoryRecord(id=f'synthetic-shape-candidate-{index}',
            memory_type='conversation_summary', subject=module.EntityRef(kind='user', id=ctx.user_id, role='user'),
            object=module.EntityRef.bot_self(ctx.bot_id), scope='private', session_id=ctx.session_id,
            platform=ctx.platform, visibility='private_pair', sayability='direct',
            reality_level='real_user_fact', lifecycle='stable_memory', content=content, evidence=content,
            confidence=.95, importance=.8, owner_bot_id=ctx.bot_id, source_plugin='synthetic-shape-fixture',
            tags=['资料', '日常', topic], metadata={'persona_id': ctx.persona_id,
            'owner_bot_id': ctx.bot_id, 'source_refs': [f'synthetic-candidate:{index}']})
        await service.store.insert_memory(record)


def expected():
    return {'synthetic_only': True, 'question_relation': 'common_list',
        'common_games': list(COMMON_GAMES), 'experienced_only': list(EXPERIENCED_GAMES),
        'favorite': '纸舟解谜', 'longest': 'unknown', 'yesterday': 'unknown',
        'game_body_chars': len(GAME_BODY), 'source_count': 33,
        'source_lengths': sorted({len(synthetic_ref(i)) for i in range(33)}),
        'old_history_roles': [r['role'] for r in failed_tool_history()],
        'tool_body_chars': [len(r['content']) for r in failed_tool_history() if r['role'] == 'tool']}
