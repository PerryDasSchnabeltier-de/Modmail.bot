"""
Cog: Modmail-Kernlogik.

- DM vom User → Kategorie-Auswahl per Dropdown (in DM)
- Bot legt eigenen Channel `ticket-NNNN-username` in der Modmail-Kategorie an
- Round-Robin-Zuweisung an einen verfügbaren Mod
- Mods nutzen /reply, /areply, /note für Antworten
- /close erzeugt Transkript, löscht Bot-DMs, archiviert/löscht Channel
- Optional: Bewertungs-Abfrage nach Schließen
"""
import discord
from discord.ext import commands
from discord import app_commands
import datetime
import io
import time
from typing import Optional

import config
from database import Database
from utils import (
    make_embed,
    channel_name_for_ticket,
    thread_user_info_embed,
    ticket_status_embed,
    round_robin_pick,
    is_mod,
    has_min_rank,
)


# Spam-Schutz: Cooldown pro User (RAM)
_last_dm_time: dict[int, float] = {}
_dm_count_window: dict[int, list[float]] = {}
# Pending-Erstnachrichten beim Kategorie-Auswahlflow
_pending_first_messages: dict[int, discord.Message] = {}


def _user_rate_limited(user_id: int) -> bool:
    now = time.time()
    if now - _last_dm_time.get(user_id, 0) < config.USER_DM_COOLDOWN:
        return True
    window = _dm_count_window.setdefault(user_id, [])
    window[:] = [t for t in window if now - t < 60]
    if len(window) >= config.USER_RATE_LIMIT:
        return True
    window.append(now)
    _last_dm_time[user_id] = now
    return False


# ============================================================
#  KATEGORIE-AUSWAHL VIEW (im DM beim ersten Kontakt)
# ============================================================
class CategorySelect(discord.ui.Select):
    def __init__(self, cog: "Modmail", guild: discord.Guild, categories: list):
        self.cog = cog
        self.guild = guild
        options = [
            discord.SelectOption(
                label=c["name"][:100],
                description=(c.get("description") or "")[:100] or None,
                emoji=c.get("emoji") or None,
                value=str(c["id"]),
            )
            for c in categories[:25]
        ]
        super().__init__(
            placeholder="Wähle eine Kategorie …",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        category_id = int(self.values[0])
        categories = await self.cog.db.list_categories(self.guild.id)
        category = next((c for c in categories if c["id"] == category_id), None)
        category_name = category["name"] if category else None

        first_msg = _pending_first_messages.pop(interaction.user.id, None)
        await interaction.response.edit_message(
            content=f"Kategorie **{category_name}** ausgewählt – dein Ticket wird erstellt …",
            view=None,
        )
        await self.cog.create_ticket_for(
            user=interaction.user,
            guild=self.guild,
            category_name=category_name,
            first_message=first_msg,
        )


class CategoryView(discord.ui.View):
    def __init__(self, cog: "Modmail", guild: discord.Guild, categories: list):
        super().__init__(timeout=300)
        self.add_item(CategorySelect(cog, guild, categories))


# ============================================================
#  BEWERTUNGS-VIEW (im DM nach Schließen)
# ============================================================
class RatingView(discord.ui.View):
    def __init__(self, db: Database, ticket_id: int, lang: str):
        super().__init__(timeout=86400)
        self.db = db
        self.ticket_id = ticket_id
        self.lang = lang
        for stars in range(1, 6):
            self.add_item(self._RateButton(stars))

    class _RateButton(discord.ui.Button):
        def __init__(self, stars: int):
            super().__init__(
                label=str(stars),
                emoji="⭐",
                style=discord.ButtonStyle.secondary,
            )
            self.stars = stars

        async def callback(self, interaction: discord.Interaction):
            view: "RatingView" = self.view  # type: ignore
            await view.db.add_rating(view.ticket_id, self.stars)
            await interaction.response.edit_message(
                content=f"⭐ {config.t(view.lang, 'rate_thanks')}",
                view=None,
            )


# ============================================================
#  PANEL-VIEW (persistent – Button im öffentlichen Channel)
# ============================================================
class PanelView(discord.ui.View):
    """Persistenter Button im öffentlichen Ticket-Panel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket öffnen",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="dpc:panel:open",
    )
    async def open_btn(
        self, interaction: discord.Interaction, _btn: discord.ui.Button
    ):
        cog = interaction.client.get_cog("Modmail")
        if cog is None:
            await interaction.response.send_message(
                "Bot startet noch – bitte gleich nochmal probieren.",
                ephemeral=True,
            )
            return
        await cog.handle_panel_click(interaction)


class PanelCategorySelect(discord.ui.Select):
    """Kategorie-Auswahl direkt nach Klick auf das Panel (ephemeral)."""

    def __init__(self, cog: "Modmail", guild: discord.Guild, categories: list):
        self.cog = cog
        self.guild = guild
        options = [
            discord.SelectOption(
                label=c["name"][:100],
                description=(c.get("description") or "")[:100] or None,
                emoji=c.get("emoji") or None,
                value=str(c["id"]),
            )
            for c in categories[:25]
        ]
        super().__init__(
            placeholder="Wähle eine Kategorie …",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        category_id = int(self.values[0])
        categories = await self.cog.db.list_categories(self.guild.id)
        category = next((c for c in categories if c["id"] == category_id), None)
        category_name = category["name"] if category else None

        await interaction.response.edit_message(
            content=(
                f"✅ Kategorie **{category_name}** gewählt – "
                f"schau in deine **DMs** für dein Ticket!"
            ),
            embed=None,
            view=None,
        )
        await self.cog.create_ticket_for(
            user=interaction.user,
            guild=self.guild,
            category_name=category_name,
            first_message=None,
        )


class PanelCategoryView(discord.ui.View):
    def __init__(self, cog: "Modmail", guild: discord.Guild, categories: list):
        super().__init__(timeout=300)
        self.add_item(PanelCategorySelect(cog, guild, categories))


# ============================================================
#  TICKET-CONTROL-VIEW (persistent – Buttons im Ticket-Channel)
# ============================================================
class TicketControlView(discord.ui.View):
    """Action-Buttons unter dem Status-Embed jedes Tickets (für Mods)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim",
        emoji="🎯",
        style=discord.ButtonStyle.success,
        custom_id="dpc:tc:claim",
    )
    async def claim_btn(self, interaction: discord.Interaction, _btn):
        cog = interaction.client.get_cog("Modmail")
        if cog:
            await cog.btn_claim(interaction)

    @discord.ui.button(
        label="Notiz",
        emoji="📝",
        style=discord.ButtonStyle.secondary,
        custom_id="dpc:tc:note",
    )
    async def note_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(_NoteModal())

    @discord.ui.button(
        label="Priorität",
        emoji="🎚️",
        style=discord.ButtonStyle.secondary,
        custom_id="dpc:tc:prio",
    )
    async def prio_btn(self, interaction: discord.Interaction, _btn):
        cog = interaction.client.get_cog("Modmail")
        if cog:
            await cog.btn_priority(interaction)

    @discord.ui.button(
        label="Schließen",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="dpc:tc:close",
    )
    async def close_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(_CloseModal())


class _CloseModal(discord.ui.Modal, title="Ticket schließen"):
    reason = discord.ui.TextInput(
        label="Grund (optional)",
        required=False,
        max_length=300,
        style=discord.TextStyle.paragraph,
        placeholder="Optional – warum wird das Ticket geschlossen?",
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Modmail")
        if cog:
            await cog.do_close(interaction, self.reason.value or None)


class _NoteModal(discord.ui.Modal, title="Interne Notiz"):
    text = discord.ui.TextInput(
        label="Notiz",
        required=True,
        max_length=1000,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Modmail")
        if cog:
            await cog.do_note(interaction, self.text.value)


class _PriorityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        placeholder="Priorität wählen …",
        options=[
            discord.SelectOption(label="🟢 Niedrig",  value="niedrig"),
            discord.SelectOption(label="🟡 Mittel",   value="mittel"),
            discord.SelectOption(label="🟠 Hoch",     value="hoch"),
            discord.SelectOption(label="🔴 Dringend", value="dringend"),
        ],
    )
    async def prio_select(self, interaction: discord.Interaction, select):
        cog = interaction.client.get_cog("Modmail")
        if cog:
            await cog.do_set_priority(interaction, select.values[0])


# ============================================================
#  HAUPT-COG
# ============================================================
class Modmail(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db

    # --------------------------------------------------------
    #  DM EMPFANGEN
    # --------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Mod-Nachricht in einem Ticket-Channel? -> auto-forward
        if (
            isinstance(message.channel, discord.TextChannel)
            and config.AUTO_FORWARD_MOD_MESSAGES
            and message.guild is not None
        ):
            await self._maybe_forward_mod_message(message)
            return

        if not isinstance(message.channel, discord.DMChannel):
            return

        user = message.author

        if await self.db.is_blacklisted(user.id):
            try:
                guild = self._first_configured_guild()
                lang = await self._lang_for_guild(guild.id) if guild else "de"
                await user.send(config.t(lang, "blacklisted"))
            except discord.HTTPException:
                pass
            return

        if _user_rate_limited(user.id):
            return

        guild = self._first_configured_guild()
        if guild is None:
            try:
                await user.send(config.t("de", "no_modmail"))
            except discord.HTTPException:
                pass
            return

        cfg = await self.db.get_guild_config(guild.id)
        lang = cfg.get("language") or "de"

        # Existiert bereits ein offenes Ticket?
        ticket = await self.db.get_open_ticket_for_user(user.id, guild.id)
        if ticket:
            await self._forward_user_message(ticket, user, message)
            return

        # Cooldown / max-open prüfen
        open_count = len(await self.db.get_open_tickets_for_user(user.id, guild.id))
        if open_count >= (cfg.get("max_open_tickets_per_user") or 1):
            try:
                await user.send(config.t(lang, "max_open"))
            except discord.HTTPException:
                pass
            return

        # Kategorie-Auswahl?
        categories = await self.db.list_categories(guild.id)
        if categories:
            _pending_first_messages[user.id] = message
            view = CategoryView(self, guild, categories)
            try:
                await user.send(
                    embed=make_embed(
                        title="📋 " + config.t(lang, "pick_category"),
                        description=(
                            "Damit dein Ticket dem richtigen Team zugewiesen "
                            "werden kann, wähle bitte eine Kategorie."
                        ),
                        color=config.COLOR_INFO,
                    ),
                    view=view,
                )
            except discord.HTTPException:
                _pending_first_messages.pop(user.id, None)
        else:
            await self.create_ticket_for(
                user=user, guild=guild, category_name=None, first_message=message
            )

    def _first_configured_guild(self) -> Optional[discord.Guild]:
        for g in self.bot.guilds:
            return g
        return None

    async def _lang_for_guild(self, guild_id: int) -> str:
        cfg = await self.db.get_guild_config(guild_id)
        return cfg.get("language") or "de"

    # --------------------------------------------------------
    #  TICKET ERSTELLEN
    # --------------------------------------------------------
    async def create_ticket_for(
        self,
        user: discord.User,
        guild: discord.Guild,
        category_name: Optional[str],
        first_message: Optional[discord.Message],
    ) -> None:
        cfg = await self.db.get_guild_config(guild.id)
        lang = cfg.get("language") or "de"

        category_id = cfg.get("modmail_category_id")
        if not category_id:
            try:
                await user.send(config.t(lang, "no_modmail"))
            except discord.HTTPException:
                pass
            return
        category_channel = guild.get_channel(category_id)
        if not isinstance(category_channel, discord.CategoryChannel):
            try:
                await user.send(config.t(lang, "no_modmail"))
            except discord.HTTPException:
                pass
            return

        ticket_number = await self.db.next_ticket_number(guild.id)
        channel_name = channel_name_for_ticket(ticket_number, user.name)

        # Permissions: nur Mods + Bot dürfen den Channel sehen
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                manage_channels=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),
        }
        if cfg.get("mod_role_id"):
            mod_role = guild.get_role(cfg["mod_role_id"])
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                    attach_files=True,
                )

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category_channel,
                overwrites=overwrites,
                topic=f"Modmail-Ticket #{ticket_number:04d} • User: {user} ({user.id})"
                + (f" • Kategorie: {category_name}" if category_name else ""),
                reason=f"Modmail-Ticket für {user}",
            )
        except discord.HTTPException as exc:
            print(f"[Modmail] Channel-Create fehlgeschlagen: {exc}")
            try:
                await user.send("❌ Fehler beim Erstellen deines Tickets.")
            except discord.HTTPException:
                pass
            return

        ticket_db_id = await self.db.create_ticket(
            user_id=user.id,
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            ticket_number=ticket_number,
            category_name=category_name,
        )

        # Round-Robin-Zuweisung
        assigned: Optional[discord.Member] = await round_robin_pick(self.db, guild)
        if assigned:
            await self.db.update_ticket(
                ticket_db_id,
                assigned_mod_id=assigned.id,
                status=config.STATUS_IN_PROGRESS,
            )
            await self.db.mark_mod_assigned(guild.id, assigned.id)

        # Ping-Rolle (sichtbarer Ping ÜBER dem Embed)
        if cfg.get("ping_role_id"):
            role = guild.get_role(cfg["ping_role_id"])
            if role:
                await ticket_channel.send(
                    content=f"🔔 {role.mention} – neues Ticket!",
                    allowed_mentions=discord.AllowedMentions(roles=[role]),
                )

        # User-Info-Embed
        member = guild.get_member(user.id)
        await ticket_channel.send(embed=thread_user_info_embed(user, member))
        ticket = await self.db.get_ticket(ticket_db_id)
        await ticket_channel.send(
            embed=ticket_status_embed(ticket, user, lang),
            view=TicketControlView(),
        )

        if assigned:
            await ticket_channel.send(
                embed=make_embed(
                    title="🎯 Automatisch zugewiesen",
                    description=f"Dieser Ticket wurde {assigned.mention} zugewiesen (Round-Robin).",
                    color=config.COLOR_INFO,
                )
            )

        # Erste Nachricht weiterleiten
        if first_message is not None:
            await self._forward_user_message(ticket, user, first_message)

        # Welcome an User
        if cfg.get("welcome_enabled", 1):
            try:
                w = await user.send(config.t(lang, "welcome"))
                await self.db.add_message(
                    ticket_id=ticket_db_id,
                    author_id=self.bot.user.id,
                    author_name=str(self.bot.user),
                    content=f"[Welcome] {config.t(lang, 'welcome')}",
                    is_mod=True,
                    bot_dm_msg_id=w.id,
                )
            except discord.HTTPException:
                pass

        try:
            await user.send(config.t(lang, "ticket_created"))
        except discord.HTTPException:
            pass

        await self.db.log_activity(
            guild_id=guild.id,
            mod_id=user.id,
            action="ticket_opened",
            ticket_id=ticket_db_id,
            details=f"Kategorie: {category_name or '-'}",
        )
        await self.update_dashboard(guild)
        if assigned:
            ticket_obj = await self.db.get_ticket(ticket_db_id)
            await self._notify_user_claimed(ticket_obj, assigned, guild)

    # --------------------------------------------------------
    #  USER-NACHRICHT WEITERLEITEN
    # --------------------------------------------------------
    async def _forward_user_message(
        self, ticket: dict, user: discord.User, message: discord.Message
    ) -> None:
        guild = self.bot.get_guild(ticket["guild_id"])
        if guild is None:
            return
        channel = guild.get_channel(ticket["channel_id"])
        if channel is None:
            return

        embed = make_embed(
            description=message.content or "_(keine Textnachricht)_",
            color=config.COLOR_USER,
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_footer(text=f"User-ID: {user.id}")

        files = []
        att_str = ""
        if message.attachments:
            att_str = "\n".join(a.url for a in message.attachments)
            embed.add_field(name="📎 Anhänge", value=att_str[:1024], inline=False)
            for a in message.attachments[:5]:
                try:
                    files.append(await a.to_file())
                except discord.HTTPException:
                    pass

        await channel.send(embed=embed, files=files)
        await self.db.add_message(
            ticket_id=ticket["id"],
            author_id=user.id,
            author_name=str(user),
            content=message.content or "",
            is_mod=False,
            attachments=att_str,
        )
        await self.db.touch_ticket(ticket["id"])
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass

    # --------------------------------------------------------
    #  Auto-Forward: Mod schreibt einfach im Ticket-Channel
    # --------------------------------------------------------
    async def _maybe_forward_mod_message(self, message: discord.Message) -> None:
        # Im Ticket-Channel?
        ticket = await self.db.get_ticket_by_channel(message.channel.id)
        if ticket is None:
            return
        if ticket["status"] == config.STATUS_CLOSED:
            return

        # Slash-/Prefix-Command? Ignorieren.
        content = message.content or ""
        if content.startswith(self.bot.command_prefix):
            return
        # "Stille" Mod-Diskussion?
        if any(content.startswith(p) for p in config.SILENT_PREFIXES):
            return
        # Leere Nachrichten ohne Anhang ignorieren
        if not content.strip() and not message.attachments:
            return

        # Nur Mods dürfen forwarden
        if not await is_mod(self.db, message.author, message.guild.id):
            return

        # User holen
        user = self.bot.get_user(ticket["user_id"])
        if user is None:
            try:
                user = await self.bot.fetch_user(ticket["user_id"])
            except discord.HTTPException:
                return

        cfg = await self.db.get_guild_config(message.guild.id)
        anonymous = bool(cfg.get("anonymous_default"))

        # Embed an User
        dm_embed = make_embed(
            description=content or "_(nur Anhang)_",
            color=config.COLOR_MOD_ANON if anonymous else config.COLOR_MOD,
        )
        if anonymous:
            dm_embed.set_author(name="Mod-Team")
        else:
            dm_embed.set_author(
                name=str(message.author),
                icon_url=message.author.display_avatar.url,
            )

        # Anhänge mitnehmen (max 5)
        files = []
        for a in message.attachments[:5]:
            try:
                files.append(await a.to_file())
            except discord.HTTPException:
                pass

        try:
            sent = await user.send(embed=dm_embed, files=files)
        except discord.HTTPException:
            try:
                await message.reply(
                    "❌ Konnte dem User keine DM schicken (DMs vermutlich aus).",
                    mention_author=False,
                )
                await message.add_reaction("❌")
            except discord.HTTPException:
                pass
            return

        # Original-Mod-Nachricht durch hübsches Embed ersetzen
        confirm = make_embed(
            description=content,
            color=config.COLOR_MOD_ANON if anonymous else config.COLOR_MOD,
        )
        prefix = "🕶️ Anonyme Antwort" if anonymous else "💬 Antwort"
        confirm.set_author(
            name=f"{prefix} – {message.author}",
            icon_url=message.author.display_avatar.url,
        )
        if message.attachments:
            confirm.add_field(
                name="📎 Anhänge",
                value="\n".join(a.url for a in message.attachments)[:1024],
                inline=False,
            )
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        await message.channel.send(embed=confirm)

        # Tracking
        update_kwargs = {}
        if ticket.get("first_reply_at") is None:
            update_kwargs["first_reply_at"] = datetime.datetime.utcnow().isoformat()
        if ticket.get("status") == config.STATUS_OPEN:
            update_kwargs["status"] = config.STATUS_IN_PROGRESS
        if update_kwargs:
            await self.db.update_ticket(ticket["id"], **update_kwargs)

        await self.db.add_message(
            ticket_id=ticket["id"],
            author_id=message.author.id,
            author_name=str(message.author),
            content=content,
            is_mod=True,
            is_anonymous=anonymous,
            bot_dm_msg_id=sent.id,
        )
        await self.db.touch_ticket(ticket["id"])
        await self.db.add_participating_mod(ticket["id"], message.author.id)
        await self.db.log_activity(
            guild_id=message.guild.id,
            mod_id=message.author.id,
            action="reply_anonymous" if anonymous else "reply",
            ticket_id=ticket["id"],
        )

    # --------------------------------------------------------
    #  HELPER: Mod-Check für Slash-Commands
    # --------------------------------------------------------
    async def _ticket_from_interaction(
        self, interaction: discord.Interaction
    ) -> Optional[dict]:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Nur in Ticket-Channels nutzbar.", ephemeral=True
            )
            return None
        ticket = await self.db.get_ticket_by_channel(interaction.channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "❌ Dies ist kein Modmail-Ticket-Channel.", ephemeral=True
            )
            return None
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Du bist kein Moderator.", ephemeral=True
            )
            return None
        return ticket

    # --------------------------------------------------------
    #  /reply, /areply, /note
    # --------------------------------------------------------
    async def _send_reply(
        self, interaction: discord.Interaction, message: str, anonymous: bool
    ):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        if ticket["status"] == config.STATUS_CLOSED:
            await interaction.response.send_message(
                "❌ Ticket ist geschlossen. Nutze `/open`.", ephemeral=True
            )
            return

        user = self.bot.get_user(ticket["user_id"]) or await self.bot.fetch_user(
            ticket["user_id"]
        )
        cfg = await self.db.get_guild_config(interaction.guild_id)
        lang = cfg.get("language") or "de"

        dm_embed = make_embed(description=message, color=config.COLOR_MOD)
        if anonymous:
            dm_embed.set_author(name="Mod-Team")
            dm_embed.color = config.COLOR_MOD_ANON
        else:
            dm_embed.set_author(
                name=str(interaction.user),
                icon_url=interaction.user.display_avatar.url,
            )

        try:
            sent = await user.send(embed=dm_embed)
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Konnte dem User keine DM schicken.", ephemeral=True
            )
            return

        # Erste Antwort? Response-Time tracken
        update_kwargs = {}
        if ticket.get("first_reply_at") is None:
            update_kwargs["first_reply_at"] = datetime.datetime.utcnow().isoformat()
        if ticket.get("status") == config.STATUS_OPEN:
            update_kwargs["status"] = config.STATUS_IN_PROGRESS
        if update_kwargs:
            await self.db.update_ticket(ticket["id"], **update_kwargs)

        confirm = make_embed(
            description=message,
            color=config.COLOR_MOD_ANON if anonymous else config.COLOR_MOD,
        )
        prefix = "🕶️ Anonyme Antwort" if anonymous else "💬 Antwort"
        confirm.set_author(
            name=f"{prefix} – {interaction.user}",
            icon_url=interaction.user.display_avatar.url,
        )
        await interaction.response.send_message(embed=confirm)

        await self.db.add_message(
            ticket_id=ticket["id"],
            author_id=interaction.user.id,
            author_name=str(interaction.user),
            content=message,
            is_mod=True,
            is_anonymous=anonymous,
            bot_dm_msg_id=sent.id,
        )
        await self.db.touch_ticket(ticket["id"])
        await self.db.add_participating_mod(ticket["id"], interaction.user.id)
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="reply_anonymous" if anonymous else "reply",
            ticket_id=ticket["id"],
        )

    @app_commands.command(name="reply", description="Antworte dem User namentlich.")
    @app_commands.describe(message="Deine Antwort")
    async def reply_cmd(self, interaction: discord.Interaction, message: str):
        await self._send_reply(interaction, message, anonymous=False)

    @app_commands.command(name="areply", description="Antworte anonym (als 'Mod-Team').")
    @app_commands.describe(message="Deine Antwort")
    async def areply_cmd(self, interaction: discord.Interaction, message: str):
        await self._send_reply(interaction, message, anonymous=True)

    @app_commands.command(name="note", description="Interne Notiz – nur für Mods sichtbar.")
    @app_commands.describe(text="Deine Notiz")
    async def note_cmd(self, interaction: discord.Interaction, text: str):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        embed = make_embed(description=text, color=config.COLOR_NOTE)
        embed.set_author(
            name=f"📝 Notiz – {interaction.user}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.set_footer(text="Nur intern – nicht an User gesendet")
        await interaction.response.send_message(embed=embed)
        await self.db.add_message(
            ticket_id=ticket["id"],
            author_id=interaction.user.id,
            author_name=str(interaction.user),
            content=text,
            is_mod=True,
            is_internal=True,
        )
        await self.db.touch_ticket(ticket["id"])

    # --------------------------------------------------------
    #  /reply_template
    # --------------------------------------------------------
    @app_commands.command(name="reply_template", description="Antworte mit einer Vorlage.")
    @app_commands.describe(name="Name der Vorlage", anonymous="Anonym antworten?")
    async def reply_template_cmd(
        self, interaction: discord.Interaction, name: str, anonymous: bool = False
    ):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        tpl = await self.db.get_template(interaction.guild_id, name)
        if not tpl:
            await interaction.response.send_message(
                f"❌ Vorlage `{name}` nicht gefunden.", ephemeral=True
            )
            return
        await self._send_reply(interaction, tpl["content"], anonymous=anonymous)

    # --------------------------------------------------------
    #  /close, /open
    # --------------------------------------------------------
    @app_commands.command(name="close", description="Schließt das aktuelle Ticket.")
    @app_commands.describe(reason="Optionaler Grund")
    async def close_cmd(
        self, interaction: discord.Interaction, reason: Optional[str] = None
    ):
        await self.do_close(interaction, reason)

    async def do_close(
        self, interaction: discord.Interaction, reason: Optional[str]
    ):
        """Gemeinsame Schließ-Logik für /close und Schließen-Button."""
        if interaction.guild is None:
            return
        ticket = await self.db.get_ticket_by_channel(interaction.channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "❌ Dies ist kein Ticket-Channel.", ephemeral=True
            )
            return
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Du bist kein Moderator.", ephemeral=True
            )
            return
        if ticket["status"] == config.STATUS_CLOSED:
            await interaction.response.send_message(
                "ℹ️ Ticket ist bereits geschlossen.", ephemeral=True
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        cfg = await self.db.get_guild_config(interaction.guild_id)
        lang = cfg.get("language") or "de"
        user: Optional[discord.User] = None
        try:
            user = self.bot.get_user(ticket["user_id"]) or await self.bot.fetch_user(
                ticket["user_id"]
            )
        except discord.HTTPException:
            pass

        # Transkript
        transcript = await self._build_transcript(ticket["id"])

        # In Log-Channel
        if cfg.get("log_channel_id"):
            log_channel = self.bot.get_channel(cfg["log_channel_id"])
            if log_channel:
                file = discord.File(
                    io.BytesIO(transcript.encode("utf-8")),
                    filename=f"transkript-ticket-{ticket['ticket_number']:04d}.txt",
                )
                e = make_embed(
                    title=f"📂 Ticket #{ticket['ticket_number']:04d} geschlossen",
                    color=config.COLOR_ERROR,
                )
                e.add_field(name="User", value=f"<@{ticket['user_id']}>", inline=True)
                e.add_field(
                    name="Geschlossen von",
                    value=interaction.user.mention,
                    inline=True,
                )
                if reason:
                    e.add_field(name="Grund", value=reason, inline=False)
                if ticket.get("category_name"):
                    e.add_field(
                        name="Kategorie",
                        value=ticket["category_name"],
                        inline=True,
                    )
                await log_channel.send(embed=e, file=file)

        # User-DM + DM-Verlauf-Löschung + Bewertungs-Abfrage
        if user:
            try:
                await user.send(config.t(lang, "close"))
            except discord.HTTPException:
                pass
            if cfg.get("delete_dm_on_close", 1):
                await self._purge_bot_dms(user)
            if cfg.get("rating_enabled", 0):
                try:
                    await user.send(
                        embed=make_embed(
                            title="⭐ " + config.t(lang, "rate_request"),
                            color=config.COLOR_INFO,
                        ),
                        view=RatingView(self.db, ticket["id"], lang),
                    )
                except discord.HTTPException:
                    pass

        await self.db.close_ticket(ticket["id"], interaction.user.id)
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="ticket_closed",
            ticket_id=ticket["id"],
            details=reason or "",
        )

        await interaction.followup.send(
            embed=make_embed(
                title="🔒 Ticket geschlossen",
                description=(
                    f"Geschlossen von {interaction.user.mention}"
                    + (f"\n**Grund:** {reason}" if reason else "")
                ),
                color=config.COLOR_ERROR,
            )
        )

        # Channel löschen oder umbenennen?
        if cfg.get("delete_channel_on_close", 0):
            try:
                await interaction.channel.delete(reason="Ticket geschlossen")
            except discord.HTTPException:
                pass
        else:
            try:
                await interaction.channel.edit(
                    name=f"closed-{ticket['ticket_number']:04d}"
                )
            except discord.HTTPException:
                pass

        await self.update_dashboard(interaction.guild)

    @app_commands.command(name="open", description="Öffnet ein geschlossenes Ticket erneut.")
    async def open_cmd(self, interaction: discord.Interaction):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        await self.db.update_ticket(
            ticket["id"], status=config.STATUS_OPEN, closed_at=None
        )
        await self.db.touch_ticket(ticket["id"])
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="ticket_reopened",
            ticket_id=ticket["id"],
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="🔓 Ticket wieder geöffnet",
                description=f"Wieder geöffnet von {interaction.user.mention}",
                color=config.COLOR_SUCCESS,
            )
        )

    # --------------------------------------------------------
    #  /assign, /priority, /tag, /info, /join, /transfer, /abwesend
    # --------------------------------------------------------
    @app_commands.command(name="assign", description="Weise das Ticket einem Mod zu.")
    async def assign_cmd(
        self, interaction: discord.Interaction, mod: discord.Member
    ):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        if not await has_min_rank(
            self.db, interaction.user, interaction.guild_id, config.RANK_MODERATOR
        ):
            await interaction.response.send_message(
                "❌ Nur Moderatoren+ dürfen zuweisen.", ephemeral=True
            )
            return
        await self.db.update_ticket(
            ticket["id"],
            assigned_mod_id=mod.id,
            status=config.STATUS_IN_PROGRESS,
        )
        await self.db.add_participating_mod(ticket["id"], mod.id)
        await self.db.mark_mod_assigned(interaction.guild_id, mod.id)
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="assigned",
            ticket_id=ticket["id"],
            details=f"-> {mod}",
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="🎯 Zugewiesen",
                description=f"Ticket wurde {mod.mention} zugewiesen.",
                color=config.COLOR_WARNING,
            )
        )
        await self._notify_user_claimed(ticket, mod, interaction.guild)
        await self.update_dashboard(interaction.guild)

    @app_commands.command(name="join", description="Tritt diesem Ticket als zusätzlicher Mod bei.")
    async def join_cmd(self, interaction: discord.Interaction):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        await self.db.add_participating_mod(ticket["id"], interaction.user.id)
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="joined",
            ticket_id=ticket["id"],
        )
        await interaction.response.send_message(
            embed=make_embed(
                description=f"✅ {interaction.user.mention} ist diesem Ticket beigetreten.",
                color=config.COLOR_SUCCESS,
            )
        )

    @app_commands.command(name="transfer", description="Übergib das Ticket an einen anderen Mod.")
    async def transfer_cmd(
        self, interaction: discord.Interaction, mod: discord.Member
    ):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        old_mod_id = ticket.get("assigned_mod_id")
        await self.db.update_ticket(ticket["id"], assigned_mod_id=mod.id)
        await self.db.add_participating_mod(ticket["id"], mod.id)
        await self.db.mark_mod_assigned(interaction.guild_id, mod.id)
        # Interne Notiz dazu
        await self.db.add_message(
            ticket_id=ticket["id"],
            author_id=interaction.user.id,
            author_name=str(interaction.user),
            content=(
                f"Ticket übergeben: <@{old_mod_id}> → {mod.mention}"
                if old_mod_id
                else f"Ticket übergeben an {mod.mention}"
            ),
            is_mod=True,
            is_internal=True,
        )
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="transferred",
            ticket_id=ticket["id"],
            details=f"-> {mod}",
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="🔄 Übergeben",
                description=f"Ticket wurde an {mod.mention} übergeben.",
                color=config.COLOR_INFO,
            )
        )
        await self._notify_user_claimed(ticket, mod, interaction.guild)
        await self.update_dashboard(interaction.guild)

    @app_commands.command(
        name="abwesend",
        description="Markiere dich als abwesend (keine automatischen Zuweisungen).",
    )
    async def absent_cmd(self, interaction: discord.Interaction):
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Du bist kein Moderator.", ephemeral=True
            )
            return
        profile = await self.db.get_mod_profile(
            interaction.guild_id, interaction.user.id
        )
        new_state = 0 if (profile and profile.get("is_absent")) else 1
        await self.db.upsert_mod_profile(
            interaction.guild_id, interaction.user.id, is_absent=new_state
        )
        await interaction.response.send_message(
            embed=make_embed(
                description=(
                    "🌙 Du bist jetzt **abwesend**."
                    if new_state
                    else "🟢 Du bist wieder **verfügbar**."
                ),
                color=config.COLOR_INFO,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="priority", description="Setze die Priorität.")
    @app_commands.choices(
        level=[
            app_commands.Choice(name="🟢 Niedrig", value="niedrig"),
            app_commands.Choice(name="🟡 Mittel", value="mittel"),
            app_commands.Choice(name="🟠 Hoch", value="hoch"),
            app_commands.Choice(name="🔴 Dringend", value="dringend"),
        ]
    )
    async def priority_cmd(
        self,
        interaction: discord.Interaction,
        level: app_commands.Choice[str],
    ):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        await self.db.update_ticket(ticket["id"], priority=level.value)

        # Eskalations-Ping?
        cfg = await self.db.get_guild_config(interaction.guild_id)
        ping = ""
        if level.value == "dringend" and cfg.get("escalation_role_id"):
            role = interaction.guild.get_role(cfg["escalation_role_id"])
            if role:
                ping = role.mention

        await interaction.response.send_message(
            content=ping or None,
            embed=make_embed(
                title="🎚️ Priorität geändert",
                description=(
                    f"Neue Priorität: {config.PRIORITY_EMOJI[level.value]} "
                    f"**{level.value.capitalize()}**"
                ),
                color=config.PRIORITY_COLOR[level.value],
            ),
        )

    @app_commands.command(name="tag", description="Setze ein freies Label/Tag.")
    @app_commands.describe(label="Tag-Bezeichnung")
    async def tag_cmd(self, interaction: discord.Interaction, label: str):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        await self.db.update_ticket(ticket["id"], tag=label)
        await interaction.response.send_message(
            embed=make_embed(
                title="🏷️ Tag gesetzt",
                description=f"Tag: **{label}**",
                color=config.COLOR_PRIMARY,
            )
        )

    @app_commands.command(name="info", description="Zeigt Infos zum aktuellen Ticket.")
    async def info_cmd(self, interaction: discord.Interaction):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        try:
            user = self.bot.get_user(ticket["user_id"]) or await self.bot.fetch_user(
                ticket["user_id"]
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ User nicht gefunden.", ephemeral=True
            )
            return
        cfg = await self.db.get_guild_config(interaction.guild_id)
        lang = cfg.get("language") or "de"
        await interaction.response.send_message(
            embed=ticket_status_embed(ticket, user, lang)
        )

    # --------------------------------------------------------
    #  TRANSKRIPT + DM-PURGE
    # --------------------------------------------------------
    async def _build_transcript(self, ticket_id: int) -> str:
        ticket = await self.db.get_ticket(ticket_id)
        messages = await self.db.get_messages(ticket_id)
        lines = [
            f"=== Modmail Ticket #{ticket['ticket_number']:04d} ===",
            f"User-ID:     {ticket['user_id']}",
            f"Kategorie:   {ticket.get('category_name') or '-'}",
            f"Geöffnet:    {ticket['opened_at']}",
            f"Geschlossen: {ticket.get('closed_at') or '(jetzt)'}",
            f"Status:      {ticket['status']}",
            f"Priorität:   {ticket['priority']}",
            f"Tag:         {ticket.get('tag') or '-'}",
            "=" * 50,
            "",
        ]
        for m in messages:
            role = "MOD" if m["is_mod"] else "USER"
            tags = []
            if m["is_internal"]:
                tags.append("INTERN")
            if m["is_anonymous"]:
                tags.append("ANONYM")
            tag_str = f" [{','.join(tags)}]" if tags else ""
            lines.append(f"[{m['created_at']}] {role}{tag_str} {m['author_name']}:")
            lines.append(m["content"] or "")
            if m["attachments"]:
                lines.append(f"  Anhänge: {m['attachments']}")
            lines.append("")
        return "\n".join(lines)

    # --------------------------------------------------------
    #  DASHBOARD (Live-Embed im Dashboard-Channel)
    # --------------------------------------------------------
    PRIO_EMOJI = {
        "dringend": "🔴",
        "hoch": "🟠",
        "mittel": "🟡",
        "niedrig": "🟢",
    }

    async def _build_dashboard_embed(
        self, guild: discord.Guild
    ) -> discord.Embed:
        tickets = await self.db.list_open_tickets(guild.id)
        cfg = await self.db.get_guild_config(guild.id)
        now = datetime.datetime.utcnow()

        e = make_embed(
            title="📊 Modmail Dashboard",
            description=(
                f"**{len(tickets)}** offene{' Tickets' if len(tickets) != 1 else 's Ticket'}"
                f" auf **{guild.name}**"
            ),
            color=config.COLOR_PRIMARY,
        )
        e.set_footer(text=f"Zuletzt aktualisiert · {now.strftime('%d.%m.%Y %H:%M')} UTC")
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)

        if not tickets:
            e.add_field(
                name="🎉 Alles erledigt",
                value="Keine offenen Tickets gerade.",
                inline=False,
            )
            return e

        # Status-Übersicht
        unclaimed = [t for t in tickets if not t.get("assigned_mod_id")]
        claimed = len(tickets) - len(unclaimed)
        urgent = sum(1 for t in tickets if t["priority"] == "dringend")
        e.add_field(
            name="🟢 Übersicht",
            value=(
                f"Übernommen: **{claimed}**\n"
                f"Offen (unclaimed): **{len(unclaimed)}**\n"
                f"🔴 Dringend: **{urgent}**"
            ),
            inline=True,
        )

        # Top 10 Tickets als Liste
        lines = []
        for t in tickets[:10]:
            try:
                opened = datetime.datetime.fromisoformat(t["opened_at"])
                age_min = max(1, int((now - opened).total_seconds() // 60))
                if age_min < 60:
                    age = f"{age_min}m"
                elif age_min < 60 * 24:
                    age = f"{age_min // 60}h"
                else:
                    age = f"{age_min // (60 * 24)}d"
            except Exception:
                age = "?"
            mod_str = (
                f"<@{t['assigned_mod_id']}>"
                if t.get("assigned_mod_id")
                else "_offen_"
            )
            channel_str = (
                f"<#{t['channel_id']}>"
                if t.get("channel_id")
                else f"#{t['ticket_number']:04d}"
            )
            prio = self.PRIO_EMOJI.get(t["priority"], "⚪")
            cat = f" • {t['category_name']}" if t.get("category_name") else ""
            lines.append(
                f"{prio} {channel_str} – <@{t['user_id']}> · {mod_str} · `{age}`{cat}"
            )
        e.add_field(
            name=f"🎫 Tickets ({min(10, len(tickets))} von {len(tickets)})",
            value="\n".join(lines),
            inline=False,
        )
        return e

    async def update_dashboard(self, guild: Optional[discord.Guild]) -> None:
        """Aktualisiert (oder erstellt) das Dashboard-Embed im konfigurierten Channel."""
        if guild is None:
            return
        cfg = await self.db.get_guild_config(guild.id)
        ch_id = cfg.get("dashboard_channel_id")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = await self._build_dashboard_embed(guild)
        msg_id = cfg.get("dashboard_message_id")
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed)
                return
            except (discord.NotFound, discord.HTTPException):
                pass
        try:
            sent = await channel.send(embed=embed)
            await self.db.update_guild_config(
                guild.id, dashboard_message_id=sent.id
            )
        except discord.HTTPException as exc:
            print(f"[Dashboard] {exc}")

    @app_commands.command(
        name="dashboard",
        description="Postet/aktualisiert das Live-Dashboard im konfigurierten Channel.",
    )
    async def dashboard_cmd(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Nur in einem Server.", ephemeral=True
            )
            return
        cfg = await self.db.get_guild_config(interaction.guild_id)
        if not cfg.get("dashboard_channel_id"):
            await interaction.response.send_message(
                "⚠️ Es ist noch kein Dashboard-Channel gesetzt. "
                "Nutze `/setup` → 📬 Modmail → Dashboard-Channel wählen.",
                ephemeral=True,
            )
            return
        await self.update_dashboard(interaction.guild)
        await interaction.response.send_message(
            f"✅ Dashboard aktualisiert in <#{cfg['dashboard_channel_id']}>.",
            ephemeral=True,
        )

    @app_commands.command(
        name="queue",
        description="Liste aller offenen Tickets (sortiert nach Priorität).",
    )
    async def queue_cmd(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Nur für Moderatoren.", ephemeral=True
            )
            return
        embed = await self._build_dashboard_embed(interaction.guild)
        embed.title = "📋 Offene Tickets (Queue)"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --------------------------------------------------------
    #  SNIPPETS (gespeicherte Antworten)
    # --------------------------------------------------------
    async def _snippet_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        snips = await self.db.list_snippets(interaction.guild_id)
        cur = (current or "").lower()
        return [
            app_commands.Choice(name=s["name"], value=s["name"])
            for s in snips
            if cur in s["name"].lower()
        ][:25]

    @app_commands.command(
        name="snippet_add",
        description="Speichert eine wiederverwendbare Antwort.",
    )
    @app_commands.describe(
        name="Kurzname (z.B. 'regeln')", content="Was der Bot senden soll"
    )
    async def snippet_add_cmd(
        self, interaction: discord.Interaction, name: str, content: str
    ):
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Nur für Moderatoren.", ephemeral=True
            )
            return
        name = name.strip().lower()
        if len(name) > 32:
            await interaction.response.send_message(
                "❌ Name zu lang (max 32 Zeichen).", ephemeral=True
            )
            return
        await self.db.add_snippet(
            interaction.guild_id, name, content, interaction.user.id
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="📌 Snippet gespeichert",
                description=f"`{name}` → benutzbar mit `/snippet name:{name}`",
                color=config.COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="snippet_remove",
        description="Löscht eine gespeicherte Antwort.",
    )
    @app_commands.describe(name="Snippet-Name")
    async def snippet_remove_cmd(
        self, interaction: discord.Interaction, name: str
    ):
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Nur für Moderatoren.", ephemeral=True
            )
            return
        ok = await self.db.remove_snippet(interaction.guild_id, name.strip().lower())
        await interaction.response.send_message(
            f"{'🗑️ Gelöscht.' if ok else '❌ Nicht gefunden.'}",
            ephemeral=True,
        )

    @snippet_remove_cmd.autocomplete("name")
    async def _snip_rm_ac(self, interaction, current: str):
        return await self._snippet_autocomplete(interaction, current)

    @app_commands.command(
        name="snippet_list",
        description="Zeigt alle gespeicherten Antworten.",
    )
    async def snippet_list_cmd(self, interaction: discord.Interaction):
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Nur für Moderatoren.", ephemeral=True
            )
            return
        snips = await self.db.list_snippets(interaction.guild_id)
        if not snips:
            await interaction.response.send_message(
                "📭 Noch keine Snippets. Lege eines an mit `/snippet_add`.",
                ephemeral=True,
            )
            return
        lines = []
        for s in snips[:25]:
            preview = s["content"].replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "…"
            lines.append(f"• **`{s['name']}`** ({s['use_count']}×) – {preview}")
        await interaction.response.send_message(
            embed=make_embed(
                title=f"📌 Snippets ({len(snips)})",
                description="\n".join(lines),
                color=config.COLOR_INFO,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="snippet",
        description="Antworte mit einem gespeicherten Snippet (im Ticket-Channel).",
    )
    @app_commands.describe(
        name="Snippet-Name", anonymous="Anonym senden?"
    )
    async def snippet_use_cmd(
        self,
        interaction: discord.Interaction,
        name: str,
        anonymous: bool = False,
    ):
        ticket = await self._ticket_from_interaction(interaction)
        if ticket is None:
            return
        snip = await self.db.get_snippet(interaction.guild_id, name.strip().lower())
        if not snip:
            await interaction.response.send_message(
                f"❌ Snippet `{name}` nicht gefunden.", ephemeral=True
            )
            return
        await self.db.increment_snippet_use(interaction.guild_id, snip["name"])
        await self._send_reply(interaction, snip["content"], anonymous=anonymous)

    @snippet_use_cmd.autocomplete("name")
    async def _snip_use_ac(self, interaction, current: str):
        return await self._snippet_autocomplete(interaction, current)

    async def _purge_bot_dms(self, user: discord.User) -> None:
        try:
            dm = user.dm_channel or await user.create_dm()
            async for msg in dm.history(limit=config.DM_DELETE_LOOKBACK):
                if msg.author.id == self.bot.user.id:
                    try:
                        await msg.delete()
                    except discord.HTTPException:
                        pass
        except discord.HTTPException as exc:
            print(f"[Modmail] DM-Purge: {exc}")

    # --------------------------------------------------------
    #  PANEL: Klick-Handler aus PanelView
    # --------------------------------------------------------
    async def handle_panel_click(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Nur in einem Server möglich.", ephemeral=True
            )
            return

        if await self.db.is_blacklisted(user.id):
            cfg = await self.db.get_guild_config(guild.id)
            await interaction.response.send_message(
                config.t(cfg.get("language") or "de", "blacklisted"),
                ephemeral=True,
            )
            return

        existing = await self.db.get_open_ticket_for_user(user.id, guild.id)
        if existing:
            await interaction.response.send_message(
                "❗ Du hast bereits ein offenes Ticket. "
                "Schreib mir einfach hier per **DM**, um zu antworten.",
                ephemeral=True,
            )
            return

        cfg = await self.db.get_guild_config(guild.id)
        if not cfg.get("modmail_category_id"):
            await interaction.response.send_message(
                "⚠️ Modmail ist auf diesem Server noch nicht eingerichtet.",
                ephemeral=True,
            )
            return

        open_count = len(await self.db.get_open_tickets_for_user(user.id, guild.id))
        if open_count >= (cfg.get("max_open_tickets_per_user") or 1):
            lang = cfg.get("language") or "de"
            await interaction.response.send_message(
                config.t(lang, "max_open"), ephemeral=True
            )
            return

        # Test, ob wir DM schreiben können
        try:
            await (user.dm_channel or await user.create_dm()).send(
                embed=make_embed(
                    description=(
                        "📩 Dein Ticket wird gleich erstellt …\n"
                        "Du chattest **ausschließlich hier per DM** mit dem Bot."
                    ),
                    color=config.COLOR_INFO,
                )
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                ('❌ Ich kann dir keine DM schicken. '
                 'Bitte aktiviere in deinen Discord-Einstellungen '
                 '„Direktnachrichten von Servermitgliedern erlauben".'),
                ephemeral=True,
            )
            return

        categories = await self.db.list_categories(guild.id)
        if categories:
            view = PanelCategoryView(self, guild, categories)
            await interaction.response.send_message(
                embed=make_embed(
                    title="📋 Wähle eine Kategorie",
                    description="Sobald du eine Kategorie wählst, erstelle ich dein Ticket.",
                    color=config.COLOR_INFO,
                ),
                view=view,
                ephemeral=True,
            )
        else:
            await interaction.response.defer(ephemeral=True)
            await self.create_ticket_for(
                user=user,
                guild=guild,
                category_name=None,
                first_message=None,
            )
            await interaction.followup.send(
                "✅ Dein Ticket wurde erstellt – schau in deine **DMs**!",
                ephemeral=True,
            )

    # --------------------------------------------------------
    #  TICKET-CONTROL-BUTTONS (im Channel)
    # --------------------------------------------------------
    async def btn_claim(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return
        ticket = await self.db.get_ticket_by_channel(interaction.channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "❌ Kein Ticket-Channel.", ephemeral=True
            )
            return
        if not await is_mod(self.db, interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "❌ Nur für Moderatoren.", ephemeral=True
            )
            return
        if ticket["status"] == config.STATUS_CLOSED:
            await interaction.response.send_message(
                "ℹ️ Ticket ist geschlossen.", ephemeral=True
            )
            return
        await self.db.update_ticket(
            ticket["id"],
            assigned_mod_id=interaction.user.id,
            status=config.STATUS_IN_PROGRESS,
        )
        await self.db.add_participating_mod(ticket["id"], interaction.user.id)
        await self.db.mark_mod_assigned(interaction.guild_id, interaction.user.id)
        await self.db.log_activity(
            guild_id=interaction.guild_id,
            mod_id=interaction.user.id,
            action="claimed",
            ticket_id=ticket["id"],
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="🎯 Ticket übernommen",
                description=f"{interaction.user.mention} kümmert sich um dieses Ticket.",
                color=config.COLOR_SUCCESS,
            )
        )
        # User per DM informieren, wer übernommen hat
        await self._notify_user_claimed(ticket, interaction.user, interaction.guild)
        # Dashboard aktualisieren
        await self.update_dashboard(interaction.guild)

    async def _notify_user_claimed(
        self,
        ticket: dict,
        mod: discord.Member,
        guild: discord.Guild,
    ) -> None:
        """Sendet dem Ticket-User eine DM mit Info, wer übernommen hat."""
        cfg = await self.db.get_guild_config(guild.id)
        anonymous = bool(cfg.get("anonymous_default"))
        try:
            user = self.bot.get_user(ticket["user_id"]) or await self.bot.fetch_user(
                ticket["user_id"]
            )
        except discord.HTTPException:
            return
        if anonymous:
            embed = make_embed(
                title="🎯 Dein Ticket wird bearbeitet",
                description=(
                    "Ein **Mod** hat dein Ticket übernommen und kümmert sich "
                    "jetzt darum. Du kannst weiter hier per DM antworten."
                ),
                color=config.COLOR_SUCCESS,
            )
        else:
            embed = make_embed(
                title="🎯 Dein Ticket wird bearbeitet",
                description=(
                    f"**{mod.display_name}** hat dein Ticket übernommen und "
                    "kümmert sich jetzt darum.\n"
                    "Du kannst weiter hier per DM antworten."
                ),
                color=config.COLOR_SUCCESS,
            )
            embed.set_thumbnail(url=mod.display_avatar.url)
        try:
            sent = await user.send(embed=embed)
            await self.db.add_message(
                ticket_id=ticket["id"],
                author_id=self.bot.user.id,
                author_name=str(self.bot.user),
                content=f"[System] Übernommen von {mod}",
                is_mod=True,
                bot_dm_msg_id=sent.id,
            )
        except discord.HTTPException:
            pass

    async def do_note(self, interaction: discord.Interaction, text: str):
        if interaction.guild is None:
            return
        ticket = await self.db.get_ticket_by_channel(interaction.channel.id)
        if ticket is None or not await is_mod(
            self.db, interaction.user, interaction.guild_id
        ):
            await interaction.response.send_message(
                "❌ Nicht erlaubt.", ephemeral=True
            )
            return
        embed = make_embed(description=text, color=config.COLOR_NOTE)
        embed.set_author(
            name=f"📝 Notiz – {interaction.user}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.set_footer(text="Nur intern – nicht an User gesendet")
        await interaction.response.send_message(embed=embed)
        await self.db.add_message(
            ticket_id=ticket["id"],
            author_id=interaction.user.id,
            author_name=str(interaction.user),
            content=text,
            is_mod=True,
            is_internal=True,
        )
        await self.db.touch_ticket(ticket["id"])

    async def btn_priority(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return
        ticket = await self.db.get_ticket_by_channel(interaction.channel.id)
        if ticket is None or not await is_mod(
            self.db, interaction.user, interaction.guild_id
        ):
            await interaction.response.send_message(
                "❌ Nicht erlaubt.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Wähle die neue Priorität:",
            view=_PriorityView(),
            ephemeral=True,
        )

    async def do_set_priority(self, interaction: discord.Interaction, level: str):
        ticket = await self.db.get_ticket_by_channel(interaction.channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "❌ Kein Ticket.", ephemeral=True
            )
            return
        await self.db.update_ticket(ticket["id"], priority=level)
        cfg = await self.db.get_guild_config(interaction.guild_id)
        ping = ""
        if level == "dringend" and cfg.get("escalation_role_id"):
            role = interaction.guild.get_role(cfg["escalation_role_id"])
            if role:
                ping = role.mention
        await interaction.response.edit_message(
            content=f"✅ Priorität auf **{level}** gesetzt.", view=None
        )
        await interaction.channel.send(
            content=ping or None,
            embed=make_embed(
                title="🎚️ Priorität geändert",
                description=(
                    f"Neue Priorität: {config.PRIORITY_EMOJI[level]} "
                    f"**{level.capitalize()}** (von {interaction.user.mention})"
                ),
                color=config.PRIORITY_COLOR[level],
            ),
        )
        await self.update_dashboard(interaction.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(Modmail(bot, bot.db))
    # Persistente Views registrieren – damit Buttons nach Restart weiter gehen
    bot.add_view(PanelView())
    bot.add_view(TicketControlView())
