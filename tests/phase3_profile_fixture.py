"""Synthetic approved background; no owner interview values."""
from integrations.phase3_memory.profile_release import identity, digest

GAMES = ('星露谷物语', '戴森球计划', '传送门2')
OLD_FAILURE_MARKER = 'SYNTHETIC_OLD_PROFILE_FAILURE'


def failed_tool_history():
    """Sixteen old rows, including valid tool pairs and incorrect prior denials.

    These are test-only native conversation inputs, not owner facts or a reason
    to discard real history. Current facts must come from fresh native context.
    """
    rows = []
    for index in range(4):
        tag = f'{OLD_FAILURE_MARKER}_{index}'
        tool_id = f'synthetic-old-profile-tool-{index}'
        rows.extend([
            {'role': 'user', 'content': tag + '：此前询问过我的游戏资料。'},
            {'role': 'assistant', 'content': tag + '：先核对旧资料。',
             'tool_calls': [{'id': tool_id, 'type': 'function', 'function': {
                 'name': 'memory_companion_navigate',
                 'arguments': '{"action":"search","query":"游戏","limit":3}'}}]},
            {'role': 'tool', 'tool_call_id': tool_id,
             'content': '{"status":"not_found","evidence":[],"fixture":"' + tag + '"}'},
            {'role': 'assistant', 'content': tag + '：旧轮没有查到具体游戏资料。'},
        ])
    return rows


def manifest(ctx):
    topics = [
        ('games', '用户自述（游戏兴趣）：最喜欢星露谷物语；也玩戴森球计划和传送门2。喜欢探索和解谜。'),
        ('reading', '用户自述（阅读）：喜欢科幻，最近读完《海底两万里》。'),
        ('food', '用户自述（饮食）：喜欢南瓜汤，不喜欢芹菜。'),
        ('chat', '用户自述（交流）：希望回答根据资料，不要虚构游戏经历，也不要反复询问资料。'),
    ]
    return {'schema':'qq-companion.profile-reviewed.v1','reviewed':True,'batch_id':'synthetic-recall',
        'identity':identity(ctx),'source_document_sha256':'b'*64,'entries':[
            {'id':name,'layer':'L2','text':text,'permission_scope':name,
             'confidence_status':'user_confirmed','shared_experience':False,
             'source_refs':['synthetic-profile:'+name]} for name,text in topics]}


def approval(m):
    return {'approved':True,'environment':'isolated','batch_id':m['batch_id'],
        'manifest_hash':digest(m),'identity':m['identity'],
        'grants':{e['permission_scope']:{'write_memory':True,'model_use':True,
            'native_proactive_recall':True} for e in m['entries']}}
