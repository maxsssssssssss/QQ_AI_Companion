"""Thin confirmed-delivery bridge to PrivateCompanion's existing preferences.

The native users record owns both preferences and this observer's cursor.  No
second fact store, extraction model, after-send hook or chat-history write is
introduced.  Guard remains the source of transport truth.  Native 6.7.0 only
extracts explicit preferences in its five supported categories; this is not a
general opinion/revision store.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import math
import re
import time


CURSOR_KEY = "phase3_confirmed_self_preferences"
SCHEMA = "guard-native-self-preferences.v1"
CATEGORIES = frozenset({"music", "food", "media", "game", "aesthetic"})


def preference_only(statement):
    """An expressed taste is not proof of the concrete episode in its wording."""
    return not re.search(r'(?:昨天|前天|昨晚|今早|上周|上个月|最近|刚刚|刚才|以前|小时候).{0,16}(?:看过|看了|玩过|玩了|去过|去了|吃过|吃了|工作|上班)|(?:我|本小姐|咱).{0,8}(?:亲自|曾经).{0,12}(?:玩|看|去|吃|工作)', str(statement))


class NativeSelfPreferences:
    """An observer drained by the existing Runtime worker, not a scheduler.

    ``attach`` starts at the current ledger position on first installation.
    ``observe`` only wakes the existing worker.  ``drain`` can also run before
    an active opportunity to catch a previously confirmed reply.  The cursor
    and derived preference are saved together by the native users writer, so
    a crash before that save replays the same confirmed source safely.
    """

    def __init__(self, private, guard, allowed_session, *, wake=None, clock=time.time):
        self.private, self.guard = private, guard
        self.session, self.wake, self.clock = str(allowed_session), wake, clock
        self.attached = False
        self.pending = False
        self._lock = asyncio.Lock()
        self.metrics = {"examined": 0, "confirmed": 0, "changed": 0}

    def _target(self):
        from ..phase3_runtime.proactive import resolve_native_target
        expected = f"{self.guard.phase3_platform_id}:FriendMessage:{self.guard.phase3_admin_id}"
        if not self.guard.phase3_enabled or self.session != expected:
            raise PermissionError("self_preference_guard_scope_mismatch")
        return resolve_native_target(self.private, self.session)[1]

    def _cursor(self, user):
        value = user.get(CURSOR_KEY)
        if (not isinstance(value, dict) or value.get("schema") != SCHEMA
                or value.get("session") != self.session
                or type(value.get("row_id")) is not int or value["row_id"] < 0):
            raise ValueError("self_preference_cursor_invalid")
        return value["row_id"]

    def _max_row(self):
        return self.guard.store.db.execute(
            "SELECT COALESCE(MAX(d.rowid),0) FROM deliveries d JOIN turns t "
            "ON t.turn_id=d.turn_id WHERE t.platform=? AND t.session=?",
            (self.guard.phase3_platform_id, self.session)).fetchone()[0]

    def _save(self):
        # Native batching retains its revision checks and existing persistence.
        self.private._schedule_data_save(sections={"users"})

    def attach(self):
        if self.attached:
            return
        if (not callable(getattr(self.private, "_record_confirmed_bot_continuity", None))
                or not callable(getattr(self.private, "_schedule_data_save", None))):
            raise ValueError("pinned_native_self_preference_api_required")
        user = self._target()
        if CURSOR_KEY not in user:
            user[CURSOR_KEY] = {"schema": SCHEMA, "session": self.session,
                                "row_id": self._max_row(), "delivery_id": ""}
            try:
                self._save()
            except BaseException:
                user.pop(CURSOR_KEY, None)
                raise
        self._cursor(user)
        self.guard.register_delivery_observer(self.observe)
        self.attached = True
        self.pending = True
        if self.wake is not None:
            self.wake.set()

    def detach(self):
        if self.attached:
            self.guard.unregister_delivery_observer(self.observe)
        self.attached = False

    async def observe(self, payload):
        # Do not trust callback text or write native state in a transport hook.
        # Failed/unknown rows still advance the durable cursor during drain,
        # but can never become an expressed preference.
        if self.attached and payload.get("session") == self.session:
            self.pending = True
            if self.wake is not None:
                self.wake.set()

    @staticmethod
    def _confirmed_at(value):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            result = dt.timestamp() if dt.tzinfo is not None else 0
            return result if math.isfinite(result) and result > 0 else 0
        except (TypeError, ValueError, OverflowError):
            return 0

    async def drain(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("self_preference_drain_limit")
        if not self.attached:
            return 0
        async with self._lock:
            native_lock = getattr(self.private, "_data_lock", None)
            if native_lock is not None:
                await asyncio.wait_for(native_lock.acquire(), timeout=1.0)
            try:
                return self._drain_locked(limit)
            finally:
                if native_lock is not None:
                    native_lock.release()

    def _drain_locked(self, limit):
        user = self._target()
        cursor = self._cursor(user)
        if cursor > self._max_row():
            # An unexpected old-ledger replacement cannot silently rewind a
            # cursor and re-extract historical expressions over newer ones.
            raise ValueError("self_preference_ledger_rewind")
        rows = self.guard.store.db.execute(
            "SELECT d.rowid,d.delivery_id,d.turn_id,d.kind,d.text,d.state,d.finished_at "
            "FROM deliveries d JOIN turns t ON t.turn_id=d.turn_id "
            "WHERE t.platform=? AND t.session=? AND d.rowid>? ORDER BY d.rowid LIMIT ?",
            (self.guard.phase3_platform_id, self.session, cursor, limit)).fetchall()
        count = 0
        for row in rows:
            row_id, delivery_id, turn_id, kind, text, state, finished_at = row
            if state == "pending":
                break  # Never advance past a still-unsettled earlier delivery.
            old_cursor = deepcopy(user[CURSOR_KEY])
            old_preferences = deepcopy(user.get("bot_self_preferences", []))
            changed = False
            confirmed_at = self._confirmed_at(finished_at)
            try:
                if state == "sent" and kind in {"normal", "final", "proactive"} and confirmed_at:
                    # This native helper also handles departure signals.  A
                    # preference-only scratch record intentionally isolates
                    # that unrelated behavior; only its preference field is
                    # merged into the exact verified native user record.
                    scratch = {"bot_self_preferences": deepcopy(old_preferences)}
                    self.private._record_confirmed_bot_continuity(scratch, text, now=confirmed_at)
                    preferences = scratch.get("bot_self_preferences", old_preferences)
                    preferences = [p for p in preferences if p in old_preferences or
                        (isinstance(p, dict) and preference_only(p.get('statement', '')))]
                    if preferences != old_preferences:
                        for pref in preferences:
                            if isinstance(pref, dict) and pref.get("at") == confirmed_at:
                                pref["phase3_confirmed_source"] = {
                                    "delivery_id": delivery_id, "turn_id": turn_id,
                                    "kind": kind, "confirmed_at": confirmed_at,
                                    "semantic_scope": "expressed_preference_only",
                                    "episode_verified": False,
                                }
                        user["bot_self_preferences"] = preferences
                        changed = True
                    self.metrics["confirmed"] += 1
                user[CURSOR_KEY] = {"schema": SCHEMA, "session": self.session,
                                   "row_id": row_id, "delivery_id": delivery_id}
                self._save()
            except BaseException:
                user[CURSOR_KEY] = old_cursor
                user["bot_self_preferences"] = old_preferences
                raise
            count += 1
            self.metrics["examined"] += 1
            self.metrics["changed"] += int(changed)
        self.pending = len(rows) >= limit or count < len(rows)
        return count

    def current(self, *, limit=5):
        """Latest native expression per supported category, never user facts.

        A future fuller self-memory adapter may replace this bounded interface;
        it does not need to replace Guard, scheduling or the user's fact store.
        """
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("self_preference_recall_limit")
        user = self._target()
        preferences = user.get("bot_self_preferences")
        if not isinstance(preferences, list):
            return []
        latest = {}
        for order, item in enumerate(preferences):
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            statement = item.get("statement")
            at = item.get("at")
            if (category not in CATEGORIES or item.get("source") != "confirmed_visible_reply"
                    or not isinstance(statement, str) or not statement.strip() or not preference_only(statement)
                    or not isinstance(at, (int, float)) or isinstance(at, bool)
                    or not math.isfinite(at) or at <= 0):
                continue
            unavailable = (item.get("active") is False or item.get("revoked") is True
                           or item.get("state") in {"revoked", "deleted", "superseded"})
            entry = {"category": category, "statement": statement[:100], "at": at,
                     "source": "confirmed_visible_reply"}
            if isinstance(item.get("phase3_confirmed_source"), dict):
                entry["confirmed_source"] = deepcopy(item["phase3_confirmed_source"])
            if category not in latest or (at, order) > latest[category][0]:
                latest[category] = ((at, order), None if unavailable else entry)
        return [v[1] for v in sorted(latest.values(), key=lambda x: x[0], reverse=True)
                if v[1] is not None][:limit]

    def prompt(self):
        items = self.current()
        if not items:
            return "[小鲸自身偏好]\n目前没有可召回的已表达偏好；可以从当下的好奇、联想或独立判断自主选题，不必假装已有固定兴趣。"
        return ("[小鲸自身偏好]\n以下只是已表达的近期偏好；送达不证明任何现实经历为真。不是畅的事实，也不是必须围绕它们选题的限制：\n"
                + "\n".join("- " + item["statement"] for item in items)
                + "\n可以自然延续，也可以产生新兴趣；当前偏好可以改变，不需要反复表达后才承认。")
