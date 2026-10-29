"""One independent worker scan for standard statistics and every canonical ROI row."""

from dataclasses import asdict
from datetime import datetime
import threading

from .frame_transformer import FrameTransformer
from .height_models import row_profile
from .height_store import HeightResultStore
from .measurement_job import (AnalysisCancelled, AnalysisOutcome, AnalysisProgress,
                              validate_reader_identity)
from .measurement_stats import summarize
from .reader import ScienceFileReader


def run_height_analysis(request, *, reader_factory=ScienceFileReader, cancel_event,
                        progress, temp_root=None):
    store = None
    current_source_frame = request.source_start
    total = request.source_end - request.source_start + 1
    progress.update(0, total, 'Starting height analysis')
    try:
        if cancel_event.is_set():
            raise AnalysisCancelled()
        transformer = FrameTransformer(request.transform)
        first = previous = None
        with reader_factory(request.source_path) as reader:
            validate_reader_identity(reader, request.source_path, request.source_identity)
            store = HeightResultStore.create(temp_root)
            store._validation_cancel_event = cancel_event
            for local, source in enumerate(range(request.source_start, request.source_end + 1)):
                current_source_frame = source
                if cancel_event.is_set():
                    raise AnalysisCancelled()
                frame = reader.temperature_frame_at(source)
                if not isinstance(frame.timestamp, datetime):
                    raise ValueError('missing or invalid timestamp')
                now = frame.timestamp
                if first is None:
                    first = now
                relative = (now - first).total_seconds()
                status = ('ok' if previous is None or now > previous else
                          'duplicate' if now == previous else 'backwards')
                previous = now
                timestamp = now.isoformat()
                store.append_frames([dict(local_frame=local, source_frame=source, timestamp=timestamp,
                                          relative_s=relative, time_status=status)])
                values = transformer.apply(frame.temperature_c)
                for roi in request.rois:
                    if cancel_event.is_set():
                        raise AnalysisCancelled()
                    store.append_rows([dict(roi_id=roi.id, local_frame=local, source_frame=source,
                                            timestamp=timestamp, relative_s=relative,
                                            **asdict(summarize(values, roi)))])
                    profile = row_profile(values, roi)
                    store.append_height_rows(dict(roi_id=roi.id, local_frame=local, roi_row_index=r,
                                                  sum_c=float(profile.sums_c[r]),
                                                  valid_pixels=int(profile.valid_counts[r]),
                                                  row_mean_c=float(profile.means_c[r]) if profile.valid_counts[r] else None,
                                                  total_pixels=roi.width) for r in range(roi.height))
                progress.update(local + 1, total, f'Source frame {source}')
            validate_reader_identity(reader, request.source_path, request.source_identity)
        if cancel_event.is_set():
            raise AnalysisCancelled()
        store._writer.set_progress_handler(lambda: int(cancel_event.is_set()), 1000)
        store.complete(expected_rows=total * len(request.rois),
                       expected_height_rows=total * sum(r.height for r in request.rois),
                       expected_frames=total, roi_shapes=tuple((r.id, r.width, r.height) for r in request.rois),
                       key_json=request.key.to_json())
        if cancel_event.is_set():
            raise AnalysisCancelled()
        store.close()
        outcome = AnalysisOutcome('complete', store, request.key)
        store = None
    except AnalysisCancelled:
        outcome = AnalysisOutcome('cancelled', None, request.key)
    except Exception as exc:
        if cancel_event.is_set():
            outcome = AnalysisOutcome('cancelled', None, request.key)
        else:
            outcome = AnalysisOutcome('failed', None, request.key, f'source frame {current_source_frame}: {exc}')
    finally:
        if store is not None:
            try:
                store.cleanup()
            except Exception as exc:
                outcome = AnalysisOutcome('failed', None, request.key,
                                          f'{outcome.error or outcome.status}; cleanup failed: {store.path}: {exc}',
                                          cleanup_store=store)
    return outcome


class HeightAnalysisJob:
    def __init__(self, request, *, reader_factory=ScienceFileReader, temp_root=None):
        self.request = request
        self.progress = AnalysisProgress()
        self._cancel = threading.Event()
        self._outcome = None
        self._thread = threading.Thread(target=self._run, args=(reader_factory, temp_root),
                                        daemon=False, name='flir-height-analysis')

    def _run(self, factory, temp_root):
        self._outcome = run_height_analysis(self.request, reader_factory=factory, cancel_event=self._cancel,
                                            progress=self.progress, temp_root=temp_root)

    @property
    def outcome(self):
        return self._outcome

    def start(self):
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def is_alive(self):
        return self._thread.is_alive()

    def join(self, timeout=None):
        self._thread.join(timeout)
