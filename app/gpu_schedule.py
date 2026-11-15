"""Cooperative GPU handoff: finish the current generation, then serve voice first."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time

from .config import state_home


class SummaryDeferred(ValueError):
    pass


def try_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b')
    if not path.stat().st_size:handle.write(b'1');handle.flush()
    try:
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handle
    except OSError as error:
        handle.close()
        if error.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):return None
        raise


def voice_pending(home=None):
    from .store import Store
    store=Store(home or state_home())
    return any(j['status'] in ('queued','running') and not j['cancel_requested']
               and not (store.home/'work'/j['id']/'result.json').exists() for j in store.jobs())


def unload_notes():
    from .local_notes import request
    receipts=[]
    for port in (11434,11435):
        try:models=request('/api/ps',timeout=3,port=port).get('models',[])
        except OSError:continue
        for model in models:
            name=model.get('name') or model.get('model')
            if name!='qwen3.5:4b' or model.get('remote_host'):continue
            request('/api/generate',{'model':name,'keep_alive':0},timeout=60,port=port)
            remaining=request('/api/ps',timeout=5,port=port).get('models',[])
            if any((m.get('name') or m.get('model'))==name and m.get('size_vram',0)>0 for m in remaining):
                raise ValueError('Qwen이 GPU 메모리를 반환하지 않았습니다. 진행 중인 로컬 요약을 확인하세요.')
            receipts.append({'port':port,'model':name,'unloaded':True})
    return receipts


@contextmanager
def voice_session(home=None,timeout=120):
    home=Path(home or state_home());deadline=time.monotonic()+timeout
    priority=resource=None
    try:
        while priority is None:
            priority=try_lock(home/'gpu-voice-priority.lock')
            if priority is None:
                if time.monotonic()>deadline:raise ValueError('다른 음성 GPU 작업이 진행 중입니다. 잠시 후 재시도하세요.')
                time.sleep(.2)
        while resource is None:
            resource=try_lock(home/'gpu-resource.lock')
            if resource is None:
                if time.monotonic()>deadline:raise ValueError('로컬 요약의 현재 생성이 끝나기를 기다리고 있습니다. 전사는 보존했습니다.')
                time.sleep(.2)
        unload_notes()
        yield
    finally:
        if resource:resource.close()
        if priority:priority.close()


@contextmanager
def summary_session(home=None,timeout=120):
    home=Path(home or state_home());deadline=time.monotonic()+timeout
    resource=None
    try:
        while resource is None:
            if voice_pending(home):raise SummaryDeferred('음성 전사 우선 · 로컬 요약은 전사 후 이어집니다.')
            priority=try_lock(home/'gpu-voice-priority.lock')
            if priority is None:raise SummaryDeferred('음성 GPU 작업 우선 · 로컬 요약 대기')
            try:resource=try_lock(home/'gpu-resource.lock')
            finally:priority.close()
            if resource is None:
                if time.monotonic()>deadline:raise ValueError('GPU 작업이 끝나기를 기다리고 있습니다.')
                time.sleep(.2)
        yield
    finally:
        if resource:resource.close()
