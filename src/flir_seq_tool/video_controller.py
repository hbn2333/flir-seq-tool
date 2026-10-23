"""Main-thread MP4 workflow independent of Matplotlib widgets."""

from pathlib import Path

from .video_export import VideoJob, build_video_request


class VideoController:
    def __init__(self, session, dialogs, output_dir, job_factory=VideoJob,
                 on_busy=lambda busy: None, on_message=lambda message: None):
        self.session, self.dialogs, self.output_dir = session, dialogs, Path(output_dir)
        self.job_factory, self.on_busy, self.on_message = job_factory, on_busy, on_message
        self.job = None
        self.closed = False

    @property
    def cancel_allowed(self):
        return self.job is not None and self.job.cancel_allowed

    def start(self):
        if self.closed or self.job is not None:
            return
        self.on_busy(True)
        try:
            speed = self.dialogs.choose_video_speed()
            if speed is None:
                return
            start, end = self.session.measure_source_range()
            name = f"{Path(self.session.reader.path).stem}-{start}-{end}.mp4"
            target = self.dialogs.choose_video_export(self.output_dir, name)
            if target is None:
                return
            request = build_video_request(self.session, target, speed)
            self.job = self.job_factory(request)
            self.job.start()
            self.on_message("MP4: checking source timestamps…")
        except Exception as exc:
            if self.job is not None:
                self.job.close()
                self.job = None
            self.on_message(f"MP4 export failed: {exc}")
        finally:
            if self.job is None:
                self.on_busy(False)

    def poll(self):
        if self.closed or self.job is None:
            return
        try:
            for event in self.job.poll():
                if event['kind'] == 'ready':
                    if self.dialogs.confirm(event['summary']):
                        self.job.confirm()
                    else:
                        self.job.cancel()
                elif event['kind'] == 'progress':
                    total, current = event.get('total', 0), event.get('current', 0)
                    count = f" {current}/{total} ({100 * current / total:.1f}%)" if total else ""
                    self.on_message(f"MP4: {event['stage']}{count}")
                elif event['kind'] == 'result':
                    self.on_message(event['message'])
                    self.job.close()
                    self.job = None
                    self.on_busy(False)
                    break
        except Exception as exc:
            if self.job is not None:
                self.job.close()
                self.job = None
            self.on_message(f"MP4 export failed: {exc}")
            self.on_busy(False)

    def cancel(self):
        if self.job is not None:
            allowed = self.job.cancel()
            self.on_message("MP4: cancellation requested…" if allowed
                            else "MP4: publishing; cancellation is no longer available")

    def close(self):
        self.closed = True
        if self.job is not None:
            self.job.close()
            self.job = None
