"""Thin wrappers over macOS APIs (PyObjC). Nothing here captures the screen; window TITLES need the
Screen Recording permission, and without it titles are simply empty."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Front:
    app: str
    bundle_id: str | None
    pid: int | None
    title: str | None


def pump_runloop() -> None:
    """Let NSWorkspace refresh its notion of the frontmost app (needed in a non-GUI process)."""
    from Foundation import NSDate, NSRunLoop
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))


def frontmost() -> Front | None:
    from AppKit import NSWorkspace
    import Quartz

    pump_runloop()
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = str(app.localizedName()) if app else None
    bid = str(app.bundleIdentifier()) if app and app.bundleIdentifier() else None
    pid = int(app.processIdentifier()) if app else None

    title = None
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID
    ) or []
    for w in windows:  # front-to-back order; apps often have small unnamed helper windows (tab strips, bars)
        if w.get("kCGWindowLayer", 1) != 0:
            continue
        if pid is not None and w.get("kCGWindowOwnerPID") != pid:
            continue
        if name is None:
            name = str(w.get("kCGWindowOwnerName") or "")
            pid = w.get("kCGWindowOwnerPID")
        t = w.get("kCGWindowName")
        if t:
            title = str(t)
            break  # frontmost NAMED window of the front app
    if not name:
        return None
    return Front(name, bid, pid, title)


def ensure_screen_title_access() -> bool:
    """True if window titles are readable. If not, ask macOS once: this adds THIS exact Python binary to
    System Settings > Privacy & Security > Screen & System Audio Recording, where you switch it on."""
    import Quartz
    if Quartz.CGPreflightScreenCaptureAccess():
        return True
    Quartz.CGRequestScreenCaptureAccess()
    return False


def input_idle_seconds() -> float:
    import Quartz
    return float(Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))


def screen_locked() -> bool:
    import Quartz
    d = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(d.get("CGSSessionScreenIsLocked", False)) or not bool(d.get("kCGSSessionOnConsoleKey", True))


def pmset_assertions_text() -> str:
    return subprocess.run(["/usr/bin/pmset", "-g", "assertions"], capture_output=True, text=True, timeout=5).stdout


def on_battery() -> bool:
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return "Battery Power" in out.splitlines()[0] if out else False
