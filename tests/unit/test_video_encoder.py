from pathlib import Path
import shutil
import threading
import sys
import time

import numpy as np
import pytest

from flir_seq_tool.video_encoder import (
    VideoCancelled, VideoEncoder, discover_encoder, encode_args, validate_video,
)


def test_encoding_arguments_preserve_path():
    target = Path('with spaces/movie.partial.mp4')
    args = encode_args('ffmpeg', target)
    assert args[-1] == str(target)
    assert args[args.index('-c:v') + 1] == 'libx264'
    assert args[args.index('-s:v') + 1] == '1920x1080'


def test_discovery_missing(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda _: None)
    with pytest.raises(RuntimeError, match='FFmpeg'):
        discover_encoder()


@pytest.fixture
def encoder():
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg unavailable')
    return discover_encoder()


def test_real_encoder_full_decode_and_wrong_count(encoder, tmp_path):
    target = tmp_path / 'movie.mp4'
    event = threading.Event()
    with VideoEncoder(encoder, target, event) as output:
        output.write(np.zeros((1080, 1920, 3), dtype=np.uint8))
        output.write(np.full((1080, 1920, 3), 255, dtype=np.uint8))
        output.finish()
    assert validate_video(encoder, target, 2, event) == 2
    with pytest.raises(RuntimeError, match='frame count'):
        validate_video(encoder, target, 3, event)


def test_corrupt_decode(encoder, tmp_path):
    target = tmp_path / 'corrupt.mp4'
    target.write_bytes(b'not a video')
    with pytest.raises(RuntimeError):
        validate_video(encoder, target, 2, threading.Event())


def test_pre_cancel_creates_no_file(encoder, tmp_path):
    event = threading.Event()
    event.set()
    target = tmp_path / 'cancelled.mp4'
    with pytest.raises(VideoCancelled):
        with VideoEncoder(encoder, target, event):
            pass
    assert not target.exists()


def test_blocked_pipe_cancellation_reaps_encoder(monkeypatch, tmp_path):
    import flir_seq_tool.video_encoder as module
    monkeypatch.setattr(module, 'encode_args', lambda *args: [sys.executable, '-c',
        'import sys,time; sys.stderr.write("x"*200000); sys.stderr.flush(); time.sleep(60)'])
    event = threading.Event()
    timer = threading.Timer(.3, event.set)
    start = time.monotonic()
    timer.start()
    try:
        with pytest.raises(VideoCancelled):
            with VideoEncoder(sys.executable, tmp_path / 'unused.mp4', event) as output:
                process = output.pipe.process
                output.write(np.zeros((1080, 1920, 3), dtype=np.uint8))
        assert process.poll() is not None
        assert len(output.pipe.tail) <= 65536
        assert not output.pipe.monitor.is_alive()
        assert not output.pipe.stderr_thread.is_alive()
        assert time.monotonic() - start < 5
    finally:
        timer.cancel()
