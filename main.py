"""
Einstiegspunkt für den DPC Modmail Bot.
- Startet Flask-Keep-Alive (mit Invite-Link-Seite)
- Lädt alle Cogs
- Synchronisiert Slash-Commands
- Loggt sich bei Discord ein
"""
import asyncio
import os
import sys

import discord
from discord.ext import commands

import config
from database import Database
from keep_alive import keep_alive


# ===== Intents =====
intents = discord.Intents.default()
intents.message_content = True   # MUSS auch im Developer-Portal aktiviert sein!
intents.dm_messages = True
intents.guilds = True
intents.members = True           # MUSS im Developer-Portal aktiviert sein!


class ModmailBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=config.COMMAND_PREFIX,
            intents=intents,
            help_command=None,
        )
        self.db = Database()

    async def setup_hook(self) -> None:
        # Datenbank initialisieren
        await self.db.init()
        print("✓ Datenbank initialisiert")

        # Cogs laden
        for ext in ("cogs.modmail", "cogs.admin", "cogs.stats", "cogs.logging_cog"):
            try:
                await self.load_extension(ext)
                print(f"✓ Cog geladen: {ext}")
            except Exception as exc:
                print(f"✗ Cog konnte nicht geladen werden: {ext} – {exc}")

        # Slash-Commands global synchronisieren
        try:
            synced = await self.tree.sync()
            print(f"✓ {len(synced)} Slash-Commands synchronisiert")
        except Exception as exc:
            print(f"✗ Slash-Sync fehlgeschlagen: {exc}")

    async def on_ready(self):
        print("=" * 50)
        print(f"✓ Eingeloggt als {self.user} (ID: {self.user.id})")
        print(f"✓ Server: {len(self.guilds)}")
        if config.CLIENT_ID:
            invite = (
                "https://discord.com/api/oauth2/authorize"
                f"?client_id={config.CLIENT_ID}"
                f"&permissions={config.INVITE_PERMISSIONS}"
                "&scope=bot%20applications.commands"
            )
            print(f"🔗 Einladungs-Link:\n   {invite}")
        print("=" * 50)
        # Status setzen
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="DM für Modmail • /help",
            )
        )


async def main():
    if not config.BOT_TOKEN:
        print("FEHLER: DISCORD_BOT_TOKEN ist nicht gesetzt (Replit Secrets).")
        sys.exit(1)

    # Webserver für Keep-Alive + Invite-Seite starten
    keep_alive()
    print(f"✓ Web-Server läuft auf Port {config.KEEP_ALIVE_PORT}")

    bot = ModmailBot()
    try:
        await bot.start(config.BOT_TOKEN)
    except discord.LoginFailure:
        print("FEHLER: Token ungültig. Bitte in Replit Secrets prüfen.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
