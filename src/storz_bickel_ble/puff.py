"""Puff (inhalation) detection for Venty / Veazy.

The device counts its auto-shutoff timer down once per second while it sits at
temperature. When it detects an inhalation it resets the counter to its maximum
and holds it there until the draw ends. So, once the setpoint is reached, any
increase of the counter marks the start of a puff and the first decrease marks
its end. Changing temperature or boost with the buttons also resets the
counter, so increases shortly after a settings change are ignored.

Verified on a Venty with firmware V01.11 at 0.5 s polling.
"""

from __future__ import annotations

import time
from typing import Optional

from .models import VaporizerState


class PuffDetector:
    def __init__(self, merge_s: float = 2.0, settle_s: float = 2.0):
        self.merge_s = merge_s      # a reset this soon after a puff ended = same puff
        self.settle_s = settle_s    # ignore resets this long after a settings change
        self.reset()

    def reset(self) -> None:
        self._prev_counter: Optional[int] = None
        self._prev_cfg = None
        self._cfg_changed = 0.0
        self._puff_end = 0.0
        self.puff_started = 0.0

    def feed(self, state: VaporizerState, now: Optional[float] = None) -> bool:
        """Update ``state.puffs`` / ``state.puffing``; True when a new puff started."""
        now = time.time() if now is None else now
        counter = state.auto_shutoff_s
        cfg = (state.target_temp, state.heater_mode)
        if self._prev_cfg is not None and cfg != self._prev_cfg:
            self._cfg_changed = now
        prev, self._prev_counter, self._prev_cfg = self._prev_counter, counter, cfg

        if not state.heater_on:
            state.puffs, state.puffing = 0, False
            return False
        if (not state.setpoint_reached or prev is None or counter is None
                or now - self._cfg_changed < self.settle_s):
            if state.puffing:
                state.puffing, self._puff_end = False, now
            return False
        started = False
        if counter > prev and not state.puffing:
            if now - self._puff_end > self.merge_s:
                state.puffs += 1
                self.puff_started = now
                started = True
            state.puffing = True
        elif counter < prev and state.puffing:
            state.puffing, self._puff_end = False, now
        return started
