import time


class TemporalFlag:
    def __init__(self, confirm_frames: int, recovery_frames: int | None = None):
        self.confirm_frames = max(1, int(confirm_frames))
        self.recovery_frames = max(1, int(recovery_frames if recovery_frames is not None else confirm_frames))
        self.active_frames = 0
        self.inactive_frames = 0
        self.confirmed = False

    def update(self, active: bool) -> bool:
        if active:
            self.active_frames += 1
            self.inactive_frames = 0
            if self.active_frames >= self.confirm_frames:
                self.confirmed = True
        else:
            self.inactive_frames += 1
            self.active_frames = 0
            if self.inactive_frames >= self.recovery_frames:
                self.confirmed = False
        return self.confirmed

    def reset(self):
        self.active_frames = 0
        self.inactive_frames = 0
        self.confirmed = False


class TemporalDurationFlag:
    """Confirm/recover a condition by elapsed time, independent of camera FPS."""

    def __init__(self, confirm_seconds: float, recovery_seconds: float | None = None):
        self.confirm_seconds = max(0.0, float(confirm_seconds))
        self.recovery_seconds = max(
            0.0,
            float(recovery_seconds if recovery_seconds is not None else confirm_seconds),
        )
        self._active_since = None
        self._inactive_since = None
        self.confirmed = False

    def update(self, active: bool, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if active:
            self._inactive_since = None
            if self._active_since is None:
                self._active_since = now
            if now - self._active_since >= self.confirm_seconds:
                self.confirmed = True
        else:
            self._active_since = None
            if self._inactive_since is None:
                self._inactive_since = now
            if now - self._inactive_since >= self.recovery_seconds:
                self.confirmed = False
        return self.confirmed

    def reset(self):
        self._active_since = None
        self._inactive_since = None
        self.confirmed = False
