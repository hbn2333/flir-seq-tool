"""Frozen MP4 inputs and guarded publication; raw SEQ data is read-only."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import multiprocessing
import queue
import tempfile
import time

from .file_safety import DEFAULT_RAW_ROOT, TargetSnapshot, assert_snapshot, validate_write_path
from .selection import DisplaySettings, SourceIdentity, transform_to_dict, transform_from_dict
from .video_timeline import VIDEO_SPEEDS


@dataclass(frozen=True)
class VideoExportRequest:
    source_path: Path
    source: SourceIdentity
    start_frame: int
    end_frame: int
    transform_json: str
    display: DisplaySettings
    speed: int
    target_path: Path
    target_snapshot: TargetSnapshot

    def __post_init__(self):
        if type(self.speed) is not int or self.speed not in VIDEO_SPEEDS:
            raise ValueError("Video speed must be 1, 2, 5 or 10")
        if (type(self.start_frame) is not int or type(self.end_frame) is not int
                or not 0 <= self.start_frame <= self.end_frame < self.source.num_frames):
            raise ValueError("Invalid confirmed video frame range")
        if self.target_path.suffix.lower() != '.mp4':
            raise ValueError("Use a .mp4 filename")
        transform_from_dict(json.loads(self.transform_json), self.source)


def build_video_request(session, target, speed):
    source_path = Path(session.reader.path).resolve()
    target = validate_write_path(target, source_path=source_path, raw_root=DEFAULT_RAW_ROOT)
    limits = session.color_limits
    if limits is None:
        raise RuntimeError("Viewer color limits are not initialized")
    start, end = session.measure_source_range()
    return VideoExportRequest(source_path, session.source_identity(), start, end,
        json.dumps(transform_to_dict(session.applied_transform), sort_keys=True, allow_nan=False),
        DisplaySettings(session.colormap, limits.vmin, limits.vmax), speed,
        target, TargetSnapshot.capture(target))


def assert_request_current(request):
    validate_write_path(request.target_path, source_path=request.source_path, raw_root=DEFAULT_RAW_ROOT)
    assert_snapshot(request.target_path, request.target_snapshot)
    stat = request.source_path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (request.source.size_bytes, request.source.modified_time_ns):
        raise RuntimeError("Source SEQ changed during MP4 export")


def publish_video(request, partial):
    assert_request_current(request)
    os.replace(partial, request.target_path)


def _reader_identity(reader):
    stat = reader.path.stat()
    return SourceIdentity(reader.path.name, stat.st_size, stat.st_mtime_ns,
        reader.width, reader.height, reader.num_frames,
        reader.timestamp_at(0), reader.timestamp_at(reader.num_frames - 1))


def _video_worker(request, messages, commands, cancel, gate, publishing, publish_lock):
    # Heavy imports and SDK initialization belong to the isolated worker.
    from .reader import ScienceFileReader
    from .frame_transformer import FrameTransformer
    from .selection import validate_source_identity
    from .video_encoder import VideoCancelled, VideoEncoder, check_cancel, discover_encoder, validate_video
    from .video_renderer import VideoRenderer
    from .video_timeline import VideoTimeline

    partial = None
    last_progress = 0.0

    def send(kind, **data):
        messages.put(dict(kind=kind, **data), timeout=3)

    def progress(stage, current=0, total=0):
        nonlocal last_progress
        now = time.monotonic()
        if current != total and now - last_progress < .1:
            return
        last_progress = now
        try:
            messages.put_nowait(dict(kind="progress", stage=stage, current=current, total=total))
        except queue.Full:
            pass

    result = dict(status="failed", message="MP4 worker did not complete")
    try:
        gate.wait()
        check_cancel(cancel)
        executable = discover_encoder()
        assert_request_current(request)
        with ScienceFileReader(request.source_path) as reader:
            validate_source_identity(request.source, _reader_identity(reader))
            timestamps = []
            count = request.end_frame - request.start_frame + 1
            for local, index in enumerate(range(request.start_frame, request.end_frame + 1)):
                check_cancel(cancel)
                timestamps.append(reader.timestamp_at(index))
                progress("scanning", local + 1, count)
            timeline = VideoTimeline.from_timestamps(timestamps, request.speed)
            summary = (f"Target: {request.target_path}\n"
                f"Overwrite existing MP4: {'YES' if request.target_snapshot.exists else 'No'}\n"
                f"Confirmed source frames: {request.start_frame}–{request.end_frame}\n"
                f"Source duration: {timeline.duration_s:.6f} s | Speed: {request.speed}×\n"
                f"Video duration: {timeline.video_duration_s:.6f} s ({timeline.frame_count} frames)\n"
                f"1920×1080 | 30 fps | H.264 | {request.display.colormap}\n"
                f"Fixed scale: {request.display.vmin_c:g}–{request.display.vmax_c:g} °C\n"
                + ("Single source frame: 1 second still video.\n" if count == 1 else "")
                + "Presentation video may skip/repeat source frames; it does not replace temperature data.\nExport?")
            send("ready", summary=summary, frame_count=timeline.frame_count,
                 duration_s=timeline.duration_s, video_duration_s=timeline.video_duration_s)
            while True:
                check_cancel(cancel)
                try:
                    partial = Path(commands.get(timeout=.1))
                    break
                except queue.Empty:
                    continue
            assert_request_current(request)
            transformer = FrameTransformer(transform_from_dict(json.loads(request.transform_json), request.source))
            previous = None
            rgb = None
            with VideoRenderer(request.display, request.speed) as renderer, VideoEncoder(executable, partial, cancel) as encoder:
                for output_index, local in enumerate(timeline.iter_source_indices()):
                    check_cancel(cancel)
                    if local != previous:
                        frame = reader.temperature_frame_at(request.start_frame + local)
                        if frame.timestamp != timestamps[local]:
                            raise RuntimeError("Source timestamp changed during MP4 export")
                        values = transformer.apply(frame.temperature_c)
                        check_cancel(cancel)
                        rgb = renderer.render(values, timeline.relative_us[local] / 1_000_000)
                        previous = local
                    encoder.write(rgb)
                    progress("encoding", output_index + 1, timeline.frame_count)
                encoder.finish()
            progress("validating")
            validate_video(executable, partial, timeline.frame_count, cancel)
            check_cancel(cancel)
            validate_source_identity(request.source, _reader_identity(reader))
        assert_request_current(request)
        with publish_lock:
            check_cancel(cancel)
            publishing.set()
        progress("publishing")
        publish_video(request, partial)
        partial = None
        result = dict(status="succeeded", message=f"MP4 exported: {request.target_path}")
    except VideoCancelled:
        result = dict(status="cancelled", message="MP4 export cancelled")
    except Exception as exc:
        result = dict(status="failed", message=f"MP4 export failed: {exc}")
    finally:
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError as exc:
                result["message"] += f"\nTemporary file remains: {partial}: {exc}"
        send("result", **result)


class VideoJob:
    """One spawn worker, a bounded event queue and a parent-owned output temporary."""

    def __init__(self, request, *, worker=_video_worker):
        context = multiprocessing.get_context("spawn")
        self.request = request
        self.messages = context.Queue(maxsize=64)
        self.commands = context.Queue(maxsize=1)
        self.cancel_event = context.Event()
        self.gate = context.Event()
        self.publishing = context.Event()
        self.publish_lock = context.Lock()
        self.process = context.Process(target=worker,
            args=(request, self.messages, self.commands, self.cancel_event, self.gate, self.publishing, self.publish_lock),
            daemon=False)
        self.tree = None
        self.partial = None
        self.result = None
        self.cancel_time = None
        self.started = self.closed = self.confirmed = False

    @property
    def cancel_allowed(self):
        return self.is_alive() and not self.publishing.is_set()

    def start(self):
        from .video_process import ProcessTree
        try:
            self.tree = ProcessTree()
            self.process.start()
            self.started = True
            self.tree.assign(self.process)
            self.gate.set()
        except Exception:
            self.close()
            raise

    def is_alive(self):
        return self.started and not self.closed and self.process.is_alive()

    def confirm(self):
        if self.confirmed or not self.is_alive() or self.cancel_event.is_set():
            return
        assert_request_current(self.request)
        self.request.target_path.parent.mkdir(parents=True, exist_ok=True)
        # The parent owns this exact file even if the worker subsequently crashes.
        fd, name = tempfile.mkstemp(prefix=self.request.target_path.stem + '.',
            suffix='.partial.mp4', dir=self.request.target_path.parent)
        os.close(fd)
        self.partial = Path(name)
        self.commands.put_nowait(str(self.partial))
        self.confirmed = True

    def cancel(self):
        if not self.publish_lock.acquire(block=False):
            return False
        try:
            if self.publishing.is_set():
                return False
            self.cancel_event.set()
            if self.cancel_time is None:
                self.cancel_time = time.monotonic()
            return True
        finally:
            self.publish_lock.release()

    def _cleanup_partial(self):
        if self.partial is not None:
            try:
                self.partial.unlink(missing_ok=True)
            except OSError as exc:
                if self.result is not None:
                    self.result['message'] += f"\nTemporary file remains: {self.partial}: {exc}"
            self.partial = None

    def poll(self):
        if self.closed:
            return []
        if self.cancel_time is not None and self.is_alive() and time.monotonic() - self.cancel_time > 5:
            self._stop_process()
        events = []
        while True:
            try:
                event = self.messages.get_nowait()
            except queue.Empty:
                break
            events.append(event)
            if event['kind'] == 'result':
                self.result = event
        if self.started and not self.process.is_alive():
            # A dead worker has flushed its Queue feeder before normal exit.
            if self.result is None:
                cancelled = self.cancel_event.is_set()
                self.result = dict(kind='result', status='cancelled' if cancelled else 'failed',
                    message=("MP4 export cancelled; unresponsive worker stopped" if cancelled else
                             f"MP4 worker exited without a result ({self.process.exitcode})"))
                events.append(self.result)
            self._cleanup_partial()
        return events

    def _stop_process(self):
        if self.tree is not None:
            self.tree.close()
        if self.started and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=2)

    def close(self):
        if self.closed:
            return
        if self.started:
            self.cancel()
            self.process.join(timeout=1)
        self._stop_process()
        self._cleanup_partial()
        for channel in (self.messages, self.commands):
            channel.cancel_join_thread()
            channel.close()
        self.closed = True
