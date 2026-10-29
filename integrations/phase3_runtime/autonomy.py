"""Opt-in autonomy adapter on the existing native scheduler and framework Agent.

No scheduler loop, second model, inbound event, or independent conversation
history is created. Existing native ticks/generation/finalization are reused.
"""
import asyncio
from contextvars import ContextVar
from datetime import datetime
import hashlib
import inspect
import json
import re
import time
import uuid
from zoneinfo import ZoneInfo

from .proactive import NativeProactiveBridge, resolve_native_target
from .autonomy_contract import PROMPT, VERSION, parse, content_identity, repeated_content
from .autonomy_request import OpportunityStopped, install_runner, monitor
from .autonomy_supplement import check_supplement


class AutonomousProactiveBridge(NativeProactiveBridge):
    def __init__(self, *args, autonomy_state, autonomy_settings, search_factory=None,
                 self_preferences=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.autonomy_enabled = True
        self.state, self.options = autonomy_state, autonomy_settings
        self.search_factory, self.self_preferences = search_factory, self_preferences
        self.scopes, self.pending = {}, {}
        self.native_adapter = None
        self._native_gate_scope = ContextVar('phase3_autonomy_native_final_gate', default=None)

    def attach(self):
        super().attach()
        from .autonomy_native import NativeAutonomyBindings
        self.native_adapter = NativeAutonomyBindings(self,
            opportunity_admission=self.opportunity_admission,
            is_open_opportunity=self.is_open_opportunity)
        self.native_adapter.attach()
        name = '_install_proactive_semantic_provider_fallback'
        self._additional_bindings[name] = getattr(self.private, name)
        setattr(self.private, name, self.install_runner)
        for name, replacement in (
                ('_review_proactive_message_send_decision', self.review_native_final_gate),
                ('_unverified_proactive_fact_decision', self.review_native_recent_fact)):
            original = getattr(self.private, name, None)
            if callable(original):
                self._additional_bindings[name] = original
                setattr(self.private, name, replacement)

    def detach(self):
        self.autonomy_enabled = False
        if self.native_adapter:
            self.native_adapter.detach()
        super().detach()

    def input_version(self, session):
        row = self.guard.store.db.execute(
            'SELECT max(rowid) FROM inbound WHERE platform=? AND session=?',
            (self.guard.phase3_platform_id, session)).fetchone()
        return str(row[0] or 0)

    async def scheduler_cycle(self, *, immediate=False):
        await self.offer_supplement()
        return await super().scheduler_cycle(immediate=immediate)

    async def offer_supplement(self):
        """Offer once through the native candidate pool, never a second timer."""
        if not self.options['supplements'] or not self.gate.continuation_allowed(
                self.gate.session,busy=self.guard.session_busy(self.gate.session)):
            return False
        offer=getattr(self.private,'_offer_proactive_candidate',None)
        if not callable(offer):return False
        row=self.guard.store.db.execute('''SELECT d.turn_id,d.kind,MAX(d.finished_at) finished
            FROM deliveries d JOIN turns t ON t.turn_id=d.turn_id
            WHERE t.session=? AND d.state='sent' AND d.kind!='progress'
            GROUP BY d.turn_id,d.kind ORDER BY MAX(d.rowid) DESC LIMIT 1''',(self.gate.session,)).fetchone()
        if row is None or not row['finished']:return False
        try:finished=datetime.fromisoformat(row['finished']).timestamp()
        except ValueError:return False
        now=self.gate.clock();age=now-finished
        if not self.options['supplement_min_seconds']<=age<self.options['supplement_max_seconds']:
            return False
        exchange='exchange:'+row['turn_id'];attempt='supplement:'+row['turn_id']
        if self.gate._get('supplement_offered:'+exchange):return False
        receipt=self.guard.store.bubble_result(row['turn_id'],row['kind'])
        if receipt.get('state')!='sent':return False
        parent='turn:'+row['turn_id'];parent_kind='passive'
        if row['kind']=='proactive':
            candidates=self.gate.db.execute('SELECT candidate FROM proactive_attempts ORDER BY attempted DESC LIMIT 24').fetchall()
            parent=next((c[0] for c in candidates if str(uuid.uuid5(uuid.NAMESPACE_URL,
                'qq-companion-proactive:proactive:'+c[0]))==row['turn_id']),None)
            if parent is None:return False
            parent_kind='interest' if parent.endswith(':interest') else 'proactive'
        eligible=self.state.supplement_eligibility(exchange,parent_attempt=parent,
            parent_kind=parent_kind,attempt_id=attempt)
        if not eligible['allowed']:return False
        last=self.guard.store.db.execute('SELECT received_at FROM inbound WHERE session=? ORDER BY rowid DESC LIMIT 1',
            (self.gate.session,)).fetchone()
        if last and datetime.fromisoformat(last[0]).timestamp()>finished:return False
        uid,user=resolve_native_target(self.private,self.gate.session)
        expires=finished+self.options['supplement_max_seconds']
        candidate={'id':attempt,'source':'conversation','reason':'conversation','action':'message',
            'topic':'最近交流的一次可选补充','motive':'只在仍有自然的补充想法时接着说一次，不引入无关新话题，不要求发送',
            'scheduled_ts':now,'window_start_at':now,'preferred_ts':now,'best_until_at':expires,
            'expire_at':expires,'window_timezone':'Asia/Shanghai','origin_event_id':attempt,
            'context':{'phase3_supplement':{'exchange_id':exchange,'parent_attempt':parent,
                'parent_kind':parent_kind,'input_version':self.input_version(self.gate.session),
                'confirmed_text':receipt.get('delivered_text','')[:2000]}}}
        async with self.private._data_lock:
            accepted=bool(offer(uid,user,candidate))
            if accepted:
                self.gate.set_control('supplement_offered:'+exchange,'1')
                self.private._schedule_data_save(sections={'users','proactive_candidate_pool'})
        return accepted

    def is_open_opportunity(self, user):
        native = self.private._planned_proactive_impulse(user) or {}
        evidence = native.get('context') or {}
        return (not native.get('trigger_message_id') and not native.get('trigger_inbound_count')
                and not any(evidence.get(k) for k in ('phase3_event_revision','event_id','memo_id','goal_id','source_message_id')) and
                str(user.get('planned_proactive_source') or '') in
                {'random', 'impulse', 'proactive', 'interest', 'own_opinion', 'want_to_chat'})

    def opportunity_admission(self, user):
        session = str(user.get('umo') or '')
        if not self.autonomy_enabled or getattr(self.guard, 'stopping', False):
            return 'service_stopping'
        try:
            _, target = resolve_native_target(self.private, session)
        except PermissionError:
            return 'identity_unavailable'
        if session != self.gate.session:
            return 'not_authorized'
        now = self.gate.clock()
        if now < float(self.gate._get('autonomy_not_before', '0')):
            return 'model_deferred'
        candidate = str(self.private._planned_proactive_delivery_key(target) or '')
        native = self.private._planned_proactive_impulse(target) or {}
        if not candidate or not native:
            return 'missing_candidate_identity'
        if self.gate.candidate_expiry(native.get('expire_at')) <= now:
            return 'candidate_expired'
        if native.get('state') in {'sent', 'blocked', 'cancelled', 'dropped', 'failed', 'unknown'}:
            return 'candidate_terminal'
        return self.gate.permit(session, candidate_id=candidate, source='want_to_chat',
            busy=self.guard.session_busy(session), purpose='explore')

    def scope_admission(self, scope):
        if not self.autonomy_enabled or getattr(self.guard, 'stopping', False):
            return 'service_stopping'
        if self.gate.clock() >= scope['deadline']:
            return 'exploration_deadline'
        if self.input_version(scope['session']) != scope['input_version']:
            return 'new_user_input'
        if not self.gate.continuation_allowed(scope['session'],
                busy=self.guard.session_busy(scope['session'])):
            hour = datetime.fromtimestamp(self.gate.clock(), ZoneInfo('Asia/Shanghai')).hour
            return 'quiet_hours' if not 9 <= hour < 23 else 'paused_or_busy'
        try:
            _, user = resolve_native_target(self.private, scope['session'])
        except PermissionError:
            return 'identity_unavailable'
        if str(self.private._planned_proactive_delivery_key(user) or '') != scope['candidate_id']:
            return 'candidate_superseded'
        return 'allowed'

    async def _context(self, user):
        session, candidate, event_id, context = await super()._context(user)
        native = self.private._planned_proactive_impulse(user) or {}
        evidence = native.get('context') or {}
        chosen = evidence.get('phase3_autonomy', {}) if isinstance(evidence, dict) else {}
        if not isinstance(chosen, dict):
            chosen = {}
        # Candidate labels are scheduling metadata, not content-wide bans.
        context['topic_id'] = (str(chosen.get('topic_identity') or '')
            if chosen.get('candidate_id') == candidate and chosen.get('topic')
            and chosen.get('topic_identity') == content_identity(chosen['topic']) else '')
        # Actual-content replay protection is separate. A previous unanswered
        # message does not suppress a new idea under the same specific subject.
        context['topic_cooldown'] = False
        if self.is_open_opportunity(user):
            context['source'] = 'want_to_chat'
        return session, candidate, event_id, context

    def selected_topic(self, scope, text, *, interest=False, persist=False):
        """Specific model-selected topic, distinct from transport/body identity.

        An interest preface precedes the final decision and uses its actual
        visible text. Native random/activity_share labels are never used here.
        """
        decision = scope.get('decision') or {}
        topic = str((decision.get('topic') if not interest else '') or text or '').strip()
        identity = content_identity(topic)
        if persist and topic:
            native = self.private._planned_proactive_impulse(scope['user']) or {}
            if str(self.private._planned_proactive_delivery_key(scope['user']) or '') == scope['candidate_id']:
                context = native.get('context')
                if context is None:
                    context = native['context'] = {}
                if not isinstance(context, dict):
                    raise ValueError('invalid_native_topic_context')
                context['phase3_autonomy_interest' if interest else 'phase3_autonomy'] = {
                    'candidate_id': scope['candidate_id'], 'evaluation_id': scope['evaluation_id'],
                    'topic': topic, 'topic_identity': identity, 'selected_at': self.gate.clock()}
                self.private._schedule_data_save(sections={'users'})
        return identity

    @staticmethod
    def _visible_body_identity(text):
        from ..phase1_guard.bubbles import MARKER
        return content_identity(str(text or '').replace(MARKER, ''))

    def closed_topics(self, session, *, limit=12):
        """Bounded private context from existing controls, audit and receipts.

        Legacy gates stored body hashes instead of topic hashes. Link those
        exact attempts to confirmed content; never derive a blanket domain ban.
        No private text is copied to a public diagnostic or a second store.
        """
        if session != self.gate.session:
            return []
        rows = self.gate.db.execute('''SELECT a.candidate,a.topic_id FROM proactive_attempts a
            JOIN proactive_controls c ON c.name='blocked:'||a.topic_id
            WHERE c.value!='' ORDER BY a.attempted DESC LIMIT 48''').fetchall()
        found = []
        seen = set()
        for candidate, identity in rows:
            if identity in seen:
                continue
            turn = str(uuid.uuid5(uuid.NAMESPACE_URL, 'qq-companion-proactive:proactive:'+candidate))
            confirmed = self.guard.store.db.execute('''SELECT d.text FROM deliveries d
                JOIN turns t ON t.turn_id=d.turn_id WHERE t.platform=? AND t.session=?
                AND d.turn_id=? AND d.kind='proactive' AND d.state='sent'
                ORDER BY d.part_index LIMIT 4''',
                (self.guard.phase3_platform_id, session, turn)).fetchall()
            body = '\n'.join(row[0] for row in confirmed)
            title = self.gate.db.execute('''SELECT e.topic FROM autonomy_evaluations e
                JOIN autonomy_opportunities o ON o.id=e.opportunity_id WHERE o.session=?
                AND (e.id=? OR e.delivery_attempt=?) AND e.topic!=''
                ORDER BY e.started DESC LIMIT 1''', (session,candidate,candidate)).fetchone()
            # Interest and later final may choose different topics; only the
            # actually sent interest text describes its closed subject.
            topic = str(title[0]) if title and not candidate.endswith(':interest') else ''
            if not body and not topic:
                continue
            seen.add(identity)
            found.append({'topic_id': identity, 'topic': topic[:240],
                'confirmed_excerpt': body[:600],
                'body_identity': self._visible_body_identity(body) if body else ''})
            if len(found) >= limit:
                break
        return found

    def topic_block_reason(self, scope, text, *, interest=False):
        identity = self.selected_topic(scope,text,interest=interest)
        if (self.gate._get('blocked:'+identity)
                or self.gate._get('blocked:'+content_identity(text))):
            return 'topic_suppressed'
        body_identity = self._visible_body_identity(text)
        for closed in self.closed_topics(scope['session']):
            if ((closed['topic'] and content_identity(closed['topic']) == identity)
                    or closed['body_identity'] == body_identity):
                return 'topic_suppressed'
        return 'allowed'

    async def tick_user(self, user_id, user):
        if not isinstance(user, dict) or self.opportunity_admission(user) != 'allowed':
            return
        session, candidate_id, _, _ = await self._context(user)
        native = self.private._planned_proactive_impulse(user)
        token = self._semantic_scope.set(True)
        try:
            return await self._additional_bindings['_tick_user'](user_id, user)
        finally:
            self._semantic_scope.reset(token)
            decision = self._decisions.pop(candidate_id, None)
            if decision and decision.get('reason') == 'model_decision' and decision['decision'] in {'defer', 'skip'}:
                self._record_decision(user, native, decision)
            scope = self.pending.get(candidate_id)
            if scope:
                # The fixed native tick awaits its transport before returning.
                # Do not settle any running/unknown Guard attempt as unattempted.
                gate_result = scope.get('native_final_gate') or {}
                self.settle_native_no_send(scope, 'native_not_sent',
                    'native_tick_ended_without_guard_attempt', gate_result=gate_result)
            self.pending.pop(candidate_id, None)

    def settle_native_no_send(self, scope, state, reason, *, gate_result=None):
        """Close only an unattempted ready candidate; preserve the model send."""
        if self.gate.db.execute('SELECT 1 FROM proactive_attempts WHERE candidate=?',
                                (scope['candidate_id'],)).fetchone():
            return False
        self.state._idle_connection()
        with self.state.db:
            changed = self.state.db.execute('''UPDATE autonomy_evaluations
                SET effective_state=?,reason=?,finished=?
                WHERE id=? AND state='complete' AND effective_state='ready_to_send'
                AND delivery_attempt='' ''',
                (state, str(reason)[:160], self.gate.clock(), scope['evaluation_id'])).rowcount
        if changed:
            try:
                self.state.write_audit(scope['evaluation_id'], 'decision', {
                    'stage':'native_final_execution', 'model_decision':scope.get('decision'),
                    'effective_state':state, 'reason':reason,
                    'native_gate':gate_result, 'final_guard_attempt_created':False,
                    'interest_attempt':scope.get('interest_attempt')})
            except Exception as exc:
                self.metrics['autonomy_native_gate_audit_error_type'] = type(exc).__name__
        return bool(changed)

    async def review_native_final_gate(self, user, text, **kwargs):
        candidate = str(self.private._planned_proactive_delivery_key(user) or '')
        scope = self.pending.get(candidate) or self.scopes.get(str(user.get('umo') or ''))
        if scope and (scope['candidate_id'] != candidate or scope['session'] != self.gate.session):
            scope = None
        token = self._native_gate_scope.set(scope)
        try:
            result = await self._additional_bindings['_review_proactive_message_send_decision'](user,text,**kwargs)
        finally:
            self._native_gate_scope.reset(token)
        if scope and isinstance(result,dict):
            scope['native_final_gate'] = dict(result)
            try:
                self.state.write_audit(scope['evaluation_id'],'decision', {
                    'stage':'native_final_gate', 'input_text':text,
                    'model_decision':scope.get('decision'), 'native_result':result,
                    'hypothetical_predicate_checked':scope.get('native_hypothetical_predicate_checked',False)})
            except Exception as exc:
                self.metrics['autonomy_native_gate_audit_error_type'] = type(exc).__name__
            if result.get('decision') in {'drop','defer'}:
                self.settle_native_no_send(scope,
                    'native_rejected' if result['decision']=='drop' else 'native_deferred',
                    result.get('reason') or 'native_final_gate_'+result['decision'],gate_result=result)
        return result

    def review_native_recent_fact(self, text, **kwargs):
        original = self._additional_bindings['_unverified_proactive_fact_decision']
        result = original(text, **kwargs)
        scope = self._native_gate_scope.get()
        if (not result or not scope or not self._semantic_scope.get()
                or (scope.get('decision') or {}).get('kind') not in {'hypothesis','creative_work'}):
            return result
        # Native's optional-subject regex treats conditional "才发现" as a
        # completed recent self action. Mask only this ambiguous predicate for
        # another call to the SAME checker. The actual payload is never edited.
        source = str(text or '')
        if re.search(r'(?:我|本小姐|咱).{0,12}(?:刚刚|刚才|昨晚|昨天|前天|上周|曾经|刚查到)',source):
            return result
        changed = False
        def unit(match):
            nonlocal changed
            value = match.group()
            if re.search(r'昨天|昨晚|前天|上周|曾经|已经|刚刚|刚才',value):
                return value
            def consequence(hit):
                nonlocal changed
                prefix = value[:hit.start()]
                antecedent = re.search(r'(?:如果|假如|假设|要是|倘若|可能|也许|或许|说不定)([^。！？!?；;\n]{1,80})$',prefix)
                if not antecedent or re.search(r'我|本小姐|咱',antecedent.group(1)):
                    return hit.group()
                changed = True
                return '才意识到'
            return re.sub('才发现',consequence,value)
        probe = re.sub(r'[^。！？!?；;\n]+',unit,source)
        if not changed or original(probe,**kwargs) is not None:
            return result
        scope['native_hypothetical_predicate_checked'] = True
        return None

    def _record_decision(self, user, native, decision):
        if not isinstance(native, dict):
            return
        now = self.gate.clock()
        expiry = self.gate.candidate_expiry(native.get('expire_at'))
        delay = decision.get('delay_minutes', 0)
        next_at = now + delay * 60
        raw = decision['decision']
        executable = raw == 'defer' and next_at < expiry
        effective = ('deferred' if executable else 'defer_expired') if raw == 'defer' else 'skipped'
        reason = 'model_decision' if raw != 'defer' or executable else 'delay_exceeds_candidate_lifetime'
        context = native.setdefault('context', {})
        if context is None:
            context = native['context'] = {}
        context['phase3_decision'] = {'decision': raw, 'effective_state':effective,
            'decided_at': now, 'next_consider_at': next_at if executable else 0,
            'expires_at':expiry, 'reason':reason}
        native.update(state='deferred' if executable else 'blocked',
            last_status='deferred' if executable else 'dropped',
            last_note='phase3_'+effective, updated_ts=now)
        if executable:
            native.update(window_start_at=next_at, preferred_ts=next_at,
                best_until_at=min(expiry,max(float(native.get('best_until_at') or 0),next_at)))
            self.gate.set_control('autonomy_not_before', next_at)
        if user.get('planned_proactive_impulse_id')==native.get('id'):
            if executable:
                user.update(next_proactive_at=next_at,planned_proactive_window_start_at=next_at,
                    planned_proactive_best_until_at=native['best_until_at'],planned_proactive_expire_at=expiry)
            user['planned_proactive_delivery_state']=native['last_status']
        self.private._schedule_data_save(sections={'users'})

    async def compose_prompt(self, scope, user):
        """Compose from native data APIs; do not concatenate conflicting templates."""
        persona = await self.private._resolve_proactive_persona_prompt(user)
        history = await self.private._recent_private_conversation_for_proactive_review(
            user, limit=self.private._proactive_history_limit('generation'))
        recent = self.private._format_recent_proactive_topics_hint(user)
        plans = self.recent_confirmed(scope['session'], include_parts=True)[-8:]
        scope['confirmed_active_history_parts'] = plans
        scope['confirmed_active_history'] = [''.join(parts) for parts in plans]
        try:
            preferences = self.self_preferences.prompt() if self.self_preferences else '尚无已确认自身偏好；可以自主选题。'
        except Exception as exc:
            self.metrics['autonomy_preferences_error_type'] = type(exc).__name__
            preferences = '本次自身偏好暂不可用；不猜测旧记录，仍可自主选题。'
        pocket = self.state.list_pocket(scope['session'], limit=8)
        scope['available_pocket_ids'] = [p['id'] for p in pocket]
        scope['pocket_snapshot'] = pocket
        memory = ''
        getter = getattr(self.private, '_memory_companion_compose_feature_context', None)
        if callable(getter):
            try:
                memory = await asyncio.wait_for(getter(kind='proactive_generation',
                    query='已有关系和明确交流边界，仅作背景；不强制围绕旧话题', user=user,
                    user_id=str(user.get('user_id') or ''), top_k=5, max_chars=760), 3)
            except (TimeoutError, Exception):
                memory = '本次可选记忆不可用，不据此编造事实。'
        native = self.private._planned_proactive_impulse(user) or {}
        candidate = {'mode':'open_topic' if self.is_open_opportunity(user) else 'contextual',
            'candidate_id':scope['candidate_id'], 'remaining_minutes':max(0,round((float(native.get('expire_at') or 0)-self.gate.clock())/60,1)),
            'reason':user.get('planned_proactive_reason'),
            'context': {} if self.is_open_opportunity(user) else native.get('context', {}),
            'send_allowed':self.gate.permit(scope['session'], candidate_id=scope['candidate_id'],
                                          source='want_to_chat') == 'allowed',
            'unanswered_count':int(user.get('ignored_streak') or 0),
            'supplements_allowed':bool(self.options['supplements'])}
        supplement=(native.get('context') or {}).get('phase3_supplement')
        if supplement:
            eligibility=self.state.supplement_eligibility(supplement['exchange_id'],
                parent_attempt=supplement['parent_attempt'],parent_kind=supplement['parent_kind'],
                attempt_id=scope['candidate_id'])
            candidate['send_allowed']=(eligibility['allowed'] and
                supplement.get('input_version')==self.input_version(scope['session']) and
                self.gate.permit(scope['session'],candidate_id=scope['candidate_id'],
                    source='want_to_chat',supplement=True)=='allowed')
            candidate.update(mode='single_supplement',supplement=supplement,
                instruction='这是同一段交流的一次可选延迟补充。只延续原交流，不承担无关新题；不需要补则skip。不能补充的补充。')
        from ..phase1_guard.bubbles import PROMPT as BUBBLES
        return '\n\n'.join((str(persona), PROMPT, BUBBLES,
            '当前真实时间：'+datetime.fromtimestamp(self.gate.clock(),ZoneInfo('Asia/Shanghai')).isoformat(),
            '联系机会（调度标签不是真实事件）：'+json.dumps(candidate,ensure_ascii=False),
            '近期对话，只作为背景；不是本轮新问题：\n'+str(history),
            '实际主动历史；不催回复，不等于禁止同领域新内容：\n'+str(recent),
            '最近已确认发送的主动正文（只证明说过，不证明其中自身经历或外部事实为真；比较核心观点，不只措辞）：\n'+
                json.dumps(scope['confirmed_active_history'], ensure_ascii=False)[:12000],
            '用户明确关闭的具体话题（不要换说法继续；同领域的其他新内容仍可选择）：\n'+
                json.dumps([{key:row[key] for key in ('topic','confirmed_excerpt')}
                    for row in self.closed_topics(scope['session'])],ensure_ascii=False),
            '现有记忆边界：\n'+str(memory), '已确认的自身偏好：\n'+preferences,
            '待分享口袋（尚未说过，需判断时效和来源；不是用户事实）：\n'+json.dumps(pocket,ensure_ascii=False)))

    async def generate(self, user, name, reason, action_context='', action='message', motive=''):
        session, candidate, _, context = await self._context(user)
        if action != 'message' or self.opportunity_admission(user) != 'allowed':
            self.metrics['generation_blocked'] += 1
            return ''
        native = self.private._planned_proactive_impulse(user) or {}
        old = (native.get('context') or {}).get('phase3_decision', {})
        evaluation_id = candidate + (':reconsider:'+str(int(old.get('next_consider_at',0))) if old.get('decision')=='defer' else '')
        admitted = await self.gate.reserve_generation(session, candidate_id=candidate,
            evaluation_id=evaluation_id, purpose='explore', **context)
        if admitted != 'allowed':
            self.metrics['generation_blocked'] += 1
            return ''
        self.metrics['generation_attempts'] += 1
        measured_start=time.monotonic()
        started = self.gate.clock()
        admission = self.state.begin_opportunity(candidate, evaluation_id=evaluation_id,
            candidate_id=candidate, session=session, deadline=float(native.get('expire_at') or started),
            input_version=self.input_version(session), prompt_version=VERSION)
        if not admission['allowed']:
            self.gate.finish_generation(evaluation_id, 'failed')
            return ''
        persisted_started = self.state.evaluation(evaluation_id)['started']
        scope = {**admission, 'candidate_id':candidate, 'session':session,
                 'started_at':persisted_started, 'input_version':self.input_version(session), 'user':user,
                 'decision':None, 'tool_runtime':None, 'interest_attempt':None}
        scope['supplement']=(native.get('context') or {}).get('phase3_supplement')
        self.scopes[session] = scope
        self._active_generations[session] = candidate
        state, effective, why = 'failed', 'failed', 'generation_error'
        token = self._semantic_scope.set(True)
        try:
            if self.self_preferences:
                try:
                    await self.self_preferences.drain(limit=32)
                except Exception as exc:
                    self.metrics['autonomy_preferences_error_type']=type(exc).__name__
            text = await monitor(self.original_generate(user, name, reason,
                action_context=action_context, action=action, motive=motive),
                lambda:self.scope_admission(scope))
            decision = scope['decision'] or parse('')
            self.pending[candidate] = scope
            if scope.get('content_review_rejected'):
                effective, why = 'content_rejected', scope['content_review_reason']
                return ''
            if decision['reason'] != 'model_decision':
                effective, why = 'invalid_output', decision['reason']
                return ''
            if decision['decision'] != 'send':
                self._record_decision(user, native, decision)
                record = native['context']['phase3_decision']
                effective, why = record['effective_state'], record['reason']
                state = 'deferred' if decision['decision']=='defer' else 'skipped'
                self.save_progress(scope, decision)
                return ''
            allowed = self.send_admission(scope, str(text or ''))
            if allowed != 'allowed':
                effective, why = 'ready_unshared', allowed
                self.save_progress(scope, {**decision,'text':str(text or '')})
                state = 'generated'
                return ''
            state, effective, why = 'generated', 'ready_to_send', 'model_decision'
            return str(text or '')
        except asyncio.CancelledError:
            state, effective, why = 'cancelled', 'interrupted', 'service_cancelled'
            raise
        except OpportunityStopped as exc:
            state, effective, why = 'cancelled', 'interrupted', str(exc)
            return ''
        except Exception as exc:
            self.metrics['generation_errors'] += 1
            self.metrics['autonomy_last_error_type']=type(exc).__name__
            why='generation_error_'+type(exc).__name__
            return ''
        finally:
            decision = scope['decision'] or parse('')
            try:
                if effective in {'interrupted','failed','invalid_output','content_rejected'}:
                    self.save_progress(scope, decision, tool_only=True)
                self.state.update_decision(evaluation_id, raw_control=decision['raw_control'],
                    parsed=decision, effective_state=effective, reason=why, topic=decision['topic'])
                self.gate.finish_generation(evaluation_id, state)
            finally:
                self.metrics['generation_seconds'] += time.monotonic()-measured_start
                self.scopes.pop(session,None)
                self._active_generations.pop(session,None)
                self._semantic_scope.reset(token)

    def save_progress(self, scope, decision, *, tool_only=False):
        sources = self.state.tool_sources(scope['opportunity_id'])
        runtime = scope.get('tool_runtime')
        # Successful tool observations are preserved even if no final is produced.
        observations = getattr(runtime, 'observations', []) if runtime else []
        text = '' if tool_only else decision.get('text') or decision.get('progress') or ''
        if not text and not sources:
            return
        if not text:
            evidence=runtime.pending_evidence() if runtime and hasattr(runtime,'pending_evidence') else []
            text = ('未分享的工具检索资料，内容仍需判断，尚无已确认结论：\n'+
                    json.dumps(evidence,ensure_ascii=False)) if evidence else '已取得来源，后续机会需要重新阅读核实；尚未生成可分享结论。'
        question = decision.get('topic') or '待重新核实的公开资料'
        finding = 'finding:'+hashlib.sha256((scope['opportunity_id']+question).encode()).hexdigest()[:32]
        kind = decision.get('kind','observation')
        if tool_only:
            kind = 'observation'
        # Missing evidence does not turn an external assertion into a hypothesis.
        # Retain its type so the next opportunity must still obtain provenance.
        old = next((p for p in self.state.list_pocket(scope['session']) if p['id']==finding),None)
        selected = next((p for p in scope.get('pocket_snapshot',[]) if p['id']==decision.get('pocket_id')),None)
        if selected:
            finding, old = selected['id'], selected
            sources = sources or selected['sources']
        previous_hashes = {s.get('content_sha256') for s in (old or {}).get('sources', [])}
        fresh_evidence = any(s.get('content_sha256') and s['content_sha256'] not in previous_hashes
                             for s in self.state.tool_sources(scope['opportunity_id']))
        check = scope.get('content_check') or {}
        creative_progress = (kind in {'creative_work','hypothesis'} and decision.get('progress')
            and check.get('novelty') in {'new','continuation'} and bool(check.get('basis')))
        saved=self.state.save_finding(finding, session=scope['session'], question=question,
            content=text[:12000], kind=kind, sources=sources[:12],
            expires_at=self.gate.clock()+3*86400, opportunity_id=scope['opportunity_id'],
            progress=decision.get('progress','')[:3000], state='unshared',
            research_progress=bool((fresh_evidence and (tool_only or decision.get('progress'))) or creative_progress),
            expected_revision=old['revision'] if old else None)
        self.state.write_audit(scope['evaluation_id'],'decision',
            {'finding_id':finding,'persistence':saved,'tool_observations':observations,'interrupted':tool_only})
        if not saved.get('saved'):
            self.metrics['autonomy_pocket_limit_reason']=saved.get('reason','unknown')

    async def framework(self, *args, **kwargs):
        session = str(kwargs.get('umo') or '')
        if not str(kwargs.get('task') or '').startswith('proactive'):
            return await self.original_framework(*args,**kwargs)
        scope = self.scopes.get(session)
        if scope is None or self.scope_admission(scope) != 'allowed':
            return ''
        kwargs['prompt'] = await self.compose_prompt(scope, scope['user'])
        kwargs['max_steps'] = 10
        text = await self.original_framework(*args, **kwargs)
        decision = parse(text)
        if scope.get('content_review_rejected') and scope.get('last_content_decision'):
            # Preserve what the model actually chose; a validation refusal is
            # not a model skip. Draft body remains private and cannot be sent.
            decision = scope['last_content_decision']
        metric='decision_'+decision['decision'] if decision['reason']=='model_decision' else 'output_'+decision['reason']
        self.metrics[metric]=self.metrics.get(metric,0)+1
        scope['decision'] = decision
        if decision['reason'] == 'model_decision':
            self.selected_topic(scope,decision.get('text',''),persist=True)
        self._decisions[scope['candidate_id']] = decision
        if decision.get('pocket_id') in scope.get('available_pocket_ids',[]):
            self.state.select_finding(decision['pocket_id'],scope['evaluation_id'])
        return decision['text']

    def clear_framework_tools(self, req, names=None):
        scope = next(iter(self.scopes.values()),None)
        if scope is None or self.scope_admission(scope) != 'allowed' or not self.search_factory:
            return super().clear_framework_tools(req,names)
        runtime = self.search_factory(scope, self.state, lambda:self.scope_admission(scope))
        scope['tool_runtime'] = runtime
        from ..phase3_search.proactive import build_proactive_toolset
        from .autonomy_arithmetic import build_calculator_tool
        from astrbot.core.agent.tool import ToolSet
        req.func_tool = build_proactive_toolset(runtime) if self.options['exploration'] else ToolSet(tools=[])
        req.func_tool.add_tool(build_calculator_tool(self.state, scope, self.scope_admission))
        return []

    def install_runner(self, build_result, *, label):
        original = self._additional_bindings['_install_proactive_semantic_provider_fallback']
        result = original(build_result,label=label)
        scope = next(iter(self.scopes.values()),None)
        runner = getattr(build_result,'agent_runner',None)
        if scope is not None and runner is not None:
            from .autonomy_content import ContentRevision
            revision = ContentRevision(self.state, scope)
            install_runner(runner,state=self.state,evaluation_id=scope['evaluation_id'],
                admit=lambda:self.scope_admission(scope),interest=lambda text:self.send_interest(scope,text),
                revision=revision.inspect, finalize_revision=revision.finalize)
        return result

    def recent_confirmed(self, session, *, include_parts=False):
        rows = self.guard.store.db.execute('''SELECT d.turn_id,d.text FROM deliveries d JOIN turns t
            ON d.turn_id=t.turn_id WHERE t.session=? AND d.kind='proactive' AND d.state='sent'
            ORDER BY d.rowid DESC LIMIT 192''',(session,)).fetchall()
        plans={}
        for row in reversed(rows):
            plans.setdefault(row['turn_id'],[]).append(row['text'])
        retained = list(plans.values())[-48:]
        return retained if include_parts else [''.join(parts) for parts in retained]

    def decision_provenance(self, scope, decision):
        """Bind declared factual sources to durable successful tool receipts.

        This establishes retrieval provenance, not factual entailment. The
        model and later human review still judge whether sources support prose.
        A model-supplied URL, a verified boolean, or an expired pocket alone is
        never retrieval evidence.
        """
        refs = decision.get('source_refs') or []
        if not refs:
            return ('external_fact_without_provenance'
                    if decision.get('kind') == 'external_fact' else 'allowed')
        sources = self.state.tool_sources(scope['opportunity_id'])
        selected = decision.get('pocket_id')
        for finding in self.state.list_pocket(scope['session']):
            if finding['id'] == selected:
                sources += finding.get('sources', [])
        available = {}
        for source in sources:
            attempt = source.get('attempt_id')
            if not attempt or source.get('verification') != 'retrieved_not_fact_verified':
                continue
            receipt = self.state.db.execute('''SELECT c.metadata FROM autonomy_calls c
                JOIN autonomy_opportunities o ON o.id=c.opportunity_id
                WHERE c.id=? AND o.session=? AND c.kind IN ('search','fetch')
                AND c.state IN ('succeeded','returned')''', (attempt, scope['session'])).fetchone()
            if receipt is None:
                continue
            metadata = json.loads(receipt[0])
            if metadata.get('io_started') is not True:
                continue
            for actual in metadata.get('sources', []):
                if (actual.get('url') == source.get('url') and actual.get('attempt_id') == attempt
                        and actual.get('content_sha256') == source.get('content_sha256')
                        and self.state.source_is_usable(source)
                        and actual.get('verification') == 'retrieved_not_fact_verified'):
                    available[source['url']] = source
        if any(ref not in available for ref in refs):
            return 'external_fact_source_not_retrieved'
        return 'allowed'

    def send_admission(self, scope, text, *, interest=False):
        current = self.scope_admission(scope)
        if current != 'allowed':
            return current
        supplement_check = check_supplement(self, scope['user'], scope=scope, interest=interest)
        if supplement_check['applicable'] and not supplement_check['allowed']:
            return supplement_check['reason']
        if scope.get('interest_terminal') in {'failed','unknown','cancelled'}:
            return 'interest_delivery_unconfirmed'
        topic_permission = self.topic_block_reason(scope,text,interest=interest)
        if topic_permission != 'allowed':
            return topic_permission
        if not text.strip() or repeated_content(text,self.recent_confirmed(scope['session'])):
            return 'empty_or_repeated_content'
        decision = scope.get('decision') or {}
        provenance = self.decision_provenance(scope, decision)
        if provenance != 'allowed':
            return provenance
        supplement = scope.get('supplement')
        if supplement and (interest or supplement.get('input_version')!=self.input_version(scope['session'])):
            return 'supplement_no_longer_current'
        supplemental = bool(scope.get('interest_attempt') or supplement) and not interest
        if supplemental and not self.options['supplements']:
            return 'supplement_disabled'
        if self.native_adapter and self.native_adapter.send_daypart_blocked(scope['user'],
                scope['user'].get('planned_proactive_reason',''), self.gate.clock()):
            return 'native_daypart_limit'
        if supplemental:
            permission = self.state.supplement_eligibility(
                supplement['exchange_id'] if supplement else scope['candidate_id'],
                parent_attempt=supplement['parent_attempt'] if supplement else scope['interest_attempt'],
                parent_kind=supplement['parent_kind'] if supplement else 'interest', attempt_id=scope['candidate_id'])
            if not permission['allowed']:
                return permission['reason']
        return self.gate.permit(scope['session'],candidate_id=scope['candidate_id']+(':interest' if interest else ''),
            source='own_opinion', topic_id=self.selected_topic(scope,text,interest=interest),
            busy=self.guard.session_busy(scope['session']),supplement=supplemental,topic_cooldown=False)

    async def send_interest(self, scope, text):
        from .autonomy_content import internal_audit_json, unsupported_episodes
        if internal_audit_json(text) or unsupported_episodes(text):
            scope['interest_content_rejected'] = True
            return 'not_sent'
        if scope.get('interest_attempt') or self.send_admission(scope,text,interest=True)!='allowed':
            return 'not_sent'
        result = await self.dispatch(scope,text,interest=True)
        scope['interest_terminal']=result['state']
        if result.get('delivery',{}).get('sent_count',0):
            scope['interest_attempt'] = scope['candidate_id']+':interest'
        return result['state']

    async def dispatch(self, scope, text, *, interest=False):
        supplement_check = check_supplement(self, scope['user'], scope=scope, interest=interest)
        if supplement_check['applicable'] and not supplement_check['allowed']:
            return {'state': 'blocked', 'reason': supplement_check['reason']}
        topic_permission = self.topic_block_reason(scope,text,interest=interest)
        if topic_permission != 'allowed':
            return {'state':'blocked','reason':topic_permission}
        topic_id = self.selected_topic(scope,text,interest=interest,persist=True)
        candidate = scope['candidate_id']+(':interest' if interest else '')
        supplement=scope.get('supplement') if not interest else None
        parent = (supplement['parent_attempt'] if supplement else scope.get('interest_attempt')) if not interest else None
        if parent:
            reservation = self.state.reserve_supplement(supplement['exchange_id'] if supplement else scope['candidate_id'],
                parent_attempt=parent,parent_kind=supplement['parent_kind'] if supplement else 'interest',attempt_id=candidate)
            if not reservation['allowed']:
                return {'state':'blocked','reason':reservation['reason']}
        async def send():
            return await self.guard.send_proactive(scope['session'],text,delivery_id='proactive:'+candidate,
                is_current=lambda:self.scope_admission(scope)=='allowed')
        def observe():
            turn=str(uuid.uuid5(uuid.NAMESPACE_URL,'qq-companion-proactive:proactive:'+candidate))
            return self.guard.store.bubble_result(turn,'proactive')
        try:
            result = await self.gate.deliver(scope['session'],candidate_id=candidate,send=send,
                observe_delivery=observe,source='own_opinion',topic_id=topic_id,
                busy=self.guard.session_busy(scope['session']),supplement=bool(parent),topic_cooldown=False)
        except asyncio.CancelledError:
            # Native tick cannot project a prefix when cancellation unwinds it.
            # Settle from Guard only, and give its existing archive API one
            # bounded chance; never resume the remaining transport plan.
            receipt=observe() or {}
            if parent:
                self.state.finish_supplement(candidate,receipt.get('state','cancelled'))
            prefix=receipt.get('delivered_text','')
            if prefix:
                archive=asyncio.create_task(self.private._archive_proactive_message_to_conversation(
                    user=scope['user'],umo=scope['session'],user_prompt='',assistant_response=prefix))
                try:
                    archived=await asyncio.wait_for(asyncio.shield(archive),5)
                    self.state.write_audit(scope['evaluation_id'],'delivery',{
                        'attempt_id':candidate,'cancelled':True,'history_projected':archived is True,
                        'result':receipt})
                except (Exception,asyncio.CancelledError):
                    archive.cancel()
                    self.metrics['autonomy_cancelled_history_unconfirmed']=True
            if not interest:
                self.state.update_delivery(scope['evaluation_id'],attempt_id=candidate,
                    state=receipt.get('state','cancelled'),reason='cancelled_after_guard_receipt')
            raise
        if parent:
            try:
                self.state.finish_supplement(candidate,result['state'] if result['state']!='blocked' else 'cancelled')
            except Exception as exc:
                self.metrics['autonomy_supplement_audit_error_type']=type(exc).__name__
        try:
            self.state.write_audit(scope['evaluation_id'],'delivery',{'attempt_id':candidate,
                'parent_attempt':parent, 'interest':interest,'result':result})
        except Exception as exc:
            # Guard's durable receipt already exists; an auxiliary audit error
            # must not hide confirmed content from native history or retry it.
            self.metrics['autonomy_delivery_audit_error_type']=type(exc).__name__
        if interest:
            delivered=(result.get('delivery') or {}).get('delivered_text','')
            if delivered:
                try:
                    archived=await self.private._archive_proactive_message_to_conversation(user=scope['user'],
                        umo=scope['session'],user_prompt='',assistant_response=delivered)
                    self.state.write_audit(scope['evaluation_id'],'delivery',
                        {'attempt_id':candidate,'interest_history_projected':archived is True})
                except Exception as exc:
                    self.metrics['autonomy_interest_history_error_type']=type(exc).__name__
        return result

    async def send(self, umo, text, image_path='', *, extra_components=None, quote_message_id='', disable_segmenting=False):
        if image_path or extra_components or quote_message_id:
            return self.outcome_type(False,False,note='phase3_text_only_boundary')
        try:
            _, user=resolve_native_target(self.private,umo)
        except PermissionError:
            return self.outcome_type(False,False,note='phase3_identity_unavailable')
        candidate=str(self.private._planned_proactive_delivery_key(user) or '')
        scope=self.pending.get(candidate)
        if not scope or self.send_admission(scope,str(text))!='allowed':
            if scope:
                self.state.update_delivery(scope['evaluation_id'],attempt_id=candidate,
                    state='cancelled',reason='admission_changed_before_send')
            return self.outcome_type(False,False,note='phase3_autonomy_send_not_admitted')
        if not await self.private._proactive_persona_delivery_allowed(umo):
            self.state.update_delivery(scope['evaluation_id'],attempt_id=candidate,
                state='cancelled',reason='persona_route_denied')
            return self.outcome_type(False,False,note='persona_route_denied')
        result=await self.dispatch(scope,text)
        delivery=result.get('delivery') or {}
        delivered=delivery.get('delivered_text','')
        sent=result['state']=='sent'
        self.metrics['delivered' if sent else 'delivery_blocked']+=1
        try:
            self.state.update_delivery(scope['evaluation_id'],attempt_id=candidate,
                state=result['state'] if result['state']!='blocked' else 'cancelled',
                reason=result.get('reason') or 'guard_'+result['state'])
        except Exception as exc:
            self.metrics['autonomy_execution_audit_error_type']=type(exc).__name__
        if delivered and self.event_delivered:
            _,_,event_id,_=await self._context(user)
            if event_id:
                try:
                    native=self.private._planned_proactive_impulse(user) or {}
                    revision=(native.get('context') or {}).get('phase3_event_revision')
                    result_event=self.event_delivered(umo,event_id,revision,delivery)
                    if inspect.isawaitable(result_event):await result_event
                except Exception as exc:
                    self.metrics['autonomy_event_observer_error_type']=type(exc).__name__
        finding=(scope.get('decision') or {}).get('pocket_id')
        if finding and delivered:
            self.state.mark_shared(finding,attempt_id=candidate,confirmed_text=delivered,
                evaluation_id=scope['evaluation_id'],whole_plan_confirmed=sent)
        return self.outcome_type(bool(delivered),sent,delivered_text=delivered,
            note='phase3_'+result['state'],primary_complete=sent,delivery_umo=umo if delivered else '')
