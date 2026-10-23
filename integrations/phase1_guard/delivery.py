"""One durable send boundary, without retries or a second message transport."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import uuid
from weakref import WeakValueDictionary


@dataclass(frozen=True)
class DeliveryReceipt:
    confirmed: bool
    platform_message_id: str | None = None
    source: str = "native_adapter_ack"


@dataclass(frozen=True)
class DeliveryResult:
    delivery_id: str
    state: str
    platform_message_id: str | None = None
    error_type: str | None = None


class DeliveryService:
    def __init__(self, store, *, timeout=10.0):
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("send timeout must be in (0, 30]")
        self.store = store
        self.timeout = timeout
        # In-flight callers retain the lock; completed turns need no RAM entry.
        self._locks = WeakValueDictionary()

    async def send(self, *, turn_id, text, chain, sender, kind="normal", part_index=0,
                   delivery_id=None, is_current=lambda: True):
        """sender must return an explicit receipt; None is never proof of delivery.

        The AstrBot event adapter wrapper constructs a confirmed receipt only
        after the pinned adapter's awaited OneBot API has completed. The adapter
        discards remote IDs, so that path deliberately leaves the ID null.
        """
        if kind not in {"normal", "progress", "final", "proactive"}:
            raise ValueError("unknown delivery kind")
        if type(part_index) is not int or part_index < 0:
            raise ValueError("invalid part index")
        delivery_id = delivery_id or str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"qq-companion:{turn_id}:{kind}:{part_index}"))
        lock = self._locks.setdefault(turn_id, asyncio.Lock())
        async with lock:
            row, created = self.store.begin_delivery(
                delivery_id, turn_id, kind, part_index, text, chain)
            if not created:
                return self._result(row)
            if not is_current():
                self.store.finish_delivery(delivery_id, "failed", "SupersededBeforeSend")
            elif not text.strip() or not chain:
                self.store.finish_delivery(delivery_id, "failed", "EmptyText")
            else:
                try:
                    self.store.mark_bubble_transport(delivery_id)
                    receipt = await asyncio.wait_for(sender(), self.timeout)
                except TimeoutError:
                    self.store.finish_delivery(delivery_id, "unknown", "TimeoutError")
                except asyncio.CancelledError:
                    self.store.finish_delivery(delivery_id, "unknown", "CancelledError")
                    raise
                except Exception as exc:
                    # Only explicit native API rejection proves failure. Network
                    # errors may follow acceptance, so they are conservatively unknown.
                    state = "failed" if type(exc).__name__ in {"ActionFailed", "ApiNotAvailable"} else "unknown"
                    self.store.finish_delivery(delivery_id, state, type(exc).__name__)
                else:
                    if isinstance(receipt, DeliveryReceipt) and receipt.confirmed:
                        self.store.finish_delivery(delivery_id, "sent", receipt_source=receipt.source,
                                                   platform_message_id=receipt.platform_message_id)
                    elif isinstance(receipt, DeliveryReceipt):
                        self.store.finish_delivery(delivery_id, "failed", "AdapterRejected")
                    else:
                        self.store.finish_delivery(delivery_id, "unknown", "MissingReceipt")
            return self._result(self.store.get_delivery(delivery_id))

    @staticmethod
    def _result(row):
        return DeliveryResult(row["delivery_id"], row["state"],
                              row["platform_message_id"], row["error_type"])
