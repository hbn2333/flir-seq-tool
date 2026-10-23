import importlib
import multiprocessing
import queue
import sys
import time
import os
import subprocess
import threading
from types import SimpleNamespace

import pytest

from flir_seq_tool.video_export import VideoJob


def test_main_module_is_import_safe():
    sys.modules.pop('flir_seq_tool.__main__', None)
    importlib.import_module('flir_seq_tool.__main__')


def waiting_worker(request, messages, commands, cancel, gate, publishing, publish_lock):
    gate.wait()
    messages.put({'kind': 'ready', 'summary': 'ready'})
    while not cancel.wait(.02):
        pass
    messages.put({'kind': 'result', 'status': 'cancelled', 'message': 'cancelled'})


def crashed_worker(request, messages, commands, cancel, gate, publishing, publish_lock):
    gate.wait()
    raise RuntimeError('intentional worker failure')


def test_spawn_cancel_and_resource_cleanup():
    job = VideoJob(None, worker=waiting_worker)
    job.start()
    deadline = time.monotonic() + 10
    ready = False
    while time.monotonic() < deadline and not ready:
        ready = any(m['kind'] == 'ready' for m in job.poll())
        time.sleep(.02)
    assert ready
    assert job.cancel()
    while time.monotonic() < deadline and job.is_alive():
        job.poll()
        time.sleep(.02)
    messages = job.poll()
    assert job.result['status'] == 'cancelled'
    job.close()
    assert not job.is_alive()


def test_worker_crash_gets_result():
    job = VideoJob(None, worker=crashed_worker)
    job.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and job.result is None:
        job.poll()
        time.sleep(.02)
    assert job.result['status'] == 'failed'
    job.close()


def hung_worker(request, messages, commands, cancel, gate, publishing, publish_lock):
    gate.wait()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    messages.put(dict(kind='ready', pid=child.pid))
    time.sleep(60)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object ownership')
def test_force_cancel_reaps_worker_and_its_encoder():
    import ctypes
    from ctypes import wintypes
    job = VideoJob(None, worker=hung_worker)
    job.start()
    handle = None
    try:
        deadline = time.monotonic() + 10
        pid = None
        while pid is None and time.monotonic() < deadline:
            for event in job.poll():
                if event['kind'] == 'ready':
                    pid = event['pid']
            time.sleep(.02)
        assert pid is not None
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
        assert handle
        assert job.cancel()
        while job.result is None and time.monotonic() < deadline:
            job.poll()
            time.sleep(.02)
        assert job.result['status'] == 'cancelled'
        assert not job.is_alive()
        assert kernel.WaitForSingleObject(handle, 2000) == 0
    finally:
        job.close()
        if handle:
            kernel.CloseHandle(handle)


def test_exit_race_drains_result_after_worker_is_observed_dead():
    job = VideoJob(None)
    job.messages.close()
    messages = queue.Queue()
    job.messages = messages
    def observe_death():
        messages.put(dict(kind='result', status='succeeded', message='done'))
        return False
    job.process = SimpleNamespace(is_alive=observe_death, exitcode=0)
    job.started = True
    events = job.poll()
    assert job.result['status'] == 'succeeded'
    assert [event['status'] for event in events if event['kind'] == 'result'] == ['succeeded']
    job.commands.close()


def test_required_control_survives_queue_backpressure():
    from flir_seq_tool.video_export import send_control
    messages = queue.Queue(maxsize=1)
    messages.put(dict(kind='progress'))
    result = dict(kind='result', status='succeeded')
    done = threading.Event()
    def send():
        send_control(messages, result, threading.Event())
        done.set()
    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    assert not done.wait(.1)
    assert messages.get()['kind'] == 'progress'
    assert done.wait(1)
    assert messages.get() == result
    thread.join(timeout=1)


def held_event_worker(request, messages, commands, cancel, gate, publishing, publish_lock):
    gate.wait()
    messages.put(dict(kind='ready'))
    cancel.wait()
    # Model termination inside Event.is_set(): this condition is not robust
    # against the owning process being killed while it holds the lock.
    with cancel._cond:
        messages.put(dict(kind='progress', stage='holding-event-lock'))
        time.sleep(60)


def test_force_cancel_does_not_read_abandoned_event_lock():
    job = VideoJob(None, worker=held_event_worker)
    job.start()
    observer = None
    try:
        deadline = time.monotonic() + 10
        locked = False
        while time.monotonic() < deadline and not locked:
            for event in job.poll():
                if event['kind'] == 'ready':
                    job.cancel()
                if event.get('stage') == 'holding-event-lock':
                    locked = True
            time.sleep(.01)
        assert locked
        job.cancel_time = time.monotonic() - 6
        observer = threading.Thread(target=job.poll, daemon=True)
        observer.start()
        observer.join(timeout=3)
        assert not observer.is_alive(), 'poll blocked on an Event lock abandoned by terminated worker'
        assert job.result['status'] == 'cancelled'
        job.close()
    finally:
        if observer is not None and observer.is_alive():
            job.started = False  # Let the pre-fix failing test clean parent handles.
        job.close()
