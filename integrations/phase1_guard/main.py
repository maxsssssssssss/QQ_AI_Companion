"""Public AstrBot hooks for durable duplicate rejection and text dispatch.

The pinned AstrBot after_message_sent hook also runs when delivery fails. This
plugin therefore awaits the public event.send API itself for its narrow offline,
non-streaming text profile, and clears that result to prevent a second dispatch.
"""

import asyncio
import math

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools

from .store import GuardStore


class Phase1Guard(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.timeout = float(config.get("send_timeout_seconds", 2))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 30:
            raise ValueError("send_timeout_seconds must be finite and in (0, 30]")
        self.store = GuardStore(StarTools.get_data_dir("phase1_guard") / "guard.sqlite3")
        recovered = self.store.recover_interrupted()
        logger.info("Phase1Guard initialized; interrupted attempts marked unknown: %s", recovered)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1000000)
    async def claim_inbound(self, event: AstrMessageEvent):
        if event.get_platform_name() != "aiocqhttp":
            return
        try:
            message_id = getattr(event.message_obj, "message_id", None)
            if message_id is None or str(message_id) == "":
                raise ValueError("OneBot event has no message_id")
            key = (str(event.get_platform_id()), event.unified_msg_origin, str(message_id))
            raw = getattr(event.message_obj, "raw_message", {})
            chain = raw.get("message", []) if isinstance(raw, dict) else []
            accepted = self.store.claim(
                *key, str(event.get_sender_id()), event.message_str, chain
            )
        except Exception as exc:
            # Never print the message or raw exception; either can contain data.
            logger.error("Phase1Guard claim failed closed: %s", type(exc).__name__)
            event.stop_event()
            return
        if not accepted:
            event.stop_event()
            logger.info("Phase1Guard duplicate event stopped: %s", key[2])
            return
        event.set_extra("phase1_guard_key", key)

    @filter.on_decorating_result(priority=-1000000)
    async def dispatch_text(self, event: AstrMessageEvent):
        key = event.get_extra("phase1_guard_key")
        result = event.get_result()
        if not key or result is None or not result.chain:
            return
        try:
            chain = [part.toDict() for part in result.chain]
            attempt = self.store.begin_outbound(key, result.get_plain_text(), chain)
        except Exception as exc:
            # Upstream logs hook exceptions and would continue sending, so a
            # failure to persist the attempt must explicitly clear its result.
            event.clear_result()
            event.stop_event()
            logger.error("Phase1Guard delivery observation failed closed: %s", type(exc).__name__)
            return
        cfg = self.context.get_config(event.unified_msg_origin)
        segmented = cfg.get("platform_settings", {}).get("segmented_reply", {}).get("enable", False)
        streaming = cfg.get("provider_settings", {}).get("streaming_response", False)
        if segmented or streaming or not all(isinstance(part, Plain) for part in result.chain):
            # Keep complex sends in the upstream path; a pre-send hook is not
            # evidence of delivery. No success is inferred from a later hook.
            self.store.finish(attempt, "unknown", "UnsupportedDeliveryProfile")
            logger.warning("Phase1Guard left complex delivery to upstream; status unknown")
            return
        if not result.get_plain_text().strip():
            # The native adapter returns without an API call for blank text.
            self.store.finish(attempt, "failed", "EmptyText")
            event.clear_result()
            return
        try:
            await asyncio.wait_for(event.send(result), timeout=self.timeout)
        except TimeoutError:
            # Timeout means the remote peer might have accepted the message.
            self.store.finish(attempt, "unknown", "TimeoutError")
            logger.error("Phase1Guard delivery status unknown: TimeoutError")
        except asyncio.CancelledError:
            self.store.finish(attempt, "unknown", "CancelledError")
            raise
        except Exception as exc:
            self.store.finish(attempt, "failed", type(exc).__name__)
            logger.error("Phase1Guard delivery failed: %s", type(exc).__name__)
        else:
            self.store.finish(attempt, "sent")
        finally:
            # Ownership of this result ends here, including timeout/failure.
            # A retry here could duplicate a delivery with a lost acknowledgement.
            event.clear_result()

    async def terminate(self):
        self.store.close()
