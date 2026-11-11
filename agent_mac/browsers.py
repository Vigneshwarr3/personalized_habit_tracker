"""Active-tab URL + title via AppleScript, run in-process with NSAppleScript (no osascript process per poll).

* Only queried when the browser is the frontmost app (so we never launch a browser or hit an uninstalled one).
* Chrome / Brave / Edge / Arc expose the window mode -> incognito/private windows are skipped.
  Safari has no scripting API for private windows -> best effort (see README).
* If Automation permission is denied (error -1743) we fall back to the window title for 10 minutes, then retry.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger("ht.browsers")

SEP = "\x1f"  # ASCII unit separator: never appears in URLs/titles

_CHROMIUM = """
tell application id "{bid}"
    if (count of windows) is 0 then return ""
    set w to front window
    set m to "normal"
    try
        set m to (mode of w) as text
    end try
    set t to active tab of w
    return m & (character id 31) & (URL of t) & (character id 31) & (title of t)
end tell
"""

_ARC = """
tell application id "{bid}"
    if (count of windows) is 0 then return ""
    set w to front window
    set m to "normal"
    try
        if incognito of w then set m to "incognito"
    end try
    set t to active tab of w
    return m & (character id 31) & (URL of t) & (character id 31) & (title of t)
end tell
"""

_SAFARI = """
tell application id "{bid}"
    if (count of windows) is 0 then return ""
    set t to current tab of front window
    return "normal" & (character id 31) & (URL of t) & (character id 31) & (name of t)
end tell
"""

BROWSERS = {
    "com.google.Chrome": _CHROMIUM,
    "com.google.Chrome.beta": _CHROMIUM,
    "com.brave.Browser": _CHROMIUM,
    "com.microsoft.edgemac": _CHROMIUM,
    "org.chromium.Chromium": _CHROMIUM,
    "company.thebrowser.Browser": _ARC,
    "com.apple.Safari": _SAFARI,
}

ERR_NOT_AUTHORIZED = -1743
DENIED_RETRY_S = 600


@dataclass(frozen=True)
class Tab:
    url: str | None
    title: str | None
    private: bool


def parse_result(text: str | None) -> Tab | None:
    if not text:
        return None
    parts = text.split(SEP)
    if len(parts) != 3:
        return None
    mode, url, title = parts
    private = mode.strip().lower() in {"incognito", "private"}
    return Tab(url or None, title or None, private)


class BrowserReader:
    def __init__(self):
        self._scripts: dict[str, object] = {}
        self._denied_until: dict[str, float] = {}

    def supports(self, bundle_id: str | None) -> bool:
        return bool(bundle_id) and bundle_id in BROWSERS

    def denied(self, bundle_id: str) -> bool:
        return self._denied_until.get(bundle_id, 0) > time.time()

    def read(self, bundle_id: str) -> Tab | None:
        if not self.supports(bundle_id) or self.denied(bundle_id):
            return None
        from Foundation import NSAppleScript  # lazy: keeps tests importable on Linux

        script = self._scripts.get(bundle_id)
        if script is None:
            script = NSAppleScript.alloc().initWithSource_(BROWSERS[bundle_id].format(bid=bundle_id))
            self._scripts[bundle_id] = script
        result, err = script.executeAndReturnError_(None)
        if err is not None:
            code = int(err.get("NSAppleScriptErrorNumber", 0))
            if code == ERR_NOT_AUTHORIZED:
                log.warning("Automation permission denied for %s; using window titles only (retry in 10 min)",
                            bundle_id)
                self._denied_until[bundle_id] = time.time() + DENIED_RETRY_S
            else:
                log.debug("AppleScript error %s for %s: %s", code, bundle_id, err.get("NSAppleScriptErrorMessage"))
            return None
        return parse_result(result.stringValue() if result is not None else None)
