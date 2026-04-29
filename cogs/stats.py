"""
Cog: Statistiken & Auswertung.
- /stats        Server-Übersicht
- /modstats     Stats für einen einzelnen Mod
- /leaderboard  Aktivste Mods (Woche/Monat)
"""
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

import config
from database import Database
from utils import make_embed, format_seconds, text_bar_chart


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db

    @app_commands.command(name="stats", description="Server-weite Modmail-Statistiken.")
    async def stats_cmd(self, interaction: discord.Interaction):
        s = await self.db.stats_overview(interaction.guild_id)
        avg = await self.db.get_average_rating(interaction.guild_id)
        e = make_embed(
            title="📊 Modmail-Statistiken",
            color=config.COLOR_PRIMARY,
        )
        e.add_field(name="🟢 Offen", value=str(s["open"]), inline=True)
        e.add_field(name="🔴 Geschlossen", value=str(s["closed"]), inline=True)
        e.add_field(name="🎯 Zugewiesen", value=str(s["assigned"]), inline=True)
        e.add_field(name="Heute", value=str(s["today"]), inline=True)
        e.add_field(name="Woche", value=str(s["week"]), inline=True)
        e.add_field(name="Monat", value=str(s["month"]), inline=True)
        e.add_field(
            name="⏱️ Ø Antwortzeit",
            value=format_seconds(s["avg_response_seconds"]),
            inline=True,
        )
        if avg is not None:
            e.add_field(name="⭐ Ø Bewertung", value=f"{avg:.2f} / 5", inline=True)
        e.add_field(
            name="Häufigste Kategorien",
            value=text_bar_chart(s["categories"]),
            inline=False,
        )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="modstats", description="Statistiken für einen Mod.")
    async def modstats_cmd(
        self,
        interaction: discord.Interaction,
        mod: Optional[discord.Member] = None,
    ):
        target = mod or interaction.user
        s = await self.db.stats_for_mod(interaction.guild_id, target.id)
        e = make_embed(
            title=f"📊 Mod-Statistiken – {target.display_name}",
            color=config.COLOR_PRIMARY,
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Bearbeitete Tickets", value=str(s["handled"]), inline=True)
        e.add_field(
            name="Ø Antwortzeit",
            value=format_seconds(s["avg_response_seconds"]),
            inline=True,
        )
        if s["avg_rating"] is not None:
            e.add_field(name="⭐ Ø Bewertung", value=f"{s['avg_rating']:.2f} / 5", inline=True)
        if s.get("profile"):
            rank = s["profile"]["rank"]
            absent = "🌙 Abwesend" if s["profile"].get("is_absent") else "🟢 Verfügbar"
            e.add_field(
                name="Rang & Status",
                value=f"{config.RANK_LABELS.get(rank, rank)} • {absent}",
                inline=False,
            )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="leaderboard", description="Top-Mods nach Antworten.")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Woche", value=7),
            app_commands.Choice(name="Monat", value=30),
        ]
    )
    async def leaderboard_cmd(
        self,
        interaction: discord.Interaction,
        period: Optional[app_commands.Choice[int]] = None,
    ):
        days = period.value if period else 7
        rows = await self.db.leaderboard(interaction.guild_id, days=days)
        if not rows:
            await interaction.response.send_message(
                "Keine Daten vorhanden.", ephemeral=True
            )
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (mod_id, count) in enumerate(rows):
            prefix = medals[i] if i < 3 else f"`#{i+1}`"
            lines.append(f"{prefix} <@{mod_id}> – **{count}** Antworten")
        e = make_embed(
            title=f"🏆 Mod-Leaderboard ({days} Tage)",
            description="\n".join(lines),
            color=config.COLOR_WARNING,
        )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="activity", description="Mod-Aktivitätsprotokoll.")
    @app_commands.describe(mod="Optional: nur Aktivitäten dieses Mods")
    async def activity_cmd(
        self,
        interaction: discord.Interaction,
        mod: Optional[discord.Member] = None,
    ):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ Nur User mit „Server verwalten“ dürfen das.", ephemeral=True
            )
            return
        entries = await self.db.get_mod_activity(
            interaction.guild_id, mod_id=mod.id if mod else None, limit=15
        )
        if not entries:
            await interaction.response.send_message(
                "Keine Aktivitäten gefunden.", ephemeral=True
            )
            return
        lines = []
        for e_ in entries:
            ts = e_["created_at"][:19].replace("T", " ")
            tdetail = f" (#{e_['ticket_id']})" if e_["ticket_id"] else ""
            extra = f" – {e_['details']}" if e_["details"] else ""
            lines.append(
                f"`{ts}` <@{e_['mod_id']}> **{e_['action']}**{tdetail}{extra}"
            )
        await interaction.response.send_message(
            embed=make_embed(
                title="📋 Mod-Aktivität" + (f" – {mod}" if mod else ""),
                description="\n".join(lines),
                color=config.COLOR_PRIMARY,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot, bot.db))
