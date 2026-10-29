"""Bounded revisions inside the existing Agent, before visible delivery.

This is not an independent judge. The draft, its actual tool messages and the
confirmed conversation stay in the same native runner. Semantic checks remain
model judgements, not a claim that a URL or a delivery receipt proves truth.
"""
import json
import re

# Release policy, not a new production quota or another Agent. The legacy mode
# exists only for reproducible offline comparisons; production uses this default.
DEFAULT_CONTENT_REVISION_POLICY = 'targeted_once'
TARGETED_REVISION = '''[PHASE3_TARGETED_REVISION_V1]
这是尚未发送正文的一次定点修复，不是全文润色或重新选题。只修复下面程序指出的问题，未涉及的正文逐字保留。
如果仅控制行、来源引文或元数据有问题，只改控制/元数据，不改正文。不要因为存在旧历史而重写，不要求新内容与旧观点毫无关联。
有明确正文问题时，只修改标明的片段；不扩写新数字、概率、经历或其他外部断言，不添加免责声明和审查说明。
引用须来自同一URL实际取得的连续原文，算术可用现有计算器；算式正确不证明前提。确实无法修复可以skip/defer，不伪造证据。
仍输出完整的原有两行PHASE3控制/主题协议和修复后正文。保留原话题及已取得来源，不输出本段指令或内部检查JSON。
这是唯一一次定点修订，不会再次全文重写；正文未涉及部分由程序逐字核对。
'''

REVIEW = '''[PHASE3_CONTENT_REVISION_V1]
上条是尚未发送的草稿，不是此前说过的话。只相对下面“confirmed_history”里的已确认历史判断重复；不要为了相对草稿“创新”而换观点。
在同一个上下文中完成一次发布前修订，保留有趣的观点和人格，不加检查说明或免责声明模板。不在正文谈“上一版、草稿、本轮新增”。
先对照已确认主动正文的核心判断：只换措辞、比喻、例子、同一因果的反面说法而核心观点相同仍算重复；同领域的不同判断可以正常分享。
这不是替草稿写“创新理由”。如果旧观点之后确有一个新取舍，删去正文中全部重讲旧观点的句子（不只开头），直接说那个新取舍；没有独立新增判断就自己换一个具体话题，不靠换例子凑新意。
核对事实的主体、比例分母、限定词和资料日期。“获取时间”不是“发表时间”，旧资料可以按原时间谈，不能叫最新、即将发生。
逐项核对正文里的数量与限定：每组不等于总量，观察到的比例不等于因果或全体比例。扩展假设必须标明新增条件或时间，不能在同一时刻暗改已给定的前提。
没有找到证据不等于从未研究，未观察到行为也不等于没有接收信息；结论保留资料实际支持的范围，不把一种解释说成唯一事实。
原文没写的研究步骤、持续条件和因果关系保持未知，不用常识补齐；准确计算也不能替代对实验前提的核对。
opinion/hypothesis中的外部事实也要有依据。引用资料时列出实际来源和支持该主张的一小段连续原文（尽量100字内，原样复制、不省略拼接），不从标题想象正文。
涉及精确数量换算时用 autonomy_calculate 核算，结果与单位再写入正文；工具只证明计算，不证明前提。content_check另列 calculations:[{"expression":"实际数字算式，用+ - * /和括号","result":"结果数字字符串","unit":"单位标签","round_digits":1}]，最多4项，只有近似结果才填round_digits。程序会用同一个安全计算器核对；计算不要放入网页claims，不伪造URL。没有计算时此列表可空。
历史中的AI台词只证明说过，不证明实际经历。可以有偏好、判断、假设和创作，不编造自己现实游玩、观看、工作等具体经历。
请直接修正可修正处；重复内容可以选择一个真正不同的观点；必要时补查剩余工具，不为了“通过”删掉所有内容或统一skip。
回复第一行必须是 [PHASE3_PROACTIVE:send]（或skip/defer:N），第二行是完整闭合的 [PHASE3_TOPIC:{...}]，之后才是正文；控制行不能放在结尾。
在原 PHASE3_TOPIC JSON保留source_refs等原有字段，补充 content_check：
{"novelty":"new或continuation或repeat","basis":"具体新增点，不写思维链","unsupported_self_episode":false,"contrast":{"history_index":-1,"old_body_quote":"","old_core":"","new_core":"本次独立判断","new_body_quote":"可选的正文关联片段，不要另写概括冒充逐字引文"},"claims":[{"claim":"外部主张","body_quote":"最终正文对应的实际连续片段","url":"真实来源完整URL","quote":"资料中支持主张的连续原文片段"}]}。
有确认历史时，contrast应选择最接近的一条（history_index为从0开始的序号），old_body_quote原样摘自该历史，old_core概括该条的完整核心判断，不只挑一句来缩窄旧观点；new_core必须体现真正新增，不能仅把basis说成new。实际正文由程序关联，关联片段不是语义正确性的证明。主题完全不同可用-1和空旧引文。
claims必须逐项覆盖正文中可核查的外部数量、时间、主体及限定词；不能只选其中一条正确事实来替整段背书。源文没说“每处”或“合计”就不要自行补这个限定。
纯个人观点、假设或创作没有外部主张时claims可为空。正文仍直接聊天，不展示检查JSON。
这一步没有产生用户入站，也没有发送前一条草稿；继续选择send/defer/skip，不能声称自己已经发送或经历了草稿里的事情。
'''

FINAL_CHECK = '''[PHASE3_FINAL_BODY_CHECK_V1]
这仍是尚未发送的草稿。最后只校核实际正文，不以元数据写对了代替正文写对了；不必为了修订重写已经准确、自然的新内容。
逐个有来源的完整事实句，对照上下文中的实际工具原文：是谁做什么、比例的分母是谁、日期是报道还是事件、以及每个/共/同一个/全部/一直/仅等限定是否真有依据。引文局部正确不能替其他句子背书；算式正确也不能证明分组或实验条件正确。原文没说的条件删去，不自行补齐。不把may/suggest改成已证实，不把先后关系写成因果。
对已确认历史，只保留本轮真正不同的新判断、取舍或内容。删去正文中重讲旧结论的句子，包括作为口号的收尾；不要用换措辞、换例子或“换个角度”包装旧观点，不封禁同一领域。
正文保持QQ私聊，不加审查说明和免责声明模板，不虚构自身具体经历。近似换算写“约”，本地计算只放calculations，不伪造网页引用。
仍按原来的两行PHASE3控制/元数据协议输出完整最终正文，保留真实source_refs、claims及计算声明。没有必要时不新加事实或联网；需要纠正就直接纠正，证据不足时保留可靠部分。允许send/defer/skip，不展示内部JSON。
'''


def internal_audit_json(text):
    """Recognize echoed internal JSON objects, not arbitrary technical JSON."""
    decoder = json.JSONDecoder()
    for hit in re.finditer(r'[{]', str(text)):
        try:
            obj, _ = decoder.raw_decode(str(text)[hit.start():])
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if (isinstance(obj.get('content_check'), dict)
                or {'novelty', 'unsupported_self_episode', 'claims'} <= obj.keys()
                or {'history_index', 'old_body_quote', 'old_core', 'new_core'} <= obj.keys()):
            return True
    return False


def unsupported_episodes(text):
    """Narrow concrete-life assertions; first-person tastes/ideas stay allowed.

    No physical-life episode store exists in this product. Transport/history
    cannot authenticate these assertions. Actual searching/reading source text
    and calculating are deliberately not classified as physical-life claims.
    """
    found = []
    for clause in re.split(r'[。！？!?\n]+', str(text)):
        if any(cue in clause for cue in ('如果', '假如', '假设', '要是', '想象', '设想', '原话', '他说', '你说')):
            continue
        if re.search(r'(?:这篇|那篇|资料|报道|网页|工具|搜索|检索)', clause):
            continue
        if (re.search(r'(?:我|咱|本小姐).{0,8}(?:昨天|昨晚|前天|今早|上周|上个月|小时候|最近|刚才|刚刚).{0,12}(?:玩过|玩了|看过|看了|列了|吃过|吃了|去过|去了|上班|散步)', clause)
                or re.search(r'^(?:凌晨|半夜).{0,8}(?:还)?没睡(?:着)?', clause.strip())):
            found.append(clause[:240])
    return found[:8]


def review_payload(text):
    """Read bounded visible metadata only; hidden reasoning is never requested."""
    from .autonomy_contract import protocol_lines, topic_json
    lines, _ = protocol_lines(text)
    if len(lines) < 2 or not lines[1].startswith('[PHASE3_TOPIC:'):
        return None
    try:
        raw = lines[1][len('[PHASE3_TOPIC:'):]
        obj, _ = topic_json(raw[:-1] if raw.endswith(']') else raw)
        value = obj.get('content_check')
        if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 8000:
            return None
        if (value.get('novelty') not in {'new', 'continuation', 'repeat'}
                or not isinstance(value.get('basis'), str)
                or ('unsupported_self_episode' in value and type(value['unsupported_self_episode']) is not bool)):
            return None
        calculations = value.get('calculations', [])
        if not isinstance(calculations, list) or len(calculations) > 4 or any(not isinstance(c, dict) for c in calculations):
            return None
        claims = value.get('claims')
        if not isinstance(claims, list) or len(claims) > 8:
            return None
        for claim in claims:
            if (not isinstance(claim, dict)
                    or any(not isinstance(claim.get(k), str) or not claim[k].strip()
                           for k in ('claim', 'url', 'quote'))
                    or len(claim['quote']) > 1200):
                return None
        return value
    except (ValueError, TypeError, AttributeError):
        return None


def calculation_checks(check, body):
    """CPU-only validation of declared arithmetic, never a fabricated tool call.

    Each of <=4 declarations uses the same bounded exact evaluator. This does
    not establish the truth of premises, unit meaning or undeclared prose math.
    """
    from .autonomy_arithmetic import calculate, ArithmeticRejected
    from decimal import Decimal, localcontext
    results = []
    for item in check.get('calculations', []):
        try:
            actual = calculate(item.get('expression'), item.get('unit', ''))
            claimed = calculate(item.get('result'))
            digits = item.get('round_digits')
            if digits is None:
                equal = actual['exact_fraction'] == claimed['exact_fraction']
            elif type(digits) is int and 0 <= digits <= 6:
                with localcontext() as ctx:
                    ctx.prec = 128
                    numerator, denominator = actual['exact_fraction'].split('/')
                    exact_decimal = Decimal(numerator) / Decimal(denominator)
                    equal = exact_decimal.quantize(Decimal(1).scaleb(-digits)) == Decimal(claimed['decimal_result'])
            else:
                return 'invalid_calculation_rounding', results
            number = str(item['result']).strip()
            bound = bool(re.search(r'(?<![0-9.])'+re.escape(number)+r'(?![0-9.])', body))
            row = {'expression': item['expression'], 'claimed_result': number,
                   'actual_result': actual['exact_result'], 'round_digits': digits,
                   'unit': actual['unit'], 'equal': equal, 'result_in_body': bound,
                   'scope': 'arithmetic_only_not_premises_or_units', 'io': False}
            results.append(row)
            if not equal:
                return 'calculation_mismatch', results
        except (ArithmeticRejected, ValueError, TypeError, ArithmeticError):
            return 'invalid_calculation', results
    return 'allowed', results


def confirmed_history_quote(quote, body, parts=None):
    """Match a continuous quote, allowing whitespace only at real bubble joins.

    Parts come from the same confirmed Guard snapshot as body, never model
    metadata. Internal whitespace, wording, numbers and punctuation stay exact.
    Bounded candidate starts avoid expensive matching on adversarial repeats.
    """
    if not isinstance(quote, str) or not quote or not isinstance(body, str):
        return None
    if quote in body:
        return 'exact'
    if (not isinstance(parts, list) or not 2 <= len(parts) <= 4
            or any(not isinstance(p, str) or not p for p in parts)
            or ''.join(parts) != body or len(quote) > 8000):
        return None
    boundaries = set()
    offset = 0
    for part in parts[:-1]:
        offset += len(part)
        left, right = body[offset - 1], body[offset]
        # Never turn a split identifier or number into a different expression.
        if ((left.isascii() and (left.isalnum() or left == '_'))
                and (right.isascii() and (right.isalnum() or right == '_'))):
            continue
        if left.isdigit() and right.isdigit():
            continue
        boundaries.add(offset)
    start = -1
    for _ in range(128):
        start = body.find(quote[0], start + 1)
        if start < 0:
            return None
        i, j = start, 0
        while i < len(body) and j < len(quote):
            if i in boundaries and quote[j] in ' \t\r\n':
                before = j
                while j < len(quote) and quote[j] in ' \t\r\n' and j - before < 64:
                    j += 1
                if j == len(quote):
                    return 'confirmed_bubble_boundary'
            if j >= len(quote) or body[i] != quote[j]:
                break
            i += 1
            j += 1
        if j == len(quote):
            return 'confirmed_bubble_boundary'
    return None


def review_reason(text, state, evaluation_id, scope):
    """Program checks provenance/declared repetition, not semantic entailment."""
    from .autonomy_contract import parse
    decision = parse(text)
    if decision['reason'] != 'model_decision':
        return 'invalid_control'
    if decision['decision'] != 'send':
        return 'allowed'
    if internal_audit_json(decision['text']):
        return 'internal_audit_json_in_body'
    check = review_payload(text)
    if check is None:
        return 'content_check_missing_or_invalid'
    arithmetic_reason, checked = calculation_checks(check, decision['text'])
    scope['content_calculation_checks'] = checked
    if arithmetic_reason != 'allowed':
        scope['content_check_feedback'] = {'calculation_checks': checked, 'instruction':'纠正正文算术；核对实际前提和单位，不要伪造网页来源。'}
        return arithmetic_reason
    if check['novelty'] == 'repeat' and not scope.get('targeted_content_review'):
        return 'repeated_core_claim'
    if check.get('unsupported_self_episode') is True:
        return 'unsupported_self_episode'
    if scope.get('require_content_bindings'):
        contrast = check.get('contrast')
        if (not isinstance(contrast, dict) or type(contrast.get('history_index')) is not int
                or any(not isinstance(contrast.get(k), str) for k in
                       ('old_body_quote','old_core','new_core'))):
            return 'content_contrast_missing'
        current = decision['text']
        new_quote = contrast.get('new_body_quote', '')
        history = scope.get('confirmed_active_history', [])
        index = contrast['history_index']
        if not contrast['new_core'].strip():
            return 'new_core_missing'
        # A model-authored paraphrase is NOT a literal evidence quote. Keep the
        # mismatch in private audit; the actual full body is bound by the program.
        # Do not suppress a valid idea for redundant bookkeeping prose.
        import hashlib
        scope['content_body_binding'] = {
            'sha256': hashlib.sha256(current.encode()).hexdigest(),
            'model_quote_exact': bool(isinstance(new_quote, str) and new_quote and new_quote in current),
            'semantic_verification': 'same_agent_judgement_not_proof'}
        if index != -1:
            if not 0 <= index < len(history) or not contrast['old_body_quote'].strip():
                return 'contrast_not_confirmed_history'
            snapshots = scope.get('confirmed_active_history_parts', [])
            parts = snapshots[index] if isinstance(snapshots, list) and index < len(snapshots) else None
            binding = confirmed_history_quote(contrast['old_body_quote'], history[index], parts)
            if not binding:
                return 'contrast_not_confirmed_history'
            scope['content_history_quote_binding'] = {'history_index': index, 'match': binding,
                'history_sha256': hashlib.sha256(history[index].encode()).hexdigest()}
            from .autonomy_contract import repeated_content
            if repeated_content(contrast['new_core'], [contrast['old_core']]):
                return 'repeated_core_claim'
        elif contrast['old_body_quote']:
            return 'contrast_not_confirmed_history'
        # All substantive quantities are reviewed in the same Agent with their
        # original context. Digits alone cannot establish an external fact:
        # personal plans, enumeration, hypothetical premises and URLs contain them.
        scope['content_claim_bindings'] = [
            {'claim': c['claim'], 'body_quote_exact': bool(isinstance(c.get('body_quote'), str) and c['body_quote'] and
               c['body_quote'] in current)} for c in check['claims']]
    if decision['kind'] == 'external_fact' and not check['claims']:
        return 'external_claim_evidence_missing'
    # The exact source receipt, body excerpt and selected pocket remain private.
    evidence = {}
    selected = decision.get('pocket_id')
    pocket_sources = []
    if selected:
        pocket = next((p for p in state.list_pocket(scope['session']) if p['id'] == selected), None)
        pocket_sources = pocket.get('sources', []) if pocket else []
    valid_sources={(s.get('attempt_id'),s.get('url'),s.get('content_sha256'))
        for s in pocket_sources+state.tool_sources(scope['opportunity_id'])
        if isinstance(s,dict) and s.get('attempt_id')
        and s.get('verification')=='retrieved_not_fact_verified' and state.source_is_usable(s)}
    valid_attempts=sorted({s[0] for s in valid_sources})
    # Read only the selected/current receipts, not every historical web body.
    receipts=state.db.execute("""SELECT c.id,c.kind,c.metadata FROM autonomy_calls c
        JOIN autonomy_opportunities o ON o.id=c.opportunity_id
        WHERE o.session=? AND c.kind IN ('search','fetch')
        AND c.state IN ('succeeded','returned') AND c.id IN ("""+
        ','.join('?' for _ in valid_attempts)+')',
        (scope['session'],*valid_attempts)).fetchall() if valid_attempts else []

    def search_rows(excerpt):
        try:
            parsed=json.loads(excerpt)
            return parsed.get('results',[]) if isinstance(parsed,dict) else []
        except (ValueError,TypeError):
            # Bounded Search JSON may end inside a later snippet. Its complete
            # URL and visible snippet prefix are still bound to that one row.
            import re
            match=re.match(r'\s*\{\s*"results"\s*:\s*\[',excerpt)
            if not match:return []
            rows=[];position=match.end();decoder=json.JSONDecoder()

            def partial_row(position):
                if position>=len(excerpt) or excerpt[position]!='{':return None
                position+=1;row={}
                while position<len(excerpt):
                    while position<len(excerpt) and excerpt[position] in ' \t\r\n,':position+=1
                    try:key,position=decoder.raw_decode(excerpt,position)
                    except ValueError:break
                    if not isinstance(key,str):break
                    while position<len(excerpt) and excerpt[position].isspace():position+=1
                    if position>=len(excerpt) or excerpt[position]!=':':break
                    position+=1
                    while position<len(excerpt) and excerpt[position].isspace():position+=1
                    try:value,position=decoder.raw_decode(excerpt,position)
                    except ValueError:
                        # Only an unfinished snippet string may use its visible
                        # prefix. Never infer a truncated URL or another field.
                        if key!='snippet' or position>=len(excerpt) or excerpt[position]!='"':break
                        tail=excerpt[position:]
                        for removed in range(min(7,len(tail))):
                            try:value=json.loads((tail[:-removed] if removed else tail)+'"')
                            except ValueError:continue
                            if isinstance(value,str):row[key]=value
                            break
                        break
                    row[key]=value
                    if position<len(excerpt) and excerpt[position]=='}':break
                return row if isinstance(row.get('url'),str) and isinstance(row.get('snippet'),str) else None

            while position<len(excerpt):
                while position<len(excerpt) and excerpt[position] in ' \t\r\n,':position+=1
                try:row,position=decoder.raw_decode(excerpt,position)
                except ValueError:
                    row=partial_row(position)
                    if row is not None:rows.append(row)
                    break
                rows.append(row)
            return rows

    def normalized(value):
        import re
        import string
        # Normalize display escapes/spacing only. Never erase punctuation,
        # merge numeric tokens, rewrite the quote, or modify generated prose.
        value = value.translate(str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"'}))
        return ' '.join(re.sub(r'\\(['+re.escape(string.punctuation)+'])',r'\1',value).split())

    for attempt,kind,raw in receipts:
        meta = json.loads(raw)
        if meta.get('io_started') is not True:
            continue
        excerpt=meta.get('result_excerpt','')
        if not isinstance(excerpt,str):continue
        rows=search_rows(excerpt) if kind=='search' else []
        for source in meta.get('sources', []):
            if (source.get('attempt_id')!=attempt or source.get('verification')!='retrieved_not_fact_verified'
                    or (attempt,source.get('url'),source.get('content_sha256')) not in valid_sources):
                continue
            url=source['url'];bodies=[]
            if kind=='search':
                bodies=[row['snippet'] for row in rows if isinstance(row,dict)
                    and row.get('url')==url and isinstance(row.get('snippet'),str)]
            elif excerpt.startswith('URL: '+url+'\nTitle: ') and '\nContent: ' in excerpt:
                bodies=[excerpt.split('\nContent: ',1)[1]]
            evidence.setdefault(url,[]).extend(normalized(body) for body in bodies)
    for claim in check['claims']:
        if claim['url'] not in decision['source_refs']:
            scope['content_check_feedback'] = {'claim_url':claim['url'],
                'declared_source_refs':decision['source_refs'], 'retrieved_urls':list(evidence)[:12],
                'instruction':'核对这一项声明的完整URL；只可引用真实已取得来源，不能猜测尾斜线或参数。'}
            return 'claim_source_not_declared'
        if not any(normalized(claim['quote']) in body for body in evidence.get(claim['url'], [])):
            scope['content_check_feedback'] = {'claim_url':claim['url'], 'quote':claim['quote'],
                'retrieved_urls':list(evidence)[:12],
                'actual_evidence': [body[:1800] for body in evidence.get(claim['url'], [])[:2]],
                'instruction':'引文必须是这个URL的实际连续原文片段，不拼接省略、不混用别的来源。若来源不支持该主张，请纠正主张。'}
            return 'claim_quote_not_in_retrieved_evidence'
    return 'allowed'


class ContentRevision:
    """Direct delivery or one bounded correction; legacy A/B remains explicit."""
    def __init__(self, state, scope, *, policy=DEFAULT_CONTENT_REVISION_POLICY):
        if policy not in {'targeted_once', 'legacy_two_pass'}:
            raise ValueError('unknown_content_revision_policy')
        self.state, self.scope = state, scope
        self.policy = policy
        self.scope['require_content_bindings'] = policy == 'legacy_two_pass'
        self.scope['targeted_content_review'] = policy == 'targeted_once'
        self.revisions = 0
        self.original_body = None
        self.edit_ranges = []
        self.original_decision = None
        self.original_check = None
        self.failed_body_calculations = []
        self.resolved_text = None
        self.last_raw_text = None

    @staticmethod
    def repairable_body(text):
        """Only a single, one-line topic/control envelope can be repaired."""
        lines = str(text).strip().splitlines()
        controls = [line for line in lines if line.startswith('[PHASE3_PROACTIVE:')]
        topics = [line for line in lines if line.startswith('[PHASE3_TOPIC:')]
        # A plain draft is NOT automatically a send. Let the same Agent choose
        # the control once while requiring that any admitted body stays exact.
        if not controls and not topics and not any('PHASE3_' in line for line in lines):
            body = '\n'.join(lines).strip()
            return body if body and len(body) <= 12000 and not internal_audit_json(body) else None
        if controls != ['[PHASE3_PROACTIVE:send]'] or len(topics) != 1:
            return None
        body = '\n'.join(line for line in lines if line not in controls + topics).strip()
        if not body or any(token in body for token in ('PHASE3_', '"content_check"')):
            return None
        return body

    def targeted_reason(self, text, decision):
        if decision['reason'] != 'model_decision':
            return 'invalid_control'
        if decision['decision'] != 'send':
            return 'allowed'
        if internal_audit_json(decision['text']):
            return 'internal_audit_json_in_body'
        if unsupported_episodes(decision['text']):
            return 'unsupported_concrete_self_episode'
        # Optional first-pass checks never require a redundant novelty verdict.
        # Existing provenance and literal replay gates still run before sending.
        check = review_payload(text)
        if check is not None:
            return review_reason(text, self.state, self.scope['evaluation_id'], self.scope)
        from .autonomy_contract import protocol_lines, topic_json
        lines, _ = protocol_lines(text)
        if len(lines) > 1 and lines[1].startswith('[PHASE3_TOPIC:'):
            raw = lines[1][len('[PHASE3_TOPIC:'):]
            try:
                meta, _ = topic_json(raw[:-1] if raw.endswith(']') else raw)
            except (ValueError, TypeError):
                return 'invalid_control'
            if isinstance(meta, dict) and 'content_check' in meta:
                return 'content_check_missing_or_invalid'
        return ('external_claim_evidence_missing' if decision['kind'] == 'external_fact' else 'allowed')

    def allowed_edits(self, text, decision, reason):
        body = decision.get('text', '')
        fragments = []
        check = review_payload(text) or {}
        if reason in {'unsupported_concrete_self_episode', 'unsupported_self_episode'}:
            fragments = unsupported_episodes(body)
        elif reason in {'claim_quote_not_in_retrieved_evidence', 'claim_source_not_declared'}:
            feedback = self.scope.get('content_check_feedback') or {}
            fragments = [c.get('body_quote', '') for c in check.get('claims', [])
                         if c.get('url') == feedback.get('claim_url')]
        elif reason in {'calculation_mismatch', 'invalid_calculation_rounding'}:
            fragments = [str(c.get('claimed_result', '')) for c in
                         self.scope.get('content_calculation_checks', []) if not c.get('equal')]
        ranges = []
        for part in fragments[:8]:
            if isinstance(part, str) and part and body.count(part) == 1:
                start = body.index(part)
                ranges.append((start, start + len(part)))
        return ranges

    def edits_within_scope(self, body):
        from difflib import SequenceMatcher
        if body == self.original_body:
            return True
        if self.original_body is None or not self.edit_ranges:
            return False
        for op, a, b, c, d in SequenceMatcher(None, self.original_body, body, autojunk=False).get_opcodes():
            if op == 'equal':
                continue
            if not any(lo <= a <= b <= hi
                       for lo, hi in self.edit_ranges):
                return False
            # A correction cannot use an admitted short span as a new essay.
            if d - c > max(32, (b - a) * 2 + 16):
                return False
        return True

    def revision_identity_reason(self, text, decision):
        """Metadata edits cannot remove the evidence obligation of intact facts."""
        old = self.original_decision or {}
        if old.get('reason') == 'model_decision':
            if any(decision.get(k) != old.get(k) for k in ('kind', 'topic', 'pocket_id')):
                return 'revision_identity_changed'
        before, after = self.original_check, review_payload(text)
        if before is not None:
            if after is None:
                return 'revision_declared_evidence_removed'
            old_claims, new_claims = before.get('claims', []), after.get('claims', [])
            if len(new_claims) < len(old_claims):
                return 'revision_declared_evidence_removed'
            for claim in old_claims:
                quote = claim.get('body_quote', '')
                intact = not quote or quote not in (self.original_body or '') or quote in decision['text']
                if intact and not any(c.get('claim') == claim.get('claim') and
                        (not quote or c.get('body_quote') == quote) for c in new_claims):
                    return 'revision_declared_evidence_removed'
            # The calculator corrects a declared result, not arbitrarily changed
            # premises or units that could make an old wrong answer "valid".
            old_calc, new_calc = before.get('calculations', []), after.get('calculations', [])
            if old_calc and [(c.get('expression'), c.get('unit', ''), c.get('round_digits')) for c in new_calc] != [
                    (c.get('expression'), c.get('unit', ''), c.get('round_digits')) for c in old_calc]:
                return 'revision_calculation_premise_changed'
            for index, old_number in self.failed_body_calculations:
                if self.original_body.count(old_number) != 1:
                    return 'revision_calculation_body_unbound'
                new_number = str(new_calc[index].get('result', ''))
                expected = self.original_body.replace(old_number, new_number, 1)
                if decision['text'] != expected:
                    return 'revision_calculation_body_not_corrected'
        return 'allowed'

    def inspect(self, text):
        if self.policy == 'legacy_two_pass':
            return self._inspect_legacy(text)
        from .autonomy_contract import parse
        from .autonomy_request import OpportunityStopped
        raw_text = text
        self.last_raw_text, self.resolved_text = raw_text, None
        if self.revisions and not self.edit_ranges:
            # Metadata repair happens BEFORE native cleanup. Only an explicit
            # send with exactly two envelope lines can bind the original draft;
            # no model prose is overwritten, and plugin-blocked text is never
            # restored. Raw SDK output remains separately audited.
            lines = str(text).strip().splitlines()
            if (len(lines) == 2 and lines[0] == '[PHASE3_PROACTIVE:send]'
                    and lines[1].startswith('[PHASE3_TOPIC:') and self.original_body):
                combined = '\n'.join(lines) + '\n' + self.original_body
                if parse(combined)['reason'] == 'model_decision':
                    text = combined
                    self.resolved_text = combined
        for name in ('content_body_binding', 'content_claim_bindings', 'content_history_quote_binding',
                     'content_calculation_checks', 'content_check_feedback'):
            self.scope.pop(name, None)
        decision = parse(text)
        reason = self.targeted_reason(text, decision)
        if (self.revisions and decision.get('decision') == 'send'
                and decision['reason'] == 'model_decision' and not self.edits_within_scope(decision['text'])):
            reason = 'revision_scope_violation'
        if self.revisions and decision.get('decision') == 'send' and decision['reason'] == 'model_decision':
            identity_reason = self.revision_identity_reason(text, decision)
            if identity_reason != 'allowed':
                reason = identity_reason
        self.scope.update(content_review_policy=self.policy, content_review_reason=reason,
            last_content_decision=decision, last_content_text=text, content_check=review_payload(text))
        self.state.write_audit(self.scope['evaluation_id'], 'decision', {
            'stage': 'same_agent_content_revision', 'policy': self.policy, 'revision': self.revisions,
            'visible_draft': raw_text, 'bound_draft': text if text != raw_text else None,
            'bound_original_body': text != raw_text,
            'check': review_payload(text), 'reason': reason,
            'semantic_fact_verification': 'not_proven_by_program_checks',
            'editable_ranges': self.edit_ranges, 'calculation_checks': self.scope.get('content_calculation_checks'),
            'history_quote_binding': self.scope.get('content_history_quote_binding')})
        if reason == 'allowed':
            return None
        body = decision.get('text', '') if decision['reason'] == 'model_decision' else self.repairable_body(text)
        # Ambiguous envelopes/internal JSON are not licenses to reconstruct a
        # message. Never expose failed output or quietly revert to a bad draft.
        if self.revisions or body is None or reason == 'internal_audit_json_in_body':
            self.scope['content_review_rejected'] = True
            raise OpportunityStopped('content_review_' + reason)
        self.original_body = body
        self.original_decision = decision
        self.original_check = review_payload(text)
        self.edit_ranges = self.allowed_edits(text, decision, reason)
        self.failed_body_calculations = [(index, str(row.get('claimed_result', '')))
            for index, row in enumerate(self.scope.get('content_calculation_checks') or [])
            if row.get('equal') is False and row.get('result_in_body') is True]
        self.revisions = 1
        feedback = {'reason': reason, 'editable_fragments': [body[a:b] for a,b in self.edit_ranges],
                    'metadata_only': not bool(self.edit_ranges),
                    'details': self.scope.get('content_check_feedback') or {}}
        instruction = TARGETED_REVISION + '\n具体问题：\n' + json.dumps(feedback, ensure_ascii=False)[:6500]
        if not self.edit_ranges:
            instruction += '\n本次仅修复控制/元数据：只输出两行PHASE3_PROACTIVE和PHASE3_TOPIC，不重复输出正文或分段标记。程序仅在你明确选择send且协议有效时绑定上条原草稿，随后照常进行原生清理和发送检查；无法修复可skip/defer。'
        if reason in {'external_claim_evidence_missing', 'content_check_missing_or_invalid'}:
            instruction += ('\n缺少的声明格式必须放入PHASE3_TOPIC JSON内：content_check:{"novelty":"new或continuation",'
                '"basis":"简短依据","claims":[{"claim":"正文中实际外部主张","body_quote":"原正文连续片段",'
                '"url":"已实际取得的完整URL","quote":"该URL资料中的连续原文"}]}。'
                '没有外部主张时claims才可为空；已有算式保留calculations，不改前提、单位或舍入精度。')
        return instruction

    def finalize(self, raw_text):
        """Return only the exact body/envelope admitted by the same inspection."""
        if (self.resolved_text is not None and raw_text == self.last_raw_text
                and self.scope.get('content_review_reason') == 'allowed'
                and not self.scope.get('content_review_rejected')):
            return self.resolved_text
        return raw_text

    def _inspect_legacy(self, text):
        from .autonomy_contract import parse
        for name in ('content_body_binding', 'content_claim_bindings', 'content_history_quote_binding',
                     'content_calculation_checks', 'content_check_feedback'):
            self.scope.pop(name, None)
        decision = parse(text)
        if self.revisions == 0 and decision['reason'] == 'model_decision' and decision['decision'] != 'send':
            return None
        reason = ('review_before_delivery' if self.revisions == 0 else
                  review_reason(text, self.state, self.scope['evaluation_id'], self.scope))
        episodes = unsupported_episodes(decision.get('text', ''))
        if self.revisions and decision.get('decision') == 'send' and episodes:
            reason = 'unsupported_concrete_self_episode'
        check = review_payload(text)
        final_check = (reason == 'allowed' and self.revisions == 1
                       and decision.get('decision') == 'send'
                       and bool((check or {}).get('claims') or self.scope.get('confirmed_active_history')))
        if final_check:
            reason = 'final_body_check_before_delivery'
        self.state.write_audit(self.scope['evaluation_id'], 'decision', {
            'stage': 'same_agent_content_revision', 'revision': self.revisions,
            'visible_draft': text, 'check': review_payload(text), 'reason': reason,
            'semantic_fact_verification': 'model_judgement_not_proof',
            'body_binding': self.scope.get('content_body_binding'),
            'claim_bindings': self.scope.get('content_claim_bindings'),
            'history_quote_binding': self.scope.get('content_history_quote_binding'),
            'calculation_checks': self.scope.get('content_calculation_checks')})
        self.scope['content_review_reason'] = reason
        self.scope['last_content_decision'] = decision
        self.scope['last_content_text'] = text
        self.scope['content_check'] = review_payload(text)
        if reason == 'allowed':
            return None
        if self.revisions >= 2:
            from .autonomy_request import OpportunityStopped
            self.scope['content_review_rejected'] = True
            raise OpportunityStopped('content_review_' + reason)
        self.revisions += 1
        return ((FINAL_CHECK if final_check else REVIEW) + '\nconfirmed_history（仅此为已确认历史，草稿不在其中）：\n' +
                json.dumps(self.scope.get('confirmed_active_history', []), ensure_ascii=False)[:12000] +
                '\n待核对的实际草稿正文（数值、主体、时间与限定应全部核对，个人计划/组织性计数/假设前提不是外部新闻）：\n' + decision.get('text','')[:12000] +
                ('\n检测到无实际证据的具体自身经历，请改为真实判断或明确假设：'+json.dumps(episodes,ensure_ascii=False) if episodes else '') +
                ('\n具体核对反馈：'+json.dumps(self.scope.get('content_check_feedback'),ensure_ascii=False)[:4500]
                 if self.scope.get('content_check_feedback') else '') +
                ('\n程序核对未通过：' + reason if self.revisions > 1 and not final_check else ''))
