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
