"""Bounded input grouping only; AstrBot still owns the runner and session lock.

The pinned PrivateCompanion buffer truncates individual text to 260 characters
and deduplicates equal text. This bridge keeps exact inbound IDs/text instead.
No model, routing classifier, background scheduler, or retry queue lives here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import time
import uuid


@dataclass
class Turn:
    turn_id: str
    platform: str
    session: str
    leader_id: str
    source_ids: list[str]
    texts: list[str]
    created: float
    updated: float
    phase: str = "collecting"
    cancelled: bool = False
    predecessor: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    waiter_id: str | None = None


class TurnCoordinator:
    def __init__(self, store, *, window=0.8, max_wait=3.0, max_messages=8, clock=time.monotonic):
        if not all(math.isfinite(x) for x in (window, max_wait)) or not 0 <= window <= max_wait <= 15:
            raise ValueError("invalid grouping time bounds")
        if type(max_messages) is not int or not 1 <= max_messages <= 32:
            raise ValueError("invalid grouping size")
        self.store = store
        self.window, self.max_wait, self.max_messages = window, max_wait, max_messages
        self.clock = clock
        self.current = {}
        self.by_id = {}
        self.message_turn = {}

    def prune(self):
        """Bound volatile bookkeeping; permanent sources remain in SQLite.

        A crashed/error-only runner must not leave a session busy indefinitely.
        Expiration fails closed for any later transport attempt from that event.
        """
        now = self.clock()
        for turn in list(self.by_id.values()):
            if turn.phase in {"collecting", "ready", "generating"} and now - turn.updated > 900:
                self.cancel(turn.turn_id)
            if turn.phase in {"done", "superseded", "cancelled"} and now - turn.updated > 300:
                self.by_id.pop(turn.turn_id, None)
                identity = (turn.platform, turn.session)
                if self.current.get(identity) is turn:
                    self.current.pop(identity, None)
        self.message_turn = {key: value for key, value in self.message_turn.items() if value in self.by_id}

    def observe(self, key, text, *, groupable=True):
        """Return (turn, superseded): caller uses native Agent cancellation once.

        A new input during generation invalidates old output and carries its
        source messages into the successor. Already acknowledged output is kept
        as history, never unsent. No semantic guess or external classifier.
        """
        platform, session, message_id = key
        self.prune()
        identity = (platform, session)
        now = self.clock()
        previous = self.current.get(identity)
        if key in self.message_turn:
            return self.by_id[self.message_turn[key]], False
        # All events already admitted before the leader resumes belong to its
        # cohort. Reaching a limit wakes that leader; it must not orphan events
        # that arrive at the deadline while the event loop is busy.
        collecting = bool(previous and previous.phase == "collecting" and groupable)
        if collecting:
            turn = previous
            turn.source_ids.append(message_id)
            turn.texts.append(text)
            turn.updated = now
            self.store.attach_source(turn.turn_id, key)
            turn.changed.set()
            superseded = False
        else:
            # A non-groupable event can interrupt an in-progress collection
            # too. Leaving that leader alive would allow two current replies
            # and could detach the media-bearing successor from earlier input.
            superseded = bool(previous and previous.phase in {"collecting", "ready", "generating"})
            ids, texts = [], []
            if superseded:
                previous.phase = "superseded"
                self.store.set_turn_state(previous.turn_id, "superseded")
                previous.changed.set()
                # Carry the facts of incomplete input, never speculative output.
                ids, texts = list(previous.source_ids), list(previous.texts)
            turn = Turn(str(uuid.uuid4()), platform, session, message_id,
                        ids + [message_id], texts + [text], now, now,
                        predecessor=previous.turn_id if superseded else None)
            if not groupable:
                turn.created = now - self.max_wait
            self.store.create_turn(turn.turn_id, platform, session, turn.source_ids,
                                   predecessor=turn.predecessor)
            self.current[identity] = turn
            self.by_id[turn.turn_id] = turn
        # Only the new event is re-bound; old events must remain invalid.
        self.message_turn[key] = turn.turn_id
        return turn, superseded

    async def wait(self, key, *, claim_leadership=False):
        turn = self.by_id[self.message_turn[key]]
        if claim_leadership:
            # Queue admission precedes independent native media preparation.
            # The first prepared event owns the existing cohort's one waiter;
            # source order stays in source_ids, independent of this choice.
            if turn.phase != "collecting" or turn.waiter_id is not None:
                return None
            turn.waiter_id = key[2]
            turn.leader_id = key[2]
        elif key[2] != turn.leader_id:
            return None
        try:
            while turn.phase == "collecting":
                deadline = min(turn.updated + self.window, turn.created + self.max_wait)
                if len(turn.source_ids) >= self.max_messages or self.clock() >= deadline:
                    break
                turn.changed.clear()
                try:
                    await asyncio.wait_for(turn.changed.wait(), max(0.001, deadline - self.clock()))
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            # A cancelled collector cannot leave every later event classified
            # as its follower. Preserve durable originals; a new input can
            # start a fresh valid turn without reviving this cancelled output.
            if claim_leadership and turn.phase == "collecting" and turn.waiter_id == key[2]:
                self.cancel(turn.turn_id)
                turn.waiter_id = None
            raise
        if turn.phase != "collecting":
            return None
        turn.phase = "ready"
        self.store.set_turn_state(turn.turn_id, "ready")
        return turn

    def generating(self, turn_id):
        turn = self.by_id[turn_id]
        if not self.is_current(turn_id):
            return False
        turn.phase = "generating"
        self.store.set_turn_state(turn_id, "generating")
        return True

    def is_current(self, turn_id):
        self.prune()
        turn = self.by_id.get(turn_id)
        return bool(turn and self.current.get((turn.platform,turn.session)) is turn
                    and not turn.cancelled and turn.phase in {"collecting", "ready", "generating"})

    def complete(self, turn_id):
        turn = self.by_id.get(turn_id)
        if turn and turn.phase not in {"superseded", "cancelled"}:
            turn.phase = "done"
            self.store.set_turn_state(turn_id, "done")

    def cancel(self, turn_id):
        turn = self.by_id.get(turn_id)
        if turn:
            turn.cancelled = True
            turn.phase = "cancelled"
            turn.changed.set()
            self.store.set_turn_state(turn_id, "cancelled")
