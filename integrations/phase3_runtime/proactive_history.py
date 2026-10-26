"""Bounded prompt-history tuning around the pinned Private instance.

Keep the native topic store, cleanup, text normalization and duplicate checks.
Only retention during cleanup and the number of displayed summaries change.
"""
from contextvars import ContextVar
import re


class NativeProactiveHistory:
    METHODS = ('_cleanup_recent_proactive_topics', '_proactive_dedup_window_minutes',
               '_format_recent_proactive_topics_hint')

    def __init__(self, private, session, policy=None):
        self.private, self.session = private, session
        policy = {} if policy is None else policy
        if not isinstance(policy, dict) or set(policy) - {'summary_limit', 'ordinary_retention_hours'}:
            raise ValueError('invalid_proactive_history_policy')
        self.limit = policy.get('summary_limit', 4)
        self.hours = policy.get('ordinary_retention_hours', 6)
        if (type(self.limit) is not int or not 1 <= self.limit <= 8
                or type(self.hours) is not int or not 6 <= self.hours <= 24):
            raise ValueError('invalid_proactive_history_policy')
        # AstrBot may populate schema defaults on an old config. The legacy
        # 4/6 pair is a no-op, including custom native retention such as 0
        # (30 days) or a sent-topic window longer than six hours.
        self.enabled = (self.limit, self.hours) != (4, 6)
        self._retaining = ContextVar('phase3_proactive_topic_retention', default=False)
        self.originals = {}

    def check_api(self):
        if self.enabled and any(not callable(getattr(self.private, name, None)) for name in self.METHODS):
            raise ValueError('pinned_private_proactive_history_api_missing')

    def attach(self, bindings):
        if not self.enabled:
            return
        self.check_api()
        callbacks = (self.cleanup, self.window_minutes, self.format_hint)
        for name, callback in zip(self.METHODS, callbacks):
            original = getattr(self.private, name)
            self.originals[name] = original
            bindings[name] = original
            setattr(self.private, name, callback)

    def _target(self, user):
        return isinstance(user, dict) and user.get('umo') == self.session

    def window_minutes(self, kind, default):
        if self._retaining.get() and kind == 'sent':
            return self.hours * 60
        return self.originals['_proactive_dedup_window_minutes'](kind, default)

    def cleanup(self, user, *, now=None):
        token = self._retaining.set(self._target(user))
        try:
            # This scope changes only native retention. Once cleanup returns,
            # native duplicate checks still read their original 240-minute
            # setting. Weather retention and the 12-entry store cap stay native.
            return self.originals['_cleanup_recent_proactive_topics'](user, now=now)
        finally:
            self._retaining.reset(token)

    def format_hint(self, user):
        original = self.originals['_format_recent_proactive_topics_hint']
        if not self._target(user):
            return original(user)
        recent = self.cleanup(user)[-self.limit:]
        rows, advice = [], []
        # The pinned formatter emits at most four 80-character summaries.
        # Reuse it on bounded views of the same store, preserving its exact
        # normalization, elapsed-time labels and leak filtering. The views do
        # not replace the user's real list or reply/ignored state.
        for start in range(0, len(recent), 4):
            view = {**user, 'recent_proactive_topics':
                    [dict(item) for item in recent[start:start + 4]]}
            for line in original(view).splitlines():
                if re.match(r'^- .+说过：', line):
                    rows.append(line)
                elif line and line not in advice:
                    advice.append(line)
        # Native weather guidance can apply because of an older retained topic
        # outside the displayed eight. Ask its original full-store formatter
        # for guidance, but do not append that call's duplicate summary rows.
        view = {**user, 'recent_proactive_topics':
                [dict(item) for item in user.get('recent_proactive_topics', [])]}
        for line in original(view).splitlines():
            if line and not re.match(r'^- .+说过：', line) and line not in advice:
                advice.append(line)
        return '\n'.join(rows + advice)
