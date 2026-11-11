"""Drops sensitive details ON THE MAC, before anything is buffered or uploaded.

Rules come from the server (settings.yaml -> privacy, via GET /privacy-rules), cached in
~/Library/Application Support/HabitTracker/privacy-rules.json. Built-in defaults cover the first run offline.
"""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_RULES = {
    "private_apps": ["1Password*", "Bitwarden", "KeePassXC", "KeePassium", "Keychain Access", "Passwords",
                     "Dashlane", "LastPass", "Enpass", "NordPass", "Proton Pass", "*bank*", "*wallet*"],
    "private_domains": ["*bank*", "chase.com", "wellsfargo.com", "capitalone.com", "discover.com",
                        "americanexpress.com", "citi.com", "paypal.com", "venmo.com", "robinhood.com", "fidelity.com",
                        "schwab.com", "vanguard.com", "coinbase.com", "creditkarma.com", "bitwarden.com",
                        "1password.com", "lastpass.com", "accounts.google.com", "passwords.google.com",
                        "login.microsoftonline.com", "appleid.apple.com", "account.apple.com", "login.*", "auth.*",
                        "sso.*", "idp.*", "*.okta.com", "duosecurity.com", "mychart.*", "mychart.com", "irs.gov",
                        "ssa.gov"],
    "private_title_regex": r"\b(passwords?|passcodes?|passkeys?|sign[ -]?in|log[ -]?in|logon|one[- ]time "
                           r"(code|password)|otp|verification code|security code|2fa|two[- ]factor|mfa|account "
                           r"(number|ending)|routing number|credit card|debit card|card number|cvv|ssn|social "
                           r"security|bank statement|tax return)\b",
}
YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def _match(value: str, patterns: list[str]) -> bool:
    v = value.lower()
    for p in patterns:
        p = p.lower()
        if fnmatch.fnmatchcase(v, p) or ("*" not in p and v.endswith("." + p)):
            return True
    return False


class PrivacyFilter:
    def __init__(self, rules: dict | None = None):
        self.set_rules(rules or DEFAULT_RULES)

    def set_rules(self, rules: dict) -> None:
        self.rules = rules
        self.apps = list(rules.get("private_apps") or [])
        self.domains = list(rules.get("private_domains") or [])
        rx = rules.get("private_title_regex")
        self.title_re = re.compile(rx, re.I) if rx else None

    @staticmethod
    def _host(url: str | None) -> str | None:
        if not url:
            return None
        try:
            h = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return None
        return h[4:] if h.startswith("www.") else h or None

    def is_private(self, app: str | None, url: str | None, title: str | None) -> bool:
        host = self._host(url)
        if app and _match(app, self.apps):
            return True
        if host and _match(host, self.domains):
            return True
        if title and self.title_re is not None and not (host and (host in YOUTUBE or host.endswith(".youtube.com"))):
            return bool(self.title_re.search(title))
        return False

    def redact(self, app: str, url: str | None, title: str | None) -> tuple[str | None, str | None]:
        """Returns (url, title) to record: bare site + no title for anything sensitive."""
        if not self.is_private(app, url, title):
            return url, title
        if url:
            try:
                p = urlsplit(url)
                url = f"{p.scheme}://{p.hostname}/" if p.scheme in ("http", "https") and p.hostname else None
            except ValueError:
                url = None
        return url, None

    # ---- cache of the server's rules
    def load_cache(self, path: Path) -> None:
        try:
            self.set_rules(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass

    def save_cache(self, path: Path) -> None:
        try:
            path.write_text(json.dumps(self.rules))
        except OSError:
            pass
