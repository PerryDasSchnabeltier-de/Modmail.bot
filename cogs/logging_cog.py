"""
Cog: Auto-Close + Wöchentliche Reports.
"""
import discord
from discord.ext import commands, tasks
import datetime

import config
from database import Database
from utils import make_embed, format_seconds


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db
        self.auto_close_task.start()
        self.weekly_report_task.start()

    def cog_unload(self):
        self.auto_close_task.cancel()
        self.weekly_report_task.cancel()

    # --------- AUTO-CLOSE ---------
    @tasks.loop(minutes=config.DEFAULT_AUTO_CLOSE_CHECK_MIN)
    async def auto_close_task(self):
        for guild in list(self.bot.guilds):
            try:
                cfg = await self.db.get_guild_config(guild.id)
                hours = cfg.get("auto_close_hours") or config.DEFAULT_AUTO_CLOSE_HOURS
                inactive = await self.db.get_inactive_open_tickets(hours)
                inactive = [t for t in inactive if t["guild_id"] == guild.id]
                for t in inactive:
                    await self._auto_close_ticket(guild, cfg, t)
            except Exception as exc:
                print(f"[AutoClose] {exc}")

    @auto_close_task.before_loop
    async def before_auto_close(self):
        await self.bot.wait_until_ready()

    async def _auto_close_ticket(self, guild, cfg, ticket):
        lang = cfg.get("language") or "de"
        try:
            user = self.bot.get_user(ticket["user_id"]) or await self.bot.fetch_user(
                ticket["user_id"]
            )
        except discord.HTTPException:
            user = None

        if user:
            try:
                await user.send(config.t(lang, "auto_closed"))
            except discord.HTTPException:
                pass
            if cfg.get("delete_dm_on_close", 1):
                await self._purge_bot_dms(user)

        ch = guild.get_channel(ticket["channel_id"])
        if ch:
            try:
                await ch.send(
                    embed=make_embed(
                        title="⏰ Auto-Close",
                        description=(
                            f"Ticket wegen Inaktivität "
                            f"({cfg.get('auto_close_hours', 72)}h) geschlossen."
                        ),
                        color=config.COLOR_ERROR,
                    )
                )
            except discord.HTTPException:
                pass

            if cfg.get("delete_channel_on_close", 0):
                try:
                    await ch.delete(reason="Auto-Close")
                except discord.HTTPException:
                    pass
            else:
                try:
                    await ch.edit(name=f"closed-{ticket['ticket_number']:04d}")
                except discord.HTTPException:
                    pass

        await self.db.close_ticket(ticket["id"], self.bot.user.id)
        await self.db.log_activity(
            guild_id=guild.id,
            mod_id=self.bot.user.id,
            action="auto_close",
            ticket_id=ticket["id"],
        )

    async def _purge_bot_dms(self, user):
        try:
            dm = user.dm_channel or await user.create_dm()
            async for msg in dm.history(limit=config.DM_DELETE_LOOKBACK):
                if msg.author.id == self.bot.user.id:
                    try:
                        await msg.delete()
                    except discord.HTTPException:
                        pass
        except discord.HTTPException:
            pass

    # --------- WEEKLY REPORT ---------
    @tasks.loop(hours=6)
    async def weekly_report_task(self):
        now = datetime.datetime.utcnow()
        if now.weekday() != 0:  # Montag
            return
        for guild in list(self.bot.guilds):
            try:
                cfg = await self.db.get_guild_config(guild.id)
                if not cfg.get("log_channel_id"):
                    continue
                last = await self.db.get_last_report_time(guild.id)
                if last:
                    last_dt = datetime.datetime.fromisoformat(last)
                    if (now - last_dt).total_seconds() < 6 * 24 * 3600:
                        continue  # zu früh

                summary = await self.db.weekly_summary(guild.id)
                top = await self.db.leaderboard(guild.id, days=7)
                ch = self.bot.get_channel(cfg["log_channel_id"])
                if not ch:
                    continue

                e = make_embed(
                    title="📅 Wöchentlicher Modmail-Report",
                    color=config.COLOR_INFO,
                )
                e.add_field(name="Neue Tickets", value=str(summary["opened"]), inline=True)
                e.add_field(
                    name="Geschlossen", value=str(summary["closed"]), inline=True
                )
                e.add_field(
                    name="Noch offen", value=str(summary["still_open"]), inline=True
                )
                if summary["avg_rating"] is not None:
                    e.add_field(
                        name="⭐ Ø Bewertung",
                        value=f"{summary['avg_rating']:.2f} / 5",
                        inline=True,
                    )
                if top:
                    medals = ["🥇", "🥈", "🥉"]
                    top_text = "\n".join(
                        f"{medals[i] if i < 3 else f'#{i+1}'} <@{m}> – {c}"
                        for i, (m, c) in enumerate(top[:5])
                    )
                    e.add_field(name="🏆 Top-Mods (7 Tage)", value=top_text, inline=False)

                await ch.send(embed=e)
                await self.db.set_last_report_time(guild.id)
            except Exception as exc:
                print(f"[WeeklyReport] {exc}")

    @weekly_report_task.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingCog(bot, bot.db))
