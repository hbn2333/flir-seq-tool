import math
from pathlib import Path
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from flir_seq_tool.frame_transformer import FrameTransformer
from flir_seq_tool.reader import ScienceFileReader
from flir_seq_tool.video_encoder import decode_sync_args, discover_encoder, validate_video
from flir_seq_tool.video_export import VideoJob, build_video_request
from flir_seq_tool.video_renderer import VideoRenderer
from flir_seq_tool.viewer import ViewerSession


SMALL_SEQ = Path('raw/Rec-000366-240_09_26_37_152.seq')


@pytest.mark.integration
@pytest.mark.parametrize('speed,single', [(1, False), (5, False), (1, True)])
def test_real_seq_video_with_readonly_source_and_last_frame(tmp_path, speed, single):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg unavailable')
    before = SMALL_SEQ.stat()
    executable = discover_encoder()
    job = None
    target = tmp_path / f'real-{speed}-{single}.mp4'
    try:
        with ScienceFileReader(SMALL_SEQ) as reader:
            session = ViewerSession(reader, vmin=20, vmax=40)
            session.timeline.set_pending(10, 10 if single else 14)
            session.confirm_range()
            session.apply_transform(session.applied_transform.with_angle(-2.3))
            request = build_video_request(session, target, speed)
            times = [reader.timestamp_at(i) for i in range(request.start_frame, request.end_frame + 1)]
            duration = (times[-1] - times[0]).total_seconds()
            expected_count = 30 if single else math.ceil(duration * 30 / speed) + 1
            source = reader.temperature_frame_at(request.end_frame)
            expected_values = FrameTransformer(session.applied_transform).apply(source.temperature_c)
            with VideoRenderer(request.display, speed) as renderer:
                expected_rgb = renderer.render(expected_values, duration)
                if speed == 1 and not single:
                    preview = tmp_path / 'render-preview.png'
                    renderer.canvas.print_png(preview)
                    print(f'Video frame preview: {preview}')
        job = VideoJob(request)
        job.start()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and job.result is None:
            for message in job.poll():
                if message['kind'] == 'ready':
                    assert message['frame_count'] == expected_count
                    assert not target.exists()
                    job.confirm()
            time.sleep(.02)
        assert job.result is not None, 'Video job timed out'
        assert job.result['status'] == 'succeeded', job.result
        assert validate_video(executable, target, expected_count, threading.Event()) == expected_count
        # Decode only the last frame, retaining bounded output even for a longer source.
        decoded = subprocess.run([executable, '-v', 'error', '-i', str(target),
            '-vf', f'select=eq(n\\,{expected_count - 1})', *decode_sync_args(executable),
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], capture_output=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
        actual = np.frombuffer(decoded.stdout, np.uint8).reshape(1080, 1920, 3)
        assert np.abs(actual.astype(float) - expected_rgb).mean() < 5
        assert list(tmp_path.glob('*.partial.mp4')) == []
    finally:
        if job is not None:
            job.close()
        after = SMALL_SEQ.stat()
        assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
