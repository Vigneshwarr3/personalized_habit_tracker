"""Idle decision (pure functions, unit-tested).

Rule: keyboard/mouse idle > threshold (120 s) => idle,
      UNLESS something keeps the display awake (PreventUserIdleDisplaySleep, e.g. a video player)
      or audio/media is playing (coreaudiod's PreventUserIdleSystemSleep) -> active, flagged media=True.
Locked screen / loginwindow => send nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# processes whose assertions do NOT mean "the user is watching something"
DEFAULT_IGNORED = {"powerd", "WindowServer", "caffeinate", "Amphetamine", "Caffeine", "KeepingYouAwake",
                   "Lungo", "backupd", "softwareupdated", "sharingd", "useractivityd"}

_LINE = re.compile(r"pid\s+(?P<pid>\d+)\((?P<proc>[^)]*)\):\s+\[[^\]]*\]\s+[\d:]+\s+(?P<kind>\w+)\s+named:\s+\"(?P<name>.*)\"")


@dataclass(frozen=True)
class Assertions:
    display_awake: bool      # some app (not in the ignore list) prevents display sleep
    audio_playing: bool      # coreaudiod holds an idle-sleep assertion -> audio is playing
    holders: tuple[str, ...]


def parse_pmset_assertions(text: str, ignored: set[str] | None = None) -> Assertions:
    ignored = DEFAULT_IGNORED if ignored is None else ignored
    display = audio = False
    holders = []
    for line in text.splitlines():
        m = _LINE.search(line)
        if not m:
            continue
        proc, kind, name = m["proc"], m["kind"], m["name"]
        if proc in ignored:
            continue
        if kind == "PreventUserIdleDisplaySleep":
            display = True
            holders.append(proc)
        elif kind == "PreventUserIdleSystemSleep" and (proc == "coreaudiod" or "audio" in name.lower()):
            audio = True
            holders.append(proc)
    return Assertions(display, audio, tuple(sorted(set(holders))))


@dataclass(frozen=True)
class IdleState:
    send: bool     # False -> record nothing (locked / asleep)
    idle: bool
    media: bool    # active only because media is playing with no input


def decide(input_idle_s: float, threshold_s: float, locked: bool, assertions: Assertions | None) -> IdleState:
    if locked:
        return IdleState(send=False, idle=True, media=False)
    if input_idle_s <= threshold_s:
        return IdleState(send=True, idle=False, media=False)
    if assertions is not None and (assertions.display_awake or assertions.audio_playing):
        return IdleState(send=True, idle=False, media=True)
    return IdleState(send=True, idle=True, media=False)
