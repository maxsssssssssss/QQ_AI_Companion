"""Bound only the authorized event's existing native image downloader.

No network-library/class patch: the installed handle wraps one event Queue
instance and creates Image-compatible components for the approved private chat.
The original raw OneBot event stays untouched for Recorder/Guard provenance.
"""
import asyncio
import inspect
from pathlib import Path
import time

from pydantic.v1 import PrivateAttr
from astrbot.api.message_components import Image, Reply
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .media import MediaUnavailable,snapshot_ref


class BoundedImage(Image):
    _budget = PrivateAttr(default=None)
    _failure = PrivateAttr(default=None)

    @property
    def phase3_media_failure(self):
        return self._failure

    async def convert_to_file_path(self):
        if self._failure:
            raise MediaUnavailable(self._failure)
        if self._budget['deadline'] is None:
            self._budget['deadline'] = time.monotonic()+self._budget['timeout']
        remaining=self._budget['deadline']-time.monotonic()
        source=self.url or self.file or ''
        path=None
        owned=False
        try:
            if remaining<=0:
                raise TimeoutError
            path=await asyncio.wait_for(Image.convert_to_file_path(self),remaining)
            # Native to_path hands ownership to its caller. Validation below
            # happens before PreProcess can register that newly created file.
            if source.startswith(('https://','http://','base64://','data:')):
                try:
                    Path(path).resolve().relative_to(Path(get_astrbot_temp_path()).resolve())
                    owned=True
                except (OSError,ValueError):
                    pass
            remaining=self._budget['deadline']-time.monotonic()
            if remaining<=0:
                raise TimeoutError
            # Validate before native normalization can turn an invalid format
            # into an indistinguishable download failure. Bytes stay private.
            await asyncio.wait_for(asyncio.to_thread(snapshot_ref,path),remaining)
            return path
        except asyncio.CancelledError:
            if owned and path:
                Path(path).unlink(missing_ok=True)
            raise
        except Exception as exc:
            if owned and path:
                Path(path).unlink(missing_ok=True)
            self._failure=(str(exc) if isinstance(exc,MediaUnavailable) else
                'download_timeout' if isinstance(exc,TimeoutError) else
                'source_unavailable' if isinstance(exc,FileNotFoundError) else 'download_unavailable')
            # Native preprocessing catches this exception. Removing this stale
            # working reference prevents a second download during collection;
            # the raw inbound message retains the original source separately.
            self.file=self.url=self.path=''
            raise MediaUnavailable(self._failure) from None


class DownloadHandle:
    def __init__(self,queue,platform_id,owner_id,timeout,on_admit):
        self.queue=queue;self.original=queue.get
        self.had_instance_method='get' in vars(queue)
        self.platform_id=str(platform_id);self.owner_id=str(owner_id);self.timeout=timeout
        self.on_admit=on_admit;self.ordinal=0
        async def get():
            while True:
                event=await self.original()
                if not (isinstance(event,AstrMessageEvent) and event.is_private_chat()
                    and str(event.get_platform_id())==self.platform_id
                    and str(event.get_sender_id())==self.owner_id):
                    return event
                self.ordinal+=1
                event.set_extra('phase3_ingress_order',self.ordinal)
                event.set_extra('phase3_received_monotonic',time.monotonic())
                event.set_extra('phase3_received_at',time.time())
                # Synchronous registration before native preprocessing preserves
                # original arrival order without holding an async ingress lock.
                if self.on_admit is not None:
                    try:
                        admitted=self.on_admit(event)
                        if inspect.isawaitable(admitted):
                            if inspect.iscoroutine(admitted):admitted.close()
                            raise TypeError('media_admission_must_be_synchronous')
                    except Exception as exc:
                        logger.error('Phase3 media admission failed closed: %s',type(exc).__name__)
                        admitted=False
                    if admitted is False:
                        event.stop_event()
                        # Native EventBus never calls task_done. Do not change
                        # its queue accounting convention or launch a rejected
                        # duplicate event's preprocessing/download work.
                        continue
                budget={'deadline':None,'timeout':self.timeout}
                def adapt(parts,depth=0):
                    result=[]
                    for part in parts:
                        if isinstance(part,Image) and not isinstance(part,BoundedImage):
                            image=BoundedImage(**part.dict())
                            image._budget=budget;result.append(image)
                        elif isinstance(part,Reply) and part.chain and depth<2:
                            cloned=part.copy(deep=True);cloned.chain=adapt(part.chain,depth+1);result.append(cloned)
                        else:result.append(part)
                    return result
                event.message_obj.message=adapt(event.message_obj.message)
                event.set_extra('phase3_media_download_bound',self.timeout)
                return event
        self.wrapper=get;queue.get=get

    def restore(self):
        if self.queue.get is self.wrapper:
            if self.had_instance_method:self.queue.get=self.original
            else:del self.queue.get
        if getattr(self.queue,'_phase3_media_download_handle',None) is self:
            del self.queue._phase3_media_download_handle


def install(queue,*,platform_id,owner_id,timeout=4.0,on_admit=None):
    if not .01<=timeout<=10 or not str(platform_id) or not str(owner_id):
        raise ValueError('invalid_media_download_scope')
    if on_admit is not None and (not callable(on_admit) or inspect.iscoroutinefunction(on_admit)):
        raise ValueError('media_admission_must_be_synchronous')
    existing=getattr(queue,'_phase3_media_download_handle',None)
    if isinstance(existing,DownloadHandle) and queue.get is existing.wrapper:
        if (existing.platform_id,existing.owner_id,existing.timeout,existing.on_admit)!=(str(platform_id),str(owner_id),timeout,on_admit):
            raise ValueError('conflicting_media_download_scope')
        return existing
    if not isinstance(queue,asyncio.Queue) or getattr(queue.get,'__func__',None) is not asyncio.Queue.get:
        return None
    handle=DownloadHandle(queue,platform_id,owner_id,timeout,on_admit)
    queue._phase3_media_download_handle=handle
    return handle
