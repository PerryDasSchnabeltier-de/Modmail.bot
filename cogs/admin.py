"""
Cog: Admin & Einstellungen.

- /setup        Komplette interaktive Einstellungen via Embeds + Buttons + Selects
- /config       Alias für /setup
- /invite       Einladungs-Link
- /help         Übersicht
- /modrank      Rang eines Mods setzen (Admin only)
- /modlist      Liste aller eingetragenen Mods
- /blacklist_*  Blacklist-Verwaltung
- /template_*   Antwort-Vorlagen
- /category_*   Ticket-Kategorien
"""
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

import config
from database import Database
from utils import make_embed, has_min_rank, get_user_rank, is_mod


# ============================================================
#  ADMIN-CHECK
# ============================================================
def admin_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            "❌ Nur Administratoren dürfen das.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


# ============================================================
#  HAUPT-EINSTELLUNGEN-VIEW
# ============================================================
class SettingsMainView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int):
        super().__init__(timeout=600)
        self.db = db
        self.guild_id = guild_id

    async def _embed(self) -> discord.Embed:
        cfg = await self.db.get_guild_config(self.guild_id)
        e = make_embed(
            title="⚙️ DPC Modmail – Einstellungen",
            description=(
                "Wähle eine Kategorie, um die Einstellungen zu öffnen.\n"
                "Alle Änderungen werden sofort gespeichert."
            ),
            color=config.COLOR_PRIMARY,
        )
        e.add_field(
            name="📬 Modmail",
            value=(
                f"Kategorie: {('<#'+str(cfg['modmail_category_id'])+'>') if cfg.get('modmail_category_id') else '_nicht gesetzt_'}\n"
                f"Log-Channel: {('<#'+str(cfg['log_channel_id'])+'>') if cfg.get('log_channel_id') else '_nicht gesetzt_'}\n"
                f"Dashboard: {('<#'+str(cfg['dashboard_channel_id'])+'>') if cfg.get('dashboard_channel_id') else '_aus_'}\n"
                f"Mod-Rolle: {('<@&'+str(cfg['mod_role_id'])+'>') if cfg.get('mod_role_id') else '_nicht gesetzt_'}"
            ),
            inline=False,
        )
        e.add_field(
            name="⏱️ Automatisierung",
            value=(
                f"Auto-Close: **{cfg.get('auto_close_hours', 72)}h**\n"
                f"Cooldown: **{cfg.get('ticket_cooldown_minutes', 0)}min**\n"
                f"Max. offene Tickets/User: **{cfg.get('max_open_tickets_per_user', 1)}**"
            ),
            inline=True,
        )
        e.add_field(
            name="🔔 Benachrichtigungen",
            value=(
                f"Ping-Rolle: {('<@&'+str(cfg['ping_role_id'])+'>') if cfg.get('ping_role_id') else '—'}\n"
                f"Eskalation: {('<@&'+str(cfg['escalation_role_id'])+'>') if cfg.get('escalation_role_id') else '—'}\n"
                f"Erinnerung: **{cfg.get('reminder_hours', 24)}h**"
            ),
            inline=True,
        )
        cats = await self.db.list_categories(self.guild_id)
        e.add_field(
            name="🎫 Ticket-Optionen",
            value=(
                f"Kategorien: **{len(cats)}**\n"
                f"Bewertung: **{'an' if cfg.get('rating_enabled') else 'aus'}**\n"
                f"DM-Verlauf löschen: **{'an' if cfg.get('delete_dm_on_close') else 'aus'}**"
            ),
            inline=True,
        )
        e.add_field(
            name="🌐 Sprache",
            value=f"**{(cfg.get('language') or 'de').upper()}**",
            inline=True,
        )
        return e

    async def render(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="📬 Modmail", style=discord.ButtonStyle.primary, row=0)
    async def b_modmail(self, interaction: discord.Interaction, _btn):
        view = ModmailSettingsView(self.db, self.guild_id, parent=self)
        await interaction.response.edit_message(embed=await view._embed(), view=view)

    @discord.ui.button(label="⏱️ Automatisierung", style=discord.ButtonStyle.primary, row=0)
    async def b_auto(self, interaction: discord.Interaction, _btn):
        view = AutomationSettingsView(self.db, self.guild_id, parent=self)
        await interaction.response.edit_message(embed=await view._embed(), view=view)

    @discord.ui.button(label="🔔 Benachrichtigungen", style=discord.ButtonStyle.primary, row=0)
    async def b_notif(self, interaction: discord.Interaction, _btn):
        view = NotificationSettingsView(self.db, self.guild_id, parent=self)
        await interaction.response.edit_message(embed=await view._embed(), view=view)

    @discord.ui.button(label="🎫 Tickets", style=discord.ButtonStyle.primary, row=1)
    async def b_tickets(self, interaction: discord.Interaction, _btn):
        view = TicketSettingsView(self.db, self.guild_id, parent=self)
        await interaction.response.edit_message(embed=await view._embed(), view=view)

    @discord.ui.button(label="🌐 Sprache", style=discord.ButtonStyle.primary, row=1)
    async def b_lang(self, interaction: discord.Interaction, _btn):
        view = LanguageSettingsView(self.db, self.guild_id, parent=self)
        await interaction.response.edit_message(embed=await view._embed(), view=view)

    @discord.ui.button(label="✖️ Schließen", style=discord.ButtonStyle.danger, row=1)
    async def b_close(self, interaction: discord.Interaction, _btn):
        await interaction.response.edit_message(
            content="Einstellungen geschlossen.", embed=None, view=None
        )


# ============================================================
#  MODMAIL-EINSTELLUNGEN
# ============================================================
class _BackButton(discord.ui.Button):
    def __init__(self, parent: SettingsMainView, row: int = 4):
        super().__init__(label="← Zurück", style=discord.ButtonStyle.secondary, row=row)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.render(interaction)


class ModmailSettingsView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, parent: SettingsMainView):
        super().__init__(timeout=600)
        self.db = db
        self.guild_id = guild_id
        self.parent_view = parent
        self.add_item(_CategorySelect(self))
        self.add_item(_LogChannelSelect(self))
        self.add_item(_DashboardChannelSelect(self))
        self.add_item(_ModRoleSelect(self))
        self.add_item(_BackButton(parent, row=4))
        self.add_item(_ToggleDeleteDM(self))
        self.add_item(_ToggleAnonymous(self))
        self.add_item(_ToggleDeleteChannel(self))
        self.add_item(_ToggleWelcome(self))

    async def _embed(self) -> discord.Embed:
        cfg = await self.db.get_guild_config(self.guild_id)
        e = make_embed(
            title="📬 Modmail-Einstellungen",
            description=(
                "Konfiguriere, wo Tickets erstellt werden, wer sie sehen darf, "
                "und wie der Bot beim Schließen aufräumt."
            ),
            color=config.COLOR_PRIMARY,
        )
        e.add_field(
            name="Modmail-Kategorie",
            value=(
                f"<#{cfg['modmail_category_id']}>"
                if cfg.get("modmail_category_id")
                else "_nicht gesetzt_"
            ),
            inline=False,
        )
        e.add_field(
            name="Log-Channel",
            value=(
                f"<#{cfg['log_channel_id']}>"
                if cfg.get("log_channel_id")
                else "_nicht gesetzt_"
            ),
            inline=False,
        )
        e.add_field(
            name="📊 Dashboard-Channel",
            value=(
                f"<#{cfg['dashboard_channel_id']}>"
                if cfg.get("dashboard_channel_id")
                else "_nicht gesetzt – ohne Dashboard_"
            ),
            inline=False,
        )
        e.add_field(
            name="Mod-Rolle",
            value=(
                f"<@&{cfg['mod_role_id']}>"
                if cfg.get("mod_role_id")
                else "_nicht gesetzt_"
            ),
            inline=False,
        )
        e.add_field(
            name="Optionen",
            value=(
                f"DM-Verlauf löschen: **{'an' if cfg.get('delete_dm_on_close') else 'aus'}**\n"
                f"Channel beim Schließen löschen: **{'an' if cfg.get('delete_channel_on_close') else 'aus'}**\n"
                f"Anonym als Default: **{'an' if cfg.get('anonymous_default') else 'aus'}**\n"
                f"Willkommens-DM: **{'an' if cfg.get('welcome_enabled', 1) else 'aus'}**"
            ),
            inline=False,
        )
        return e

    async def render(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self._embed(), view=self)


class _CategorySelect(discord.ui.ChannelSelect):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(
            channel_types=[discord.ChannelType.category],
            placeholder="Modmail-Kategorie wählen …",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id,
            modmail_category_id=self.values[0].id,
        )
        await self.parent_view.render(interaction)


class _LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            placeholder="Log-Channel wählen …",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id,
            log_channel_id=self.values[0].id,
        )
        await self.parent_view.render(interaction)


class _ModRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(
            placeholder="Mod-Rolle wählen …",
            min_values=1,
            max_values=1,
            row=3,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id,
            mod_role_id=self.values[0].id,
        )
        await self.parent_view.render(interaction)


class _DashboardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            placeholder="Dashboard-Channel wählen (Live-Übersicht) …",
            min_values=1,
            max_values=1,
            row=2,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        # Vorherige Dashboard-Message vergessen, damit ein neuer Post erstellt wird
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id,
            dashboard_channel_id=self.values[0].id,
            dashboard_message_id=None,
        )
        # Sofort posten, damit User direkt das Ergebnis sieht
        cog = interaction.client.get_cog("Modmail")
        if cog:
            await cog.update_dashboard(interaction.guild)
        await self.parent_view.render(interaction)


class _ToggleDeleteDM(discord.ui.Button):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(label="DM-Verlauf an/aus", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        cfg = await self.parent_view.db.get_guild_config(self.parent_view.guild_id)
        new_val = 0 if cfg.get("delete_dm_on_close") else 1
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id, delete_dm_on_close=new_val
        )
        await self.parent_view.render(interaction)


class _ToggleAnonymous(discord.ui.Button):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(label="Anonym-Default an/aus", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        cfg = await self.parent_view.db.get_guild_config(self.parent_view.guild_id)
        new_val = 0 if cfg.get("anonymous_default") else 1
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id, anonymous_default=new_val
        )
        await self.parent_view.render(interaction)


class _ToggleDeleteChannel(discord.ui.Button):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(label="Channel löschen an/aus", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        cfg = await self.parent_view.db.get_guild_config(self.parent_view.guild_id)
        new_val = 0 if cfg.get("delete_channel_on_close") else 1
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id, delete_channel_on_close=new_val
        )
        await self.parent_view.render(interaction)


class _ToggleWelcome(discord.ui.Button):
    def __init__(self, parent: ModmailSettingsView):
        super().__init__(label="Willkommens-DM an/aus", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        cfg = await self.parent_view.db.get_guild_config(self.parent_view.guild_id)
        new_val = 0 if cfg.get("welcome_enabled", 1) else 1
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id, welcome_enabled=new_val
        )
        await self.parent_view.render(interaction)


# ============================================================
#  AUTOMATISIERUNG
# ============================================================
class _NumberModal(discord.ui.Modal):
    def __init__(self, parent: discord.ui.View, field_name: str, label: str, current: int):
        super().__init__(title=f"Wert ändern – {label}")
        self.parent_view = parent
        self.field_name = field_name
        self.input = discord.ui.TextInput(
            label=label,
            default=str(current),
            placeholder="Zahl eingeben",
            required=True,
            max_length=6,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.input.value.strip())
            if value < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Ungültige Zahl.", ephemeral=True
            )
            return
        await self.parent_view.db.update_guild_config(  # type: ignore
            self.parent_view.guild_id, **{self.field_name: value}  # type: ignore
        )
        await self.parent_view.render(interaction)  # type: ignore


class AutomationSettingsView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, parent: SettingsMainView):
        super().__init__(timeout=600)
        self.db = db
        self.guild_id = guild_id
        self.parent_view = parent
        self.add_item(_BackButton(parent))

    async def _embed(self) -> discord.Embed:
        cfg = await self.db.get_guild_config(self.guild_id)
        e = make_embed(
            title="⏱️ Automatisierungen",
            description="Steuere automatische Schließung und Limits.",
            color=config.COLOR_INFO,
        )
        e.add_field(
            name="Auto-Close Inaktivität",
            value=f"**{cfg.get('auto_close_hours', 72)}** Stunden",
            inline=True,
        )
        e.add_field(
            name="Cooldown zwischen Tickets",
            value=f"**{cfg.get('ticket_cooldown_minutes', 0)}** Minuten",
            inline=True,
        )
        e.add_field(
            name="Max. offene Tickets/User",
            value=f"**{cfg.get('max_open_tickets_per_user', 1)}**",
            inline=True,
        )
        return e

    async def render(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="Auto-Close (h)", style=discord.ButtonStyle.primary, row=0)
    async def b_auto(self, interaction: discord.Interaction, _btn):
        cfg = await self.db.get_guild_config(self.guild_id)
        await interaction.response.send_modal(
            _NumberModal(self, "auto_close_hours", "Auto-Close (Stunden)",
                         cfg.get("auto_close_hours", 72))
        )

    @discord.ui.button(label="Cooldown (min)", style=discord.ButtonStyle.primary, row=0)
    async def b_cd(self, interaction: discord.Interaction, _btn):
        cfg = await self.db.get_guild_config(self.guild_id)
        await interaction.response.send_modal(
            _NumberModal(self, "ticket_cooldown_minutes", "Cooldown (Minuten)",
                         cfg.get("ticket_cooldown_minutes", 0))
        )

    @discord.ui.button(label="Max. offene Tickets", style=discord.ButtonStyle.primary, row=0)
    async def b_max(self, interaction: discord.Interaction, _btn):
        cfg = await self.db.get_guild_config(self.guild_id)
        await interaction.response.send_modal(
            _NumberModal(self, "max_open_tickets_per_user", "Max. offene Tickets pro User",
                         cfg.get("max_open_tickets_per_user", 1))
        )


# ============================================================
#  BENACHRICHTIGUNGEN
# ============================================================
class NotificationSettingsView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, parent: SettingsMainView):
        super().__init__(timeout=600)
        self.db = db
        self.guild_id = guild_id
        self.parent_view = parent
        self.add_item(_PingRoleSelect(self))
        self.add_item(_EscalationRoleSelect(self))
        self.add_item(_BackButton(parent, row=3))

    async def _embed(self) -> discord.Embed:
        cfg = await self.db.get_guild_config(self.guild_id)
        e = make_embed(
            title="🔔 Benachrichtigungen",
            color=config.COLOR_WARNING,
        )
        e.add_field(
            name="Ping bei neuem Ticket",
            value=(
                f"<@&{cfg['ping_role_id']}>" if cfg.get("ping_role_id") else "_aus_"
            ),
            inline=False,
        )
        e.add_field(
            name='Eskalations-Rolle (bei "dringend")',
            value=(
                f"<@&{cfg['escalation_role_id']}>"
                if cfg.get("escalation_role_id")
                else "_aus_"
            ),
            inline=False,
        )
        e.add_field(
            name="Erinnerung bei Inaktivität",
            value=f"**{cfg.get('reminder_hours', 24)}h**",
            inline=False,
        )
        return e

    async def render(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="Erinnerung (h)", style=discord.ButtonStyle.primary, row=2)
    async def b_rem(self, interaction: discord.Interaction, _btn):
        cfg = await self.db.get_guild_config(self.guild_id)
        await interaction.response.send_modal(
            _NumberModal(self, "reminder_hours", "Erinnerung (Stunden)",
                         cfg.get("reminder_hours", 24))
        )

    @discord.ui.button(label="Ping-Rolle entfernen", style=discord.ButtonStyle.secondary, row=2)
    async def b_rm_ping(self, interaction: discord.Interaction, _btn):
        await self.db.update_guild_config(self.guild_id, ping_role_id=None)
        await self.render(interaction)

    @discord.ui.button(label="Eskal.-Rolle entfernen", style=discord.ButtonStyle.secondary, row=2)
    async def b_rm_esc(self, interaction: discord.Interaction, _btn):
        await self.db.update_guild_config(self.guild_id, escalation_role_id=None)
        await self.render(interaction)


class _PingRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: NotificationSettingsView):
        super().__init__(
            placeholder="Ping-Rolle bei neuem Ticket …",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id, ping_role_id=self.values[0].id
        )
        await self.parent_view.render(interaction)


class _EscalationRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: NotificationSettingsView):
        super().__init__(
            placeholder="Eskalations-Rolle (für dringende Tickets) …",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.db.update_guild_config(
            self.parent_view.guild_id, escalation_role_id=self.values[0].id
        )
        await self.parent_view.render(interaction)


# ============================================================
#  TICKET-OPTIONEN
# ============================================================
class TicketSettingsView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, parent: SettingsMainView):
        super().__init__(timeout=600)
        self.db = db
        self.guild_id = guild_id
        self.parent_view = parent
        self.add_item(_BackButton(parent))

    async def _embed(self) -> discord.Embed:
        cfg = await self.db.get_guild_config(self.guild_id)
        cats = await self.db.list_categories(self.guild_id)
        tpls = await self.db.list_templates(self.guild_id)
        cats_text = (
            "\n".join(f"• {c.get('emoji') or '•'} **{c['name']}**" for c in cats[:10])
            if cats
            else "_keine_"
        )
        tpls_text = (
            "\n".join(f"• `{t['name']}`" for t in tpls[:10]) if tpls else "_keine_"
        )
        e = make_embed(
            title="🎫 Ticket-Einstellungen",
            color=config.COLOR_PRIMARY,
        )
        e.add_field(name=f"Kategorien ({len(cats)})", value=cats_text, inline=False)
        e.add_field(name=f"Vorlagen ({len(tpls)})", value=tpls_text, inline=False)
        e.add_field(
            name="Bewertung",
            value=f"**{'an' if cfg.get('rating_enabled') else 'aus'}**",
            inline=True,
        )
        e.set_footer(
            text=(
                "Verwalten via Slash-Commands: "
                "/category_add, /category_remove, /template_add, /template_remove"
            )
        )
        return e

    async def render(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="Bewertung an/aus", style=discord.ButtonStyle.secondary, row=0)
    async def b_rate(self, interaction: discord.Interaction, _btn):
        cfg = await self.db.get_guild_config(self.guild_id)
        new_val = 0 if cfg.get("rating_enabled") else 1
        await self.db.update_guild_config(self.guild_id, rating_enabled=new_val)
        await self.render(interaction)

    @discord.ui.button(label="Default-Kategorien anlegen", style=discord.ButtonStyle.primary, row=0)
    async def b_defcat(self, interaction: discord.Interaction, _btn):
        await self.db.ensure_default_categories(self.guild_id)
        await self.render(interaction)


# ============================================================
#  SPRACHE
# ============================================================
class LanguageSettingsView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, parent: SettingsMainView):
        super().__init__(timeout=600)
        self.db = db
        self.guild_id = guild_id
        self.parent_view = parent
        self.add_item(_BackButton(parent))

    async def _embed(self) -> discord.Embed:
        cfg = await self.db.get_guild_config(self.guild_id)
        e = make_embed(
            title="🌐 Sprache",
            description=(
                "Wähle die Sprache für User-Nachrichten (Welcome, Close-Text, "
                "Bewertungs-Abfrage etc.). Mod-Befehle bleiben auf Deutsch."
            ),
            color=config.COLOR_INFO,
        )
        e.add_field(name="Aktuell", value=f"**{(cfg.get('language') or 'de').upper()}**")
        return e

    async def render(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="🇩🇪 Deutsch", style=discord.ButtonStyle.primary, row=0)
    async def b_de(self, interaction: discord.Interaction, _btn):
        await self.db.update_guild_config(self.guild_id, language="de")
        await self.render(interaction)

    @discord.ui.button(label="🇬🇧 English", style=discord.ButtonStyle.primary, row=0)
    async def b_en(self, interaction: discord.Interaction, _btn):
        await self.db.update_guild_config(self.guild_id, language="en")
        await self.render(interaction)


# ============================================================
#  COG
# ============================================================
class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db

    # ---------- /setup, /config ----------
    @app_commands.command(name="setup", description="Öffnet die Bot-Einstellungen.")
    @admin_check()
    async def setup_cmd(self, interaction: discord.Interaction):
        # Default-Kategorien beim ersten Mal anlegen
        await self.db.ensure_default_categories(interaction.guild_id)
        view = SettingsMainView(self.db, interaction.guild_id)
        await interaction.response.send_message(
            embed=await view._embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="config", description="Alias für /setup.")
    @admin_check()
    async def config_cmd(self, interaction: discord.Interaction):
        await self.db.ensure_default_categories(interaction.guild_id)
        view = SettingsMainView(self.db, interaction.guild_id)
        await interaction.response.send_message(
            embed=await view._embed(), view=view, ephemeral=True
        )

    @app_commands.command(
        name="modmailsettings",
        description="Öffnet das vollständige Einstellungs-Menü (alles auf einen Klick).",
    )
    @admin_check()
    async def modmailsettings_cmd(self, interaction: discord.Interaction):
        await self.db.ensure_default_categories(interaction.guild_id)
        view = SettingsMainView(self.db, interaction.guild_id)
        await interaction.response.send_message(
            embed=await view._embed(), view=view, ephemeral=True
        )

    # ---------- /invite ----------
    @app_commands.command(name="invite", description="Zeigt den Bot-Einladungs-Link.")
    async def invite_cmd(self, interaction: discord.Interaction):
        if not config.CLIENT_ID:
            await interaction.response.send_message(
                "❌ DISCORD_CLIENT_ID nicht gesetzt.", ephemeral=True
            )
            return
        url = (
            "https://discord.com/api/oauth2/authorize"
            f"?client_id={config.CLIENT_ID}"
            f"&permissions={config.INVITE_PERMISSIONS}"
            "&scope=bot%20applications.commands"
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="🔗 Bot einladen",
                description=f"[**Klick hier zum Einladen**]({url})",
                color=config.COLOR_PRIMARY,
            ),
            ephemeral=True,
        )

    # ---------- /modrank, /modlist ----------
    @app_commands.command(name="modrank", description="Setze den Rang eines Mods.")
    @app_commands.choices(
        rank=[
            app_commands.Choice(name="🟢 Supporter", value=config.RANK_SUPPORTER),
            app_commands.Choice(name="🟡 Moderator", value=config.RANK_MODERATOR),
            app_commands.Choice(name="🔴 Admin",     value=config.RANK_ADMIN),
        ]
    )
    @admin_check()
    async def modrank_cmd(
        self,
        interaction: discord.Interaction,
        mod: discord.Member,
        rank: app_commands.Choice[str],
    ):
        await self.db.upsert_mod_profile(
            interaction.guild_id, mod.id, rank=rank.value
        )
        await interaction.response.send_message(
            embed=make_embed(
                title="✅ Rang gesetzt",
                description=f"{mod.mention} hat jetzt den Rang **{config.RANK_LABELS[rank.value]}**.",
                color=config.COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="modlist", description="Liste aller eingetragenen Mods.")
    async def modlist_cmd(self, interaction: discord.Interaction):
        profiles = await self.db.get_all_mod_profiles(interaction.guild_id)
        if not profiles:
            await interaction.response.send_message(
                "Keine Mods eingetragen. Nutze `/modrank` zum Anlegen.",
                ephemeral=True,
            )
            return
        lines = []
        for p in profiles:
            absent = " 🌙 abwesend" if p.get("is_absent") else ""
            lines.append(
                f"• <@{p['user_id']}> – {config.RANK_LABELS.get(p['rank'], p['rank'])}{absent}"
            )
        await interaction.response.send_message(
            embed=make_embed(
                title=f"👥 Mods ({len(profiles)})",
                description="\n".join(lines),
                color=config.COLOR_PRIMARY,
            ),
            ephemeral=True,
        )

    # ---------- BLACKLIST ----------
    @app_commands.command(name="blacklist_add", description="Sperrt einen User für Modmail.")
    async def blacklist_add(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: Optional[str] = None,
    ):
        if not await has_min_rank(
            self.db, interaction.user, interaction.guild_id, config.RANK_MODERATOR
        ):
            await interaction.response.send_message(
                "❌ Nur Moderatoren+ dürfen blacklisten.", ephemeral=True
            )
            return
        await self.db.add_blacklist(user.id, reason or "", interaction.user.id)
        await self.db.log_activity(
            interaction.guild_id, interaction.user.id,
            "blacklist_add", details=f"{user} – {reason or '-'}",
        )
        await interaction.response.send_message(
            f"🚫 {user.mention} geblacklistet.", ephemeral=True
        )

    @app_commands.command(name="blacklist_remove", description="Entfernt einen User von der Blacklist.")
    async def blacklist_remove(
        self, interaction: discord.Interaction, user: discord.User
    ):
        if not await has_min_rank(
            self.db, interaction.user, interaction.guild_id, config.RANK_MODERATOR
        ):
            await interaction.response.send_message(
                "❌ Nur Moderatoren+ dürfen das.", ephemeral=True
            )
            return
        await self.db.remove_blacklist(user.id)
        await interaction.response.send_message(
            f"✅ {user.mention} entfernt.", ephemeral=True
        )

    @app_commands.command(name="blacklist_list", description="Zeigt alle geblacklisteten User.")
    async def blacklist_list(self, interaction: discord.Interaction):
        entries = await self.db.get_blacklist()
        if not entries:
            await interaction.response.send_message(
                "Blacklist ist leer.", ephemeral=True
            )
            return
        desc = "\n".join(
            f"• <@{e['user_id']}> – {e.get('reason') or '_kein Grund_'}"
            for e in entries[:25]
        )
        await interaction.response.send_message(
            embed=make_embed(
                title=f"🚫 Blacklist ({len(entries)})",
                description=desc,
                color=config.COLOR_ERROR,
            ),
            ephemeral=True,
        )

    # ---------- KATEGORIEN ----------
    @app_commands.command(name="category_add", description="Neue Ticket-Kategorie anlegen.")
    @admin_check()
    async def cat_add(
        self,
        interaction: discord.Interaction,
        name: str,
        description: Optional[str] = "",
        emoji: Optional[str] = "",
    ):
        await self.db.add_category(
            interaction.guild_id, name, description or "", emoji or ""
        )
        await interaction.response.send_message(
            f"✅ Kategorie **{name}** angelegt.", ephemeral=True
        )

    @app_commands.command(name="category_list", description="Liste aller Ticket-Kategorien.")
    async def cat_list(self, interaction: discord.Interaction):
        cats = await self.db.list_categories(interaction.guild_id)
        if not cats:
            await interaction.response.send_message(
                "Keine Kategorien.", ephemeral=True
            )
            return
        lines = [
            f"`{c['id']:>3}` {c.get('emoji') or '•'} **{c['name']}** – "
            f"{c.get('description') or '_-_'}"
            for c in cats
        ]
        await interaction.response.send_message(
            embed=make_embed(
                title="🎫 Ticket-Kategorien",
                description="\n".join(lines),
                color=config.COLOR_PRIMARY,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="category_remove", description="Ticket-Kategorie löschen.")
    @admin_check()
    async def cat_remove(
        self, interaction: discord.Interaction, category_id: int
    ):
        await self.db.remove_category(category_id)
        await interaction.response.send_message(
            "✅ Kategorie gelöscht.", ephemeral=True
        )

    # ---------- TEMPLATES ----------
    @app_commands.command(name="template_add", description="Neue Antwort-Vorlage speichern.")
    @admin_check()
    async def tpl_add(
        self, interaction: discord.Interaction, name: str, content: str
    ):
        await self.db.add_template(interaction.guild_id, name, content)
        await interaction.response.send_message(
            f"✅ Vorlage `{name}` gespeichert.", ephemeral=True
        )

    @app_commands.command(name="template_list", description="Liste aller Antwort-Vorlagen.")
    async def tpl_list(self, interaction: discord.Interaction):
        tpls = await self.db.list_templates(interaction.guild_id)
        if not tpls:
            await interaction.response.send_message(
                "Keine Vorlagen.", ephemeral=True
            )
            return
        lines = [f"• `{t['name']}` – {t['content'][:60]}…" for t in tpls]
        await interaction.response.send_message(
            embed=make_embed(
                title="📄 Vorlagen",
                description="\n".join(lines),
                color=config.COLOR_PRIMARY,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="template_remove", description="Antwort-Vorlage löschen.")
    @admin_check()
    async def tpl_remove(self, interaction: discord.Interaction, name: str):
        await self.db.remove_template(interaction.guild_id, name)
        await interaction.response.send_message(
            f"✅ Vorlage `{name}` gelöscht.", ephemeral=True
        )

    # ---------- PING-ROLLE ----------
    @app_commands.command(
        name="setpingrole",
        description="Diese Rolle wird bei jedem neuen Ticket gepingt.",
    )
    @app_commands.describe(role="Rolle die gepingt werden soll (leer = aus)")
    @admin_check()
    async def setpingrole_cmd(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ):
        await self.db.update_guild_config(
            interaction.guild_id,
            ping_role_id=role.id if role else None,
        )
        if role:
            await interaction.response.send_message(
                f"✅ Bei jedem neuen Ticket wird ab jetzt {role.mention} gepingt.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                "✅ Ping-Rolle entfernt – es wird niemand mehr gepingt.",
                ephemeral=True,
            )

    # ---------- PANEL ----------
    @app_commands.command(
        name="panel",
        description="Sende ein Ticket-Panel mit Button in diesen Channel.",
    )
    @app_commands.describe(
        title="Optional: Eigener Titel",
        description="Optional: Eigene Beschreibung",
    )
    @admin_check()
    async def panel_cmd(
        self,
        interaction: discord.Interaction,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ):
        # Persistente PanelView aus modmail-Cog importieren
        from cogs.modmail import PanelView

        title = title or "📩 Support – Ticket öffnen"
        description = description or (
            "Klicke unten auf **Ticket öffnen**, um privat mit unserem "
            "Mod-Team zu chatten.\n\n"
            "🔒 **Vollständig privat** – nur du und das Mod-Team sehen "
            "den Verlauf, du chattest ausschließlich per **DM mit dem Bot**."
        )
        embed = make_embed(
            title=title,
            description=description,
            color=config.COLOR_PRIMARY,
        )
        embed.add_field(
            name="So funktioniert's",
            value=(
                "1️⃣ Klicke auf **Ticket öffnen**\n"
                "2️⃣ Wähle eine Kategorie\n"
                "3️⃣ Schreib uns dein Anliegen per **DM**\n"
                "4️⃣ Unser Team meldet sich"
            ),
            inline=False,
        )
        await interaction.channel.send(embed=embed, view=PanelView())
        await interaction.response.send_message(
            "✅ Ticket-Panel gesendet.", ephemeral=True
        )

    # ---------- HELP ----------
    @app_commands.command(name="help", description="Zeigt alle verfügbaren Befehle.")
    async def help_cmd(self, interaction: discord.Interaction):
        e = make_embed(title="📖 DPC Modmail – Befehle", color=config.COLOR_PRIMARY)
        e.add_field(
            name="Tickets (im Ticket-Channel)",
            value=(
                "`/reply` `/areply` `/note` `/reply_template`\n"
                "`/snippet` – Schnellantwort senden\n"
                "`/close` `/open` `/info` `/tag` `/priority`\n"
                "`/assign` `/transfer` `/join`"
            ),
            inline=False,
        )
        e.add_field(
            name="Übersicht",
            value=(
                "`/queue` – Liste aller offenen Tickets\n"
                "`/dashboard` – Live-Dashboard aktualisieren\n"
                "`/abwesend` – Pause-Status umschalten"
            ),
            inline=False,
        )
        e.add_field(
            name="Snippets (gespeicherte Antworten)",
            value=(
                "`/snippet_add` `/snippet_remove`\n"
                "`/snippet_list` – Übersicht\n"
                "`/snippet name:<x>` – im Ticket senden"
            ),
            inline=False,
        )
        e.add_field(
            name="Admin",
            value=(
                "`/setup` `/config` – Einstellungen mit Buttons\n"
                "`/modrank` `/modlist`\n"
                "`/category_add/_list/_remove`\n"
                "`/template_add/_list/_remove`\n"
                "`/blacklist_add/_remove/_list`"
            ),
            inline=False,
        )
        e.add_field(
            name="Statistiken",
            value="`/stats` `/modstats` `/leaderboard` `/activity`",
            inline=False,
        )
        e.add_field(
            name="Allgemein",
            value="`/invite` `/help`",
            inline=False,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot, bot.db))
