"""Applied scale policy, uncommitted text, and actual display bounds.

No frame arrays or worker/UI objects are retained here. Snapshot status is one
of manual, auto_ready, temporary, per_frame, or preview; no_finite is independent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .color_scale import ColorLimits, frame_color_limits
from .color_scale_models import ColorScaleScope
from .selection import DisplaySettings


@dataclass(frozen=True)
class EffectiveScale:
    limits: ColorLimits
    mode: str
    status: str
    no_finite: bool


class ColorScaleState:
    def __init__(self, display: DisplaySettings | None = None) -> None:
        self._settings = (DisplaySettings('inferno', 0.0, 1.0, fixed_source='auto')
                          if display is None else display)
        self._needs_seed = display is None
        self._scope: ColorScaleScope | None = None
        self._draft_bounds: tuple[str, str] | None = None
        self._auto_cache: tuple[ColorScaleScope, ColorLimits] | None = None
        if self._settings.auto_scope is not None:
            self._auto_cache = (self._settings.auto_scope, self._fixed_limits())
        self._frame_limits: ColorLimits | None = None
        self._latest_valid_actual: ColorLimits | None = None
        self._preview_limits: ColorLimits | None = None
        self._refresh()

    @property
    def settings(self) -> DisplaySettings:
        return self._settings

    @property
    def draft_bounds(self) -> tuple[str, str] | None:
        return self._draft_bounds

    @property
    def draft_pending(self) -> bool:
        return self._draft_bounds is not None

    @property
    def fixed_ready(self) -> bool:
        display = self.settings
        return (display.fixed_source == 'manual'
                or (self._scope is not None and display.auto_scope == self._scope))

    def _fixed_limits(self) -> ColorLimits:
        return ColorLimits(self.settings.vmin_c, self.settings.vmax_c)

    def _replace_settings(self, **changes) -> None:
        candidate = replace(self.settings, **changes)
        if candidate != self.settings:
            self._settings = candidate

    def _refresh(self) -> None:
        display = self.settings
        if self._preview_limits is not None:
            limits, status = self._preview_limits, 'preview'
        elif display.scale_mode == 'fixed' and self.fixed_ready:
            limits = self._fixed_limits()
            status = 'manual' if display.fixed_source == 'manual' else 'auto_ready'
        else:
            limits = self._frame_limits or self._latest_valid_actual or ColorLimits(0.0, 1.0)
            status = 'per_frame' if display.scale_mode == 'per_frame' else 'temporary'
        no_finite = self._frame_limits is None
        self._effective = EffectiveScale(limits, display.scale_mode, status, no_finite)
        if not no_finite:
            self._latest_valid_actual = limits

    def set_scope(self, scope: ColorScaleScope) -> None:
        if not isinstance(scope, ColorScaleScope):
            raise ValueError('scope must be a ColorScaleScope')
        self._scope = scope
        if self.settings.auto_scope != scope:
            self._replace_settings(auto_scope=None)
        self._refresh()

    def observe_frame(self, values) -> ColorLimits:
        limits = frame_color_limits(values)
        self._frame_limits = limits
        if self._needs_seed and limits is not None:
            self._replace_settings(vmin_c=limits.vmin, vmax_c=limits.vmax)
            self._needs_seed = False
        self._refresh()
        return self._effective.limits

    def select_mode(self, mode: str) -> None:
        if mode not in ('fixed', 'per_frame'):
            raise ValueError('mode must be fixed or per_frame')
        self._replace_settings(scale_mode=mode)
        self._refresh()

    def edit_bounds(self, min_text: str, max_text: str) -> None:
        if not isinstance(min_text, str) or not isinstance(max_text, str):
            raise ValueError('draft bounds must be strings')
        self._draft_bounds = (min_text, max_text)

    def discard_draft(self) -> None:
        """Drop uncommitted text without changing applied or effective scale."""
        self._draft_bounds = None

    def apply_draft(self) -> None:
        if self.draft_bounds is None:
            return
        minimum, maximum = map(float, self.draft_bounds)
        self.apply_manual(minimum, maximum)

    def apply_manual(self, minimum: float, maximum: float) -> None:
        # DisplaySettings validates both inputs before any state is changed.
        candidate = replace(self.settings, vmin_c=minimum, vmax_c=maximum,
                            scale_mode='fixed', fixed_source='manual', auto_scope=None)
        if candidate != self.settings:
            self._settings = candidate
        self._needs_seed = False
        self._draft_bounds = None
        self._refresh()

    def restore_auto(self) -> None:
        self._replace_settings(scale_mode='fixed', fixed_source='auto', auto_scope=None)
        self._draft_bounds = None
        if self._auto_cache is not None and self._auto_cache[0] == self._scope:
            self.accept_auto(*self._auto_cache)
        else:
            self._refresh()

    def invalidate_auto(self) -> None:
        """Forget automatic provenance after an external source change."""
        self._auto_cache = None
        self._replace_settings(auto_scope=None)
        self._refresh()

    def accept_auto(self, scope: ColorScaleScope, limits: ColorLimits) -> bool:
        if (not isinstance(scope, ColorScaleScope) or scope != self._scope
                or self.settings.scale_mode != 'fixed'
                or self.settings.fixed_source != 'auto'
                or not isinstance(limits, ColorLimits)):
            return False
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in (limits.vmin, limits.vmax)):
            return False
        if limits.vmin >= limits.vmax:
            return False
        self._replace_settings(vmin_c=limits.vmin, vmax_c=limits.vmax, auto_scope=scope)
        self._auto_cache = (scope, limits)
        self._needs_seed = False
        self._refresh()
        return True

    def settings_for_selection(self, scope: ColorScaleScope) -> DisplaySettings:
        display = self.settings
        valid_scope = (display.auto_scope if display.fixed_source == 'auto'
                       and display.auto_scope == scope else None)
        return replace(display, auto_scope=valid_scope)

    def render_snapshot(self) -> EffectiveScale:
        return self._effective

    def freeze_preview(self) -> None:
        if self._preview_limits is None:
            self._preview_limits = self._effective.limits
        self._refresh()

    def resume_preview(self) -> None:
        self._preview_limits = None
        self._refresh()
