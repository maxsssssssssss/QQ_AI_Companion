"""Bounded image snapshots for the existing event and Provider context.

This is not a media store or downloader. AstrBot's native preprocessing owns
network download/path mapping; snapshots detach admitted local bytes from the
source event's cleanup lifetime. No pixels, paths or signed URLs enter receipts.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import io
from pathlib import Path
import stat
import time

from PIL import Image as PILImage

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_IMAGES = 8
MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}


class MediaUnavailable(ValueError):
    """Only a fixed reason is exposed, never a source or decoder exception."""


def source_kind(ref):
    if not isinstance(ref, str) or not ref:
        return 'missing'
    if ref.startswith(('data:', 'base64://')):
        return 'inline'
    if ref.startswith(('http://', 'https://')):
        return 'unresolved_remote'
    return 'local'


def snapshot_ref(ref, *, max_bytes=MAX_IMAGE_BYTES):
    """Capture original admitted bytes, validating format and original size.

    Run on a worker thread. Remote references remaining after native image
    preprocessing are explicitly unavailable; there is no hidden URL retry.
    """
    kind = source_kind(ref)
    if kind == 'missing':
        raise MediaUnavailable('missing_reference')
    if kind == 'unresolved_remote':
        raise MediaUnavailable('download_unavailable')
    if kind == 'inline':
        if ref.startswith('base64://'):
            encoded = ref[9:]
        else:
            header, separator, encoded = ref.partition(',')
            if not separator or not header.lower().endswith(';base64'):
                raise MediaUnavailable('invalid_encoding')
        if len(encoded) > ((max_bytes + 2) // 3) * 4:
            raise MediaUnavailable('size_limit')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise MediaUnavailable('invalid_encoding') from None
    else:
        from astrbot.core.utils.media_utils import file_uri_to_path
        try:
            path = Path(file_uri_to_path(ref))
            if not stat.S_ISREG(path.stat().st_mode):
                raise MediaUnavailable('invalid_source')
            with path.open('rb') as stream:
                data = stream.read(max_bytes + 1)
        except (OSError, ValueError) as exc:
            if isinstance(exc, MediaUnavailable):
                raise
            raise MediaUnavailable('source_unavailable') from None
    if len(data) > max_bytes:
        raise MediaUnavailable('size_limit')
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            fmt = image.format
            width, height = image.size
            frames = getattr(image, 'n_frames', 1)
            if fmt not in MIMES:
                raise MediaUnavailable('unsupported_format')
            if width <= 0 or height <= 0 or max(width, height) > 8192:
                raise MediaUnavailable('dimension_limit')
            image.verify()
    except MediaUnavailable:
        raise
    except Exception:
        raise MediaUnavailable('invalid_image') from None
    mime = MIMES[fmt]
    payload = 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')
    receipt = {'state': 'ready', 'source': kind, 'format': fmt, 'width': width,
               'height': height, 'frames': frames, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return payload, receipt


def unavailable_text(reason, *, current=True):
    scope = '当前消息的图片' if current else '历史消息中的图片'
    return f'[{scope}本次不可用（{reason}）；没有看到图像内容，不得猜测。可继续回答有依据的文字部分。]'


async def snapshot_components(components, *, timeout=3.0, max_images=MAX_IMAGES,
                              max_total_bytes=MAX_TOTAL_BYTES):
    """Return copied native components and safe ordered image receipts.

    Call after native preprocessing and admission, before waiting for grouping.
    Keep the returned components only in the existing turn/event lifecycle.
    Images inside a Reply remain inside that Reply; original components and
    original inbound archive are never rewritten.
    """
    from astrbot.api.message_components import Image, Plain, Reply
    deadline = time.monotonic() + timeout
    receipts = []
    used_bytes = 0

    async def clone_parts(parts, depth=0):
        nonlocal used_bytes
        result = []
        for part in parts:
            if isinstance(part, Image):
                ref = part.url or part.file
                try:
                    failure = getattr(part, 'phase3_media_failure', None)
                    if failure:
                        raise MediaUnavailable(failure)
                    if len(receipts) >= max_images:
                        raise MediaUnavailable('image_count_limit')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MediaUnavailable('snapshot_timeout')
                    payload, receipt = await asyncio.wait_for(asyncio.to_thread(
                        snapshot_ref, ref, max_bytes=min(MAX_IMAGE_BYTES, max(0, max_total_bytes-used_bytes))), remaining)
                    used_bytes += receipt['bytes']
                    # AstrBot accepts base64 components and persists the same
                    # image as a portable data URI through its native Provider.
                    result.append(Image.fromBase64(payload.partition(',')[2]))
                except (MediaUnavailable, TimeoutError) as exc:
                    reason = str(exc) if isinstance(exc, MediaUnavailable) else 'snapshot_timeout'
                    receipt = {'state': 'unavailable', 'reason': reason, 'source': source_kind(ref)}
                    result.append(Plain(unavailable_text(reason)))
                receipts.append({**receipt, 'index': len(receipts), 'quoted': depth > 0})
            elif isinstance(part, Reply) and part.chain and depth < 2:
                cloned = copy.deepcopy(part)
                cloned.chain = await clone_parts(part.chain, depth+1)
                result.append(cloned)
            else:
                result.append(copy.deepcopy(part))
        return result

    return await clone_parts(components), receipts


async def restore_current_images(messages, components, *, timeout=3.0):
    """Preserve admitted still-image pixels at the native Agent boundary.

    AstrBot 4.29.1 builds a <=512 KiB preview even at a large configured edge.
    Only replace the current user message's existing image parts when the
    ordered source mapping is exact. Keep native animation montages unchanged.
    No image-capability/caption decision is overridden, and no old turn changes.
    """
    from astrbot.api.message_components import Image, Reply
    from astrbot.core.agent.message import ImageURLPart
    current = next((m for m in reversed(messages) if m.role == 'user'), None)
    if current is None or not isinstance(current.content, list):
        return {'state': 'not_applicable', 'image_count': 0}
    indices = [i for i, p in enumerate(current.content) if isinstance(p, ImageURLPart)]
    if not indices:
        return {'state': 'not_applicable', 'image_count': 0}
    def refs(parts):
        for part in parts:
            if isinstance(part, Image):
                yield part.url or part.file
            elif isinstance(part, Reply) and part.chain:
                yield from refs(part.chain)
    # The pinned builder may place quoted images in extra content before direct
    # attachments. A count match alone is insufficient to prove that mapping.
    if any(isinstance(p, Reply) and p.chain and any(refs(p.chain)) for p in components):
        return {'state': 'kept_native', 'reason': 'quoted_image_mapping', 'image_count': len(indices)}
    sources = list(refs(components))
    if len(sources) != len(indices) or len(indices) > MAX_IMAGES:
        return {'state': 'kept_native', 'reason': 'source_mapping_mismatch', 'image_count': len(indices)}
    prepared, receipts = [], []
    deadline = time.monotonic() + timeout
    used = 0
    try:
        for ref in sources:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise MediaUnavailable('snapshot_timeout')
            payload, receipt = await asyncio.wait_for(asyncio.to_thread(
                snapshot_ref, ref, max_bytes=min(MAX_IMAGE_BYTES,max(0,MAX_TOTAL_BYTES-used))),remaining)
            used += receipt['bytes']
            if receipt['frames'] > 1:
                return {'state': 'kept_native', 'reason': 'native_animation_montage', 'image_count': len(indices)}
            prepared.append(ImageURLPart(image_url=ImageURLPart.ImageURL(url=payload)))
            receipts.append(receipt)
    except (MediaUnavailable, TimeoutError) as exc:
        reason = str(exc) if isinstance(exc,MediaUnavailable) else 'snapshot_timeout'
        return {'state': 'kept_native', 'reason': reason, 'image_count': len(indices)}
    content = list(current.content)
    for index, part in zip(indices,prepared):
        content[index] = part
    current.content = content
    return {'state': 'originals_restored', 'image_count': len(indices), 'images': receipts}
