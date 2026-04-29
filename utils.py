"""
Hilfsfunktionen, die in mehreren Cogs gebraucht werden.
"""
import discord
import re
import datetime
from typing import Optional

import config
from database import Database


def make_embed(
    title: str = "",
    description: str = "",
    color: int = config.COLOR_PRIMARY,
    footer: bool = True,
) -> discord.Embed:
    """Einheitlich gestaltete Embeds."""
    e = discord.Embed(
        title=title or None,
        description=description or None,
        color=color,
        timestamp=datetime.datetime.utcnow(),
    )
    if footer:
        e.set_footer(text=config.EMBED_FOOTER)
    return e


def sanitize_username(name: str) -> str:
    """Channel-tauglichen Slug aus einem Usernamen bauen."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9_-]", "", name)
    return (name or "user")[:20]


def channel_name_for_ticket(ticket_number: int, username: str) -> str:
    return f"ticket-{ticket_number:04d}-{sanitize_username(username)}"


def format_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


async def get_user_rank(
    db: Database, member: discord.Member, guild_id: int
) -> str:
    """Rang eines Mods. Admin > Moderator > Supporter."""
    if member.guild_permissions.administrator:
        return config.RANK_ADMIN
    profile = await db.get_mod_profile(guild_id, member.id)
    if profile:
        return profile.get("rank") or config.RANK_SUPPORTER
    return config.RANK_SUPPORTER


async def has_min_rank(
    db: Database, member: discord.Member, guild_id: int, required: str
) -> bool:
    rank = await get_user_rank(db, member, guild_id)
    return config.RANK_ORDER.get(rank, 0) >= config.RANK_ORDER.get(required, 0)


async def is_mod(
    db: Database, member: discord.Member, guild_id: int
) -> bool:
    """Hat Mod-Rolle ODER Mod-Profil ODER Admin-Permission."""
    if member.guild_permissions.administrator:
        return True
    cfg = await db.get_guild_config(guild_id)
    if cfg.get("mod_role_id"):
        if any(r.id == cfg["mod_role_id"] for r in member.roles):
            return True
    return await db.get_mod_profile(guild_id, member.id) is not None


def thread_user_info_embed(
    user: discord.User, member: Optional[discord.Member]
) -> discord.Embed:
    e = make_embed(title="👤 User-Info", color=config.COLOR_PRIMARY)
    e.set_thumbnail(url=user.display_avatar.url)
    e.add_field(name="Name", value=f"{user} (`{user.id}`)", inline=False)
    e.add_field(
        name="Account erstellt",
        value=discord.utils.format_dt(user.created_at, "F"),
        inline=False,
    )
    if member:
        if member.joined_at:
            e.add_field(
                name="Server beigetreten",
                value=discord.utils.format_dt(member.joined_at, "F"),
                inline=False,
            )
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        if roles:
            e.add_field(
                name=f"Rollen ({len(roles)})",
                value=" ".join(roles[:15]),
                inline=False,
            )
    return e


def ticket_status_embed(
    ticket: dict, user: discord.User, lang: str = "de"
) -> discord.Embed:
    prio = ticket["priority"]
    color = config.PRIORITY_COLOR.get(prio, config.COLOR_PRIMARY)
    e = make_embed(
        title=f"📬 Ticket #{ticket['ticket_number']:04d}",
        color=color,
    )
    e.add_field(name="User", value=user.mention, inline=True)
    e.add_field(
        name="Status",
        value=config.status_label(lang, ticket["status"]),
        inline=True,
    )
    e.add_field(
        name="Priorität",
        value=f"{config.PRIORITY_EMOJI.get(prio, '')} {prio.capitalize()}",
        inline=True,
    )
    e.add_field(
        name="Kategorie",
        value=ticket.get("category_name") or "—",
        inline=True,
    )
    e.add_field(name="Tag", value=ticket.get("tag") or "—", inline=True)
    if ticket.get("assigned_mod_id"):
        e.add_field(
            name="Zugewiesen an",
            value=f"<@{ticket['assigned_mod_id']}>",
            inline=True,
        )
    return e


async def round_robin_pick(
    db: Database, guild: discord.Guild
) -> Optional[discord.Member]:
    """Wählt den nächsten Mod per Round-Robin (nicht abwesend)."""
    candidates = await db.get_available_mods_round_robin(guild.id)
    for c in candidates:
        member = guild.get_member(c["user_id"])
        if member is not None:
            return member
    return None


def text_bar_chart(items: list, width: int = 14) -> str:
    """Einfaches Text-Balkendiagramm für Embed-Felder."""
    if not items:
        return "_Keine Daten_"
    max_v = max((v for _, v in items), default=1)
    lines = []
    for label, val in items:
        bar = "█" * max(1, int((val / max_v) * width))
        lines.append(f"`{label[:14]:<14}` {bar} `{val}`")
    return "\n".join(lines)
