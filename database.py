"""
Datenbank-Layer (aiosqlite). Alle Queries für den Modmail Bot.
Schema enthält: Server-Konfig, Tickets, Nachrichten, Blacklist,
Mod-Profile, Ticket-Kategorien, Templates, Bewertungen, Aktivitätsprotokoll.
"""
import aiosqlite
import datetime
from typing import Optional

import config


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


class Database:
    def __init__(self, path: str = config.DATABASE_PATH):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            # Server-Konfiguration (alle Settings auf einer Zeile)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    modmail_category_id INTEGER,
                    log_channel_id INTEGER,
                    mod_role_id INTEGER,
                    ping_role_id INTEGER,
                    escalation_role_id INTEGER,
                    welcome_enabled INTEGER DEFAULT 1,
                    anonymous_default INTEGER DEFAULT 0,
                    delete_dm_on_close INTEGER DEFAULT 1,
                    delete_channel_on_close INTEGER DEFAULT 0,
                    auto_close_hours INTEGER DEFAULT 72,
                    ticket_cooldown_minutes INTEGER DEFAULT 0,
                    max_open_tickets_per_user INTEGER DEFAULT 1,
                    reminder_hours INTEGER DEFAULT 24,
                    rating_enabled INTEGER DEFAULT 0,
                    language TEXT DEFAULT 'de',
                    ticket_counter INTEGER DEFAULT 0
                )
            """)

            # Tickets (= eigener Channel pro Anliegen)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_number INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    category_name TEXT,
                    status TEXT DEFAULT 'offen',
                    priority TEXT DEFAULT 'mittel',
                    tag TEXT,
                    assigned_mod_id INTEGER,
                    participating_mods TEXT DEFAULT '',
                    opened_at TEXT NOT NULL,
                    first_reply_at TEXT,
                    last_activity TEXT NOT NULL,
                    closed_at TEXT,
                    closed_by_id INTEGER,
                    last_reminder_at TEXT
                )
            """)

            # Nachrichten (für Transkripte + Stats)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    is_mod INTEGER DEFAULT 0,
                    is_internal INTEGER DEFAULT 0,
                    is_anonymous INTEGER DEFAULT 0,
                    content TEXT,
                    attachments TEXT,
                    created_at TEXT NOT NULL,
                    bot_dm_msg_id INTEGER,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    added_by INTEGER,
                    added_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS mod_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    mod_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    ticket_id INTEGER,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Mod-Profile pro Server (Rang, Abwesenheit, Round-Robin)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS mod_profile (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    rank TEXT DEFAULT 'supporter',
                    is_absent INTEGER DEFAULT 0,
                    last_assigned_at TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            # Ticket-Kategorien (User-Auswahl beim Öffnen)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    emoji TEXT
                )
            """)

            # Antwort-Vorlagen
            await db.execute("""
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(guild_id, name)
                )
            """)

            # Bewertungen
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ratings (
                    ticket_id INTEGER PRIMARY KEY,
                    rating INTEGER NOT NULL,
                    comment TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS weekly_reports (
                    guild_id INTEGER PRIMARY KEY,
                    last_sent_at TEXT
                )
            """)

            # Snippets (gespeicherte Antworten)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    use_count INTEGER DEFAULT 0,
                    UNIQUE(guild_id, name)
                )
            """)

            # Migration: Dashboard-Channel + Message
            for col, ddl in (
                ("dashboard_channel_id", "INTEGER"),
                ("dashboard_message_id", "INTEGER"),
            ):
                try:
                    await db.execute(
                        f"ALTER TABLE guild_config ADD COLUMN {col} {ddl}"
                    )
                except Exception:
                    pass  # bereits vorhanden

            await db.commit()

    # ============================================================
    #  GUILD CONFIG
    # ============================================================
    async def get_guild_config(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
            )
            row = await cur.fetchone()
            if row is None:
                # Standardwerte einfügen, damit jede Spalte existiert
                await db.execute(
                    "INSERT INTO guild_config (guild_id) VALUES (?)",
                    (guild_id,),
                )
                await db.commit()
                cur = await db.execute(
                    "SELECT * FROM guild_config WHERE guild_id = ?",
                    (guild_id,),
                )
                row = await cur.fetchone()
            return dict(row)

    async def update_guild_config(self, guild_id: int, **kwargs) -> None:
        if not kwargs:
            return
        await self.get_guild_config(guild_id)  # ensure row exists
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE guild_config SET {set_clause} WHERE guild_id = ?",
                list(kwargs.values()) + [guild_id],
            )
            await db.commit()

    async def next_ticket_number(self, guild_id: int) -> int:
        await self.get_guild_config(guild_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE guild_config SET ticket_counter = ticket_counter + 1 "
                "WHERE guild_id = ?",
                (guild_id,),
            )
            cur = await db.execute(
                "SELECT ticket_counter FROM guild_config WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cur.fetchone()
            await db.commit()
            return row[0]

    # ============================================================
    #  TICKETS
    # ============================================================
    async def create_ticket(
        self,
        user_id: int,
        guild_id: int,
        channel_id: int,
        ticket_number: int,
        category_name: Optional[str] = None,
    ) -> int:
        now = _now()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT INTO tickets
                   (ticket_number, user_id, guild_id, channel_id, category_name,
                    status, priority, opened_at, last_activity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticket_number, user_id, guild_id, channel_id, category_name,
                 config.STATUS_OPEN, "mittel", now, now),
            )
            await db.commit()
            return cur.lastrowid

    async def get_open_tickets_for_user(
        self, user_id: int, guild_id: int
    ) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM tickets
                   WHERE user_id = ? AND guild_id = ? AND status != ?
                   ORDER BY id DESC""",
                (user_id, guild_id, config.STATUS_CLOSED),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_open_ticket_for_user(
        self, user_id: int, guild_id: int
    ) -> Optional[dict]:
        rows = await self.get_open_tickets_for_user(user_id, guild_id)
        return rows[0] if rows else None

    async def get_ticket_by_channel(self, channel_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_ticket(self, ticket_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_ticket(self, ticket_id: int, **kwargs) -> None:
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE tickets SET {set_clause} WHERE id = ?",
                list(kwargs.values()) + [ticket_id],
            )
            await db.commit()

    async def touch_ticket(self, ticket_id: int) -> None:
        await self.update_ticket(ticket_id, last_activity=_now())

    async def close_ticket(self, ticket_id: int, closed_by: int) -> None:
        await self.update_ticket(
            ticket_id,
            status=config.STATUS_CLOSED,
            closed_at=_now(),
            closed_by_id=closed_by,
        )

    async def list_open_tickets(self, guild_id: int) -> list:
        """Alle offenen Tickets eines Servers, sortiert nach Priorität+Alter."""
        prio_order = "CASE priority "\
                     "WHEN 'dringend' THEN 0 "\
                     "WHEN 'hoch' THEN 1 "\
                     "WHEN 'mittel' THEN 2 "\
                     "WHEN 'niedrig' THEN 3 ELSE 4 END"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"""SELECT * FROM tickets
                    WHERE guild_id = ? AND status != ?
                    ORDER BY {prio_order} ASC, opened_at ASC""",
                (guild_id, config.STATUS_CLOSED),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_inactive_open_tickets(self, hours: int) -> list:
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        ).isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM tickets
                   WHERE status != ? AND last_activity < ?""",
                (config.STATUS_CLOSED, cutoff),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def add_participating_mod(self, ticket_id: int, mod_id: int) -> None:
        ticket = await self.get_ticket(ticket_id)
        if not ticket:
            return
        existing = (ticket.get("participating_mods") or "").split(",")
        existing = [x for x in existing if x]
        if str(mod_id) not in existing:
            existing.append(str(mod_id))
            await self.update_ticket(
                ticket_id, participating_mods=",".join(existing)
            )

    # ============================================================
    #  MESSAGES
    # ============================================================
    async def add_message(
        self,
        ticket_id: int,
        author_id: int,
        author_name: str,
        content: str,
        is_mod: bool = False,
        is_internal: bool = False,
        is_anonymous: bool = False,
        attachments: str = "",
        bot_dm_msg_id: Optional[int] = None,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT INTO messages
                   (ticket_id, author_id, author_name, is_mod, is_internal,
                    is_anonymous, content, attachments, created_at, bot_dm_msg_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticket_id, author_id, author_name,
                 int(is_mod), int(is_internal), int(is_anonymous),
                 content, attachments, _now(), bot_dm_msg_id),
            )
            await db.commit()
            return cur.lastrowid

    async def get_messages(self, ticket_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM messages WHERE ticket_id = ? ORDER BY id ASC",
                (ticket_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_bot_dm_message_ids(self, ticket_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """SELECT bot_dm_msg_id FROM messages
                   WHERE ticket_id = ? AND bot_dm_msg_id IS NOT NULL""",
                (ticket_id,),
            )
            return [r[0] for r in await cur.fetchall()]

    # ============================================================
    #  BLACKLIST
    # ============================================================
    async def add_blacklist(self, user_id: int, reason: str, by_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO blacklist VALUES (?, ?, ?, ?)",
                (user_id, reason, by_id, _now()),
            )
            await db.commit()

    async def remove_blacklist(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
            await db.commit()

    async def is_blacklisted(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)
            )
            return await cur.fetchone() is not None

    async def get_blacklist(self) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM blacklist ORDER BY added_at DESC"
            )
            return [dict(r) for r in await cur.fetchall()]

    # ============================================================
    #  MOD-AKTIVITÄT
    # ============================================================
    async def log_activity(
        self,
        guild_id: int,
        mod_id: int,
        action: str,
        ticket_id: Optional[int] = None,
        details: str = "",
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO mod_activity
                   (guild_id, mod_id, action, ticket_id, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, mod_id, action, ticket_id, details, _now()),
            )
            await db.commit()

    async def get_mod_activity(
        self,
        guild_id: int,
        mod_id: Optional[int] = None,
        limit: int = 25,
    ) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if mod_id is not None:
                cur = await db.execute(
                    """SELECT * FROM mod_activity
                       WHERE guild_id = ? AND mod_id = ?
                       ORDER BY id DESC LIMIT ?""",
                    (guild_id, mod_id, limit),
                )
            else:
                cur = await db.execute(
                    """SELECT * FROM mod_activity WHERE guild_id = ?
                       ORDER BY id DESC LIMIT ?""",
                    (guild_id, limit),
                )
            return [dict(r) for r in await cur.fetchall()]

    # ============================================================
    #  MOD PROFILE / RÄNGE / ABWESENHEIT
    # ============================================================
    async def get_mod_profile(self, guild_id: int, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM mod_profile WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_mod_profile(
        self,
        guild_id: int,
        user_id: int,
        rank: Optional[str] = None,
        is_absent: Optional[int] = None,
    ) -> None:
        existing = await self.get_mod_profile(guild_id, user_id)
        async with aiosqlite.connect(self.path) as db:
            if existing is None:
                await db.execute(
                    """INSERT INTO mod_profile (guild_id, user_id, rank, is_absent)
                       VALUES (?, ?, ?, ?)""",
                    (guild_id, user_id, rank or config.RANK_SUPPORTER,
                     int(is_absent) if is_absent is not None else 0),
                )
            else:
                fields = {}
                if rank is not None:
                    fields["rank"] = rank
                if is_absent is not None:
                    fields["is_absent"] = int(is_absent)
                if fields:
                    set_clause = ", ".join(f"{k} = ?" for k in fields)
                    await db.execute(
                        f"UPDATE mod_profile SET {set_clause} "
                        "WHERE guild_id = ? AND user_id = ?",
                        list(fields.values()) + [guild_id, user_id],
                    )
            await db.commit()

    async def get_available_mods_round_robin(self, guild_id: int) -> list:
        """Liefert alle nicht-abwesenden Mods, sortiert nach last_assigned_at."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM mod_profile
                   WHERE guild_id = ? AND is_absent = 0
                   ORDER BY COALESCE(last_assigned_at, '') ASC""",
                (guild_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def mark_mod_assigned(self, guild_id: int, user_id: int) -> None:
        await self.upsert_mod_profile(guild_id, user_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE mod_profile SET last_assigned_at = ? "
                "WHERE guild_id = ? AND user_id = ?",
                (_now(), guild_id, user_id),
            )
            await db.commit()

    async def get_all_mod_profiles(self, guild_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM mod_profile WHERE guild_id = ?",
                (guild_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    # ============================================================
    #  TICKET-KATEGORIEN
    # ============================================================
    async def list_categories(self, guild_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM ticket_categories WHERE guild_id = ? ORDER BY id",
                (guild_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def add_category(
        self, guild_id: int, name: str, description: str = "", emoji: str = ""
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT INTO ticket_categories (guild_id, name, description, emoji)
                   VALUES (?, ?, ?, ?)""",
                (guild_id, name, description, emoji),
            )
            await db.commit()
            return cur.lastrowid

    async def remove_category(self, category_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM ticket_categories WHERE id = ?", (category_id,)
            )
            await db.commit()

    async def ensure_default_categories(self, guild_id: int) -> None:
        existing = await self.list_categories(guild_id)
        if existing:
            return
        for name, desc, emoji in config.DEFAULT_TICKET_CATEGORIES:
            await self.add_category(guild_id, name, desc, emoji)

    # ============================================================
    #  TEMPLATES
    # ============================================================
    async def list_templates(self, guild_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM templates WHERE guild_id = ? ORDER BY name",
                (guild_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def add_template(self, guild_id: int, name: str, content: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO templates (guild_id, name, content)
                   VALUES (?, ?, ?)""",
                (guild_id, name, content),
            )
            await db.commit()

    async def remove_template(self, guild_id: int, name: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM templates WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            )
            await db.commit()

    async def get_template(self, guild_id: int, name: str) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM templates WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    # ============================================================
    #  SNIPPETS (gespeicherte Antworten)
    # ============================================================
    async def list_snippets(self, guild_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM snippets WHERE guild_id = ? ORDER BY name ASC",
                (guild_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def add_snippet(
        self, guild_id: int, name: str, content: str, created_by: int
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO snippets
                   (guild_id, name, content, created_by, created_at, use_count)
                   VALUES (?, ?, ?, ?, ?,
                           COALESCE((SELECT use_count FROM snippets
                                     WHERE guild_id = ? AND name = ?), 0))""",
                (guild_id, name, content, created_by, _now(), guild_id, name),
            )
            await db.commit()

    async def remove_snippet(self, guild_id: int, name: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM snippets WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_snippet(self, guild_id: int, name: str) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM snippets WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def increment_snippet_use(self, guild_id: int, name: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE snippets SET use_count = use_count + 1 "
                "WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            )
            await db.commit()

    # ============================================================
    #  RATINGS
    # ============================================================
    async def add_rating(
        self, ticket_id: int, rating: int, comment: str = ""
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO ratings
                   (ticket_id, rating, comment, created_at)
                   VALUES (?, ?, ?, ?)""",
                (ticket_id, rating, comment, _now()),
            )
            await db.commit()

    async def get_average_rating(
        self, guild_id: int, mod_id: Optional[int] = None
    ) -> Optional[float]:
        async with aiosqlite.connect(self.path) as db:
            if mod_id is None:
                cur = await db.execute(
                    """SELECT AVG(r.rating) FROM ratings r
                       JOIN tickets t ON r.ticket_id = t.id
                       WHERE t.guild_id = ?""",
                    (guild_id,),
                )
            else:
                cur = await db.execute(
                    """SELECT AVG(r.rating) FROM ratings r
                       JOIN tickets t ON r.ticket_id = t.id
                       WHERE t.guild_id = ? AND
                       (t.assigned_mod_id = ? OR t.closed_by_id = ?)""",
                    (guild_id, mod_id, mod_id),
                )
            row = await cur.fetchone()
            return row[0]

    # ============================================================
    #  STATISTIKEN
    # ============================================================
    async def stats_overview(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            now = datetime.datetime.utcnow()
            today_iso = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            week_iso = (now - datetime.timedelta(days=7)).isoformat()
            month_iso = (now - datetime.timedelta(days=30)).isoformat()

            async def _count(query, args):
                cur = await db.execute(query, args)
                row = await cur.fetchone()
                return row[0] if row else 0

            stats = {
                "open": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status != ?",
                    (guild_id, config.STATUS_CLOSED),
                ),
                "closed": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = ?",
                    (guild_id, config.STATUS_CLOSED),
                ),
                "assigned": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND assigned_mod_id IS NOT NULL AND status != ?",
                    (guild_id, config.STATUS_CLOSED),
                ),
                "today": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND opened_at >= ?",
                    (guild_id, today_iso),
                ),
                "week": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND opened_at >= ?",
                    (guild_id, week_iso),
                ),
                "month": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND opened_at >= ?",
                    (guild_id, month_iso),
                ),
            }

            # Häufigste Kategorien
            cur = await db.execute(
                """SELECT category_name, COUNT(*) as cnt FROM tickets
                   WHERE guild_id = ? AND category_name IS NOT NULL
                   GROUP BY category_name ORDER BY cnt DESC LIMIT 6""",
                (guild_id,),
            )
            stats["categories"] = [
                (r[0], r[1]) for r in await cur.fetchall()
            ]
            stats["avg_response_seconds"] = await self._avg_response_seconds(
                db, guild_id
            )
            return stats

    async def _avg_response_seconds(
        self, db, guild_id: int, mod_id: Optional[int] = None
    ) -> Optional[float]:
        """Durchschnitt zwischen opened_at und first_reply_at."""
        if mod_id is None:
            cur = await db.execute(
                """SELECT opened_at, first_reply_at FROM tickets
                   WHERE guild_id = ? AND first_reply_at IS NOT NULL""",
                (guild_id,),
            )
        else:
            cur = await db.execute(
                """SELECT opened_at, first_reply_at FROM tickets
                   WHERE guild_id = ? AND first_reply_at IS NOT NULL
                   AND (assigned_mod_id = ? OR closed_by_id = ?)""",
                (guild_id, mod_id, mod_id),
            )
        rows = await cur.fetchall()
        if not rows:
            return None
        total = 0.0
        for o, f in rows:
            try:
                d = (
                    datetime.datetime.fromisoformat(f)
                    - datetime.datetime.fromisoformat(o)
                ).total_seconds()
                total += max(0, d)
            except Exception:
                continue
        return total / len(rows) if rows else None

    async def stats_for_mod(self, guild_id: int, mod_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """SELECT COUNT(DISTINCT t.id) FROM tickets t
                   LEFT JOIN messages m ON m.ticket_id = t.id
                   WHERE t.guild_id = ? AND
                   (t.assigned_mod_id = ? OR t.closed_by_id = ?
                    OR m.author_id = ?)""",
                (guild_id, mod_id, mod_id, mod_id),
            )
            handled = (await cur.fetchone())[0]
            avg_resp = await self._avg_response_seconds(db, guild_id, mod_id)
            avg_rating = await self.get_average_rating(guild_id, mod_id)
            profile = await self.get_mod_profile(guild_id, mod_id)
            return {
                "handled": handled,
                "avg_response_seconds": avg_resp,
                "avg_rating": avg_rating,
                "profile": profile,
            }

    async def leaderboard(self, guild_id: int, days: int = 7) -> list:
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(days=days)
        ).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """SELECT author_id, COUNT(*) as cnt FROM messages
                   WHERE is_mod = 1 AND is_internal = 0 AND created_at >= ?
                   AND ticket_id IN (SELECT id FROM tickets WHERE guild_id = ?)
                   GROUP BY author_id
                   ORDER BY cnt DESC LIMIT 10""",
                (cutoff, guild_id),
            )
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def weekly_summary(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            week_iso = (
                datetime.datetime.utcnow() - datetime.timedelta(days=7)
            ).isoformat()

            async def _count(q, a):
                cur = await db.execute(q, a)
                return (await cur.fetchone())[0]

            return {
                "opened": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND opened_at >= ?",
                    (guild_id, week_iso),
                ),
                "closed": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND closed_at >= ?",
                    (guild_id, week_iso),
                ),
                "still_open": await _count(
                    "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status != ?",
                    (guild_id, config.STATUS_CLOSED),
                ),
                "avg_rating": await self.get_average_rating(guild_id),
            }

    # ============================================================
    #  WEEKLY REPORT
    # ============================================================
    async def get_last_report_time(self, guild_id: int) -> Optional[str]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT last_sent_at FROM weekly_reports WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_last_report_time(self, guild_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO weekly_reports
                   (guild_id, last_sent_at) VALUES (?, ?)""",
                (guild_id, _now()),
            )
            await db.commit()
