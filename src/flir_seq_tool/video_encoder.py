"""Bounded-memory FFmpeg pipes with cancellation and full-frame verification."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

import numpy as np

from .video_renderer import VIDEO_HEIGHT, VIDEO_WIDTH


class VideoCancelled(Exception):
    pass


def check_cancel(event):
    if event.is_set():
        raise VideoCancelled("MP4 export cancelled")


def _flags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def discover_encoder():
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("FFmpeg not found on PATH; install FFmpeg with libx264 to export MP4")
    try:
        result = subprocess.run([executable, "-hide_banner", "-encoders"],
            capture_output=True, timeout=10, creationflags=_flags(), shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"FFmpeg encoder check failed: {exc}") from exc
    names = [line.split()[1] for line in result.stdout.decode(errors="replace").splitlines()
             if len(line.split()) >= 2]
    if result.returncode or "libx264" not in names:
        raise RuntimeError("FFmpeg must provide the libx264 H.264 encoder")
    return str(Path(executable).resolve())


def encode_args(executable, target):
    return [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", "1920x1080",
            "-r", "30", "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(target)]


def _terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


class _PipeProcess:
    def __init__(self, args, cancel_event, *, decoding=False):
        check_cancel(cancel_event)
        self.cancel_event = cancel_event
        self.tail = bytearray()
        self.stopped = threading.Event()
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL if decoding else subprocess.PIPE,
            stdout=subprocess.PIPE if decoding else subprocess.DEVNULL,
            stderr=subprocess.PIPE, shell=False, creationflags=_flags(), bufsize=0)
        self.stderr_thread = threading.Thread(target=self._drain, daemon=True)
        self.monitor = threading.Thread(target=self._watch_cancel, daemon=True)
        self.stderr_thread.start()
        self.monitor.start()

    def _drain(self):
        while data := self.process.stderr.read(4096):
            self.tail.extend(data)
            del self.tail[:-65536]

    def _watch_cancel(self):
        while not self.stopped.wait(0.05):
            if self.cancel_event.is_set():
                _terminate(self.process)
                return

    def wait(self):
        code = self.process.wait()
        self.stderr_thread.join(timeout=3)
        check_cancel(self.cancel_event)
        if code:
            raise RuntimeError(f"FFmpeg failed ({code}): {self.tail.decode(errors='replace')}")

    def close(self):
        _terminate(self.process)
        self.stopped.set()
        self.monitor.join(timeout=3)
        self.stderr_thread.join(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


class VideoEncoder:
    def __init__(self, executable, target, cancel_event):
        self.executable, self.target, self.cancel_event = executable, target, cancel_event
        self.pipe = None

    def __enter__(self):
        self.pipe = _PipeProcess(encode_args(self.executable, self.target), self.cancel_event)
        return self

    def write(self, rgb):
        check_cancel(self.cancel_event)
        if rgb.shape != (VIDEO_HEIGHT, VIDEO_WIDTH, 3) or rgb.dtype != np.uint8:
            raise ValueError("Expected a 1920x1080 RGB uint8 video frame")
        data = memoryview(np.ascontiguousarray(rgb)).cast("B")
        try:
            while data:
                check_cancel(self.cancel_event)
                written = self.pipe.process.stdin.write(data)
                if not written:
                    raise BrokenPipeError("FFmpeg closed its input")
                data = data[written:]
        except (BrokenPipeError, OSError) as exc:
            check_cancel(self.cancel_event)
            self.pipe.wait()
            raise RuntimeError(f"FFmpeg input failed: {exc}") from exc

    def finish(self):
        self.pipe.process.stdin.close()
        self.pipe.wait()

    def __exit__(self, *args):
        if self.pipe is not None:
            self.pipe.close()


def validate_video(executable, target, expected_frames, cancel_event):
    args = [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
            "-err_detect", "explode", "-i", str(target), "-map", "0:v:0",
            "-an", "-sn", "-dn", "-vsync", "0", "-c:v", "rawvideo",
            "-pix_fmt", "rgb24", "-f", "framehash", "pipe:1"]
    pipe = _PipeProcess(args, cancel_event, decoding=True)
    count = 0
    dimensions = timebase = False
    try:
        for raw in pipe.process.stdout:
            check_cancel(cancel_event)
            line = raw.decode("ascii", errors="replace").strip()
            if line.startswith("#dimensions"):
                dimensions = bool(re.fullmatch(r"#dimensions\s+0:\s*1920x1080", line))
            elif line.startswith("#tb"):
                timebase = bool(re.fullmatch(r"#tb\s+0:\s*1/30", line))
            elif line and not line.startswith("#"):
                fields = [field.strip() for field in line.split(",")]
                if len(fields) != 6 or fields[0] != "0" or int(fields[4]) != 1920 * 1080 * 3:
                    raise RuntimeError("Invalid decoded video frame")
                count += 1
                if count > expected_frames:
                    raise RuntimeError("Decoded frame count exceeds expected frame count")
        pipe.wait()
        if not dimensions or not timebase:
            raise RuntimeError("Decoded video must be 1920x1080 at 30 fps")
        if count != expected_frames:
            raise RuntimeError(f"Decoded frame count {count} != expected frame count {expected_frames}")
        return count
    finally:
        pipe.close()
