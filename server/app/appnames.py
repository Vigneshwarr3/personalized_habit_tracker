"""Readable app names for Android package names (the phone sends the real name from app 1.5 on; this covers
older data and anything it didn't send)."""
from __future__ import annotations

KNOWN = {
    "com.instagram.android": "Instagram", "com.instagram.barcelona": "Threads", "com.whatsapp": "WhatsApp",
    "com.google.android.youtube": "YouTube", "com.google.android.apps.youtube.music": "YouTube Music",
    "com.zhiliaoapp.musically": "TikTok", "com.snapchat.android": "Snapchat", "com.reddit.frontpage": "Reddit",
    "com.twitter.android": "X", "com.facebook.katana": "Facebook", "com.facebook.orca": "Messenger",
    "org.telegram.messenger": "Telegram", "com.discord": "Discord", "org.thoughtcrime.securesms": "Signal",
    "com.google.android.apps.messaging": "Messages", "com.linkedin.android": "LinkedIn",
    "com.indeed.android.jobsearch": "Indeed", "com.glassdoor.app": "Glassdoor", "com.duolingo": "Duolingo",
    "org.coursera.android": "Coursera", "com.udemy.android": "Udemy", "org.khanacademy.android": "Khan Academy",
    "com.quizlet.quizletandroid": "Quizlet", "com.openai.chatgpt": "ChatGPT", "com.anthropic.claude": "Claude",
    "ai.perplexity.app.android": "Perplexity", "com.google.android.apps.bard": "Gemini",
    "com.instructure.candroid": "Canvas", "com.google.android.apps.classroom": "Classroom",
    "com.google.android.keep": "Keep", "com.microsoft.office.onenote": "OneNote",
    "com.google.android.apps.docs.editors.docs": "Google Docs", "com.goodnotes.android": "GoodNotes",
    "com.notion.id": "Notion", "md.obsidian": "Obsidian", "com.google.android.calendar": "Calendar",
    "com.google.android.gm": "Gmail", "com.microsoft.office.outlook": "Outlook", "com.Slack": "Slack",
    "com.netflix.mediaclient": "Netflix", "in.startv.hotstar": "Hotstar", "com.amazon.avod.thirdpartyclient": "Prime Video",
    "com.disney.disneyplus": "Disney+", "com.hulu.plus": "Hulu", "com.spotify.music": "Spotify",
    "com.amazon.kindle": "Kindle", "com.google.android.apps.books": "Play Books",
    "com.chase.sig.android": "Chase", "com.infonow.bofa": "Bank of America", "com.wf.wellsfargomobile": "Wells Fargo",
    "com.google.android.apps.walletnfcrel": "Wallet", "com.android.chrome": "Chrome", "com.brave.browser": "Brave",
    "com.microsoft.emmx": "Edge", "com.google.android.apps.maps": "Maps", "com.ubercab": "Uber", "me.lyft.android": "Lyft",
    "com.strava": "Strava", "com.google.android.apps.fitness": "Fit", "com.nike.ntc": "Nike Training",
    "com.android.settings": "Settings", "com.google.android.apps.nexuslauncher": "Home screen",
    "com.sec.android.app.launcher": "Home screen", "com.android.systemui": "System UI",
    "com.google.android.permissioncontroller": "Permissions", "com.google.android.documentsui": "Files",
    "com.tailscale.ipn": "Tailscale", "com.habittracker.collector": "Habit Tracker",
    "us.zoom.videomeetings": "Zoom", "com.microsoft.teams": "Teams", "com.google.android.apps.photos": "Photos",
    "com.android.vending": "Play Store", "com.google.android.dialer": "Phone", "com.google.android.GoogleCamera": "Camera",
}
_SKIP = {"com", "org", "net", "in", "us", "me", "ai", "io", "co", "android", "google", "apps", "app", "mobile",
         "client", "ui", "activity", "main", "lite", "free", "pro"}


def pretty(app: str | None) -> str:
    """'com.instagram.android' -> 'Instagram'. Names that aren't package names are returned unchanged."""
    if not app:
        return ""
    if app in KNOWN:
        return KNOWN[app]
    if " " in app or "." not in app:
        return app
    parts = [p for p in app.split(".") if p and p.lower() not in _SKIP]
    if not parts:
        return app
    word = parts[-1]  # usually the app itself: com.example.coolapp -> coolapp
    return word[:1].upper() + word[1:]
