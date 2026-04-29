"""
Konfiguration und Konstanten für den DPC Modmail Bot.
Alle technischen Defaults zentral. Pro-Server-Einstellungen liegen in der DB.
"""

import os

# ============================================================
#  DISCORD – TOKEN & CLIENT-ID
# ============================================================
# Möglichkeit 1 (empfohlen): per Umgebungsvariable / Hoster-Secret setzen
#                             → DISCORD_BOT_TOKEN, DISCORD_CLIENT_ID
# Möglichkeit 2: einfach hier direkt eintragen (zwischen den "")
#                Wenn die Umgebungsvariable nicht gesetzt ist, wird der
#                Wert von hier verwendet.
#
# ⚠️ Wenn du den Token hier einträgst, NIEMALS diese Datei in ein
#    öffentliches Git-Repo hochladen – sonst kann jeder deinen Bot übernehmen!
# ============================================================
BOT_TOKEN_INLINE = "MTQ5ODYwNDI3Nzc0MzA5MTczMg.G1EB5C.4SeQWfDPNFD4RHcTLnieJS0dTwf3qcyZ4AySQ0"  # ← hier deinen Bot-Token eintragen (oder leer lassen)
CLIENT_ID_INLINE = "1498604277743091732"  # ← hier deine Application-/Client-ID eintragen

BOT_TOKEN = BOT_TOKEN_INLINE or os.environ.get("DISCORD_BOT_TOKEN", "")
CLIENT_ID = CLIENT_ID_INLINE or os.environ.get("DISCORD_CLIENT_ID", "")
COMMAND_PREFIX = "!"

# ===== Auto-Forward von Mod-Nachrichten im Ticket-Channel =====
# True  = jede normale Nachricht eines Mods im Ticket-Channel wird
#         automatisch an den User gesendet (wie /reply)
# False = Mods müssen explizit /reply oder /areply benutzen
# Mit Präfix "=" oder ">" am Anfang einer Nachricht wird das
# Auto-Forwarding für DIESE Nachricht übersprungen (interne Mod-Diskussion).
AUTO_FORWARD_MOD_MESSAGES = True
SILENT_PREFIXES = ("=", ">", "//")

# ===== Pfade =====
DATABASE_PATH = "modmail.db"
TRANSCRIPT_DIR = "transcripts"

# ===== Web / Keep-Alive =====
KEEP_ALIVE_PORT = int(os.environ.get("PORT", 8080))

# ===== Bot-Permissions für Invite-Link =====
# View Channels, Send Messages, Manage Channels, Manage Messages,
# Read Message History, Embed Links, Attach Files, Manage Roles,
# Add Reactions, Read DMs, Send Messages in Threads
INVITE_PERMISSIONS = 268823632

# ===== Defaults für Server-Einstellungen =====
DEFAULT_AUTO_CLOSE_HOURS = 72
DEFAULT_AUTO_CLOSE_CHECK_MIN = 30
DEFAULT_REMINDER_HOURS = 24
DEFAULT_TICKET_COOLDOWN_MIN = 0
DEFAULT_MAX_OPEN_TICKETS = 1
DEFAULT_LANGUAGE = "de"

# Spam-Schutz (Speicher-basiert)
USER_DM_COOLDOWN = 3  # Sekunden zwischen DMs
USER_RATE_LIMIT = 15  # Nachrichten pro Minute
DM_DELETE_LOOKBACK = 200

# ===== Status / Priorität / Ränge =====
STATUS_OPEN = "offen"
STATUS_IN_PROGRESS = "in_bearbeitung"
STATUS_CLOSED = "geschlossen"

STATUS_LABELS_DE = {
    STATUS_OPEN: "🟢 Offen",
    STATUS_IN_PROGRESS: "🟡 In Bearbeitung",
    STATUS_CLOSED: "🔴 Geschlossen",
}
STATUS_LABELS_EN = {
    STATUS_OPEN: "🟢 Open",
    STATUS_IN_PROGRESS: "🟡 In Progress",
    STATUS_CLOSED: "🔴 Closed",
}

PRIORITIES = ["niedrig", "mittel", "hoch", "dringend"]
PRIORITY_EMOJI = {"niedrig": "🟢", "mittel": "🟡", "hoch": "🟠", "dringend": "🔴"}
PRIORITY_COLOR = {
    "niedrig": 0x57F287,
    "mittel": 0xFEE75C,
    "hoch": 0xFAA61A,
    "dringend": 0xED4245,
}

RANK_SUPPORTER = "supporter"
RANK_MODERATOR = "moderator"
RANK_ADMIN = "admin"
RANK_ORDER = {RANK_SUPPORTER: 1, RANK_MODERATOR: 2, RANK_ADMIN: 3}
RANK_LABELS = {
    RANK_SUPPORTER: "🟢 Supporter",
    RANK_MODERATOR: "🟡 Moderator",
    RANK_ADMIN: "🔴 Admin",
}

# ===== Default Ticket-Kategorien (bei /setup automatisch angelegt) =====
DEFAULT_TICKET_CATEGORIES = [
    ("Frage", "Allgemeine Frage", "❓"),
    ("Bug Report", "Einen Fehler melden", "🐛"),
    ("Beschwerde", "Etwas geht schief", "⚠️"),
    ("Ban Appeal", "Einspruch gegen einen Bann", "⛓️"),
    ("Sonstiges", "Anderes Anliegen", "💬"),
]

# ===== Embed-Farben (einheitlich) =====
COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_ERROR = 0xED4245
COLOR_INFO = 0x3498DB
COLOR_USER = 0x57F287
COLOR_MOD = 0x5865F2
COLOR_MOD_ANON = 0x9B59B6
COLOR_NOTE = 0xFEE75C

EMBED_FOOTER = "DPC Modmail"

# ===== Übersetzungen =====
LANG = {
    "de": {
        "welcome": (
            "Hallo! Vielen Dank, dass du dich an unser Mod-Team wendest.\n\n"
            "Dein Anliegen wurde an unsere Moderatoren weitergeleitet. "
            "Bitte beschreibe dein Problem so genau wie möglich – wir melden "
            "uns schnellstmöglich bei dir."
        ),
        "close": (
            "Dein Modmail-Ticket wurde geschlossen. "
            "Der Chatverlauf wurde aus dieser DM gelöscht.\n"
            "Bei weiteren Fragen schreibe einfach erneut."
        ),
        "auto_closed": "⏰ Dein Ticket wurde wegen Inaktivität automatisch geschlossen.",
        "pick_category": "Bitte wähle eine Kategorie für dein Anliegen:",
        "blacklisted": "🚫 Du wurdest vom Modmail-System ausgeschlossen.",
        "no_modmail": "⚠️ Aktuell ist kein Modmail-Channel konfiguriert.",
        "ticket_created": "✅ Dein Ticket wurde erstellt – ein Mod meldet sich gleich!",
        "rate_request": "Wie zufrieden warst du mit dem Support?",
        "rate_thanks": "Danke für dein Feedback!",
        "cooldown": "⏳ Bitte warte etwas, bevor du ein neues Ticket öffnest.",
        "max_open": "❗ Du hast bereits die maximale Anzahl offener Tickets.",
        "no_categories": "Allgemein",
    },
    "en": {
        "welcome": (
            "Hello! Thanks for reaching out to our mod team.\n\n"
            "Your message was forwarded to our moderators. Please describe "
            "your issue in as much detail as possible — we'll get back to "
            "you as soon as we can."
        ),
        "close": (
            "Your modmail ticket has been closed. "
            "The chat history was removed from this DM.\n"
            "If you have more questions, just write again."
        ),
        "auto_closed": "⏰ Your ticket was auto-closed due to inactivity.",
        "pick_category": "Please pick a category for your request:",
        "blacklisted": "🚫 You have been blacklisted from the modmail system.",
        "no_modmail": "⚠️ Modmail isn't configured yet on this server.",
        "ticket_created": "✅ Your ticket was created — a mod will be with you shortly!",
        "rate_request": "How satisfied were you with the support?",
        "rate_thanks": "Thanks for your feedback!",
        "cooldown": "⏳ Please wait before opening a new ticket.",
        "max_open": "❗ You already have the maximum number of open tickets.",
        "no_categories": "General",
    },
}


def t(lang: str, key: str) -> str:
    """Übersetzungs-Helper. Fällt auf DE zurück."""
    return LANG.get(lang, LANG["de"]).get(key, LANG["de"].get(key, key))


def status_label(lang: str, status: str) -> str:
    table = STATUS_LABELS_EN if lang == "en" else STATUS_LABELS_DE
    return table.get(status, status)
