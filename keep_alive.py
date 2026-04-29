"""
Kleiner Flask-Webserver, der den Bot 24/7 wachhält (UptimeRobot-kompatibel)
und gleichzeitig eine schöne Status-Seite mit dem Einladungs-Link anzeigt.
"""
from flask import Flask
from threading import Thread
import os

import config

app = Flask(__name__)


def _build_invite_url() -> str:
    """OAuth2-Invite-URL für den Bot."""
    client_id = config.CLIENT_ID
    if not client_id:
        return ""
    return (
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions={config.INVITE_PERMISSIONS}"
        "&scope=bot%20applications.commands"
    )


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DPC Modmail Bot</title>
  <style>
    :root {
      --bg: #0b0d12;
      --panel: #14171f;
      --accent: #5865F2;
      --accent-hover: #4752c4;
      --text: #e7e9ee;
      --muted: #8a92a3;
      --border: #232634;
      --ok: #57F287;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0; min-height: 100%;
      background: radial-gradient(circle at 20% 0%, #1a1f2e 0%, var(--bg) 60%);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                   "Helvetica Neue", Arial, sans-serif;
    }
    .wrap {
      max-width: 720px;
      margin: 0 auto;
      padding: 64px 24px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 40px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.4);
    }
    .badge {
      display: inline-flex; align-items: center; gap: 8px;
      background: rgba(87, 242, 135, 0.1);
      color: var(--ok);
      padding: 6px 12px; border-radius: 999px;
      font-size: 13px; font-weight: 600;
      border: 1px solid rgba(87, 242, 135, 0.3);
    }
    .dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--ok);
      box-shadow: 0 0 8px var(--ok);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.5; }
    }
    h1 {
      font-size: 32px; margin: 24px 0 8px; letter-spacing: -0.02em;
    }
    p.lead {
      color: var(--muted); font-size: 16px; line-height: 1.6;
      margin: 0 0 32px;
    }
    .invite-btn {
      display: inline-flex; align-items: center; justify-content: center;
      gap: 10px;
      background: var(--accent); color: white;
      padding: 14px 28px;
      border-radius: 10px;
      font-weight: 600; font-size: 15px;
      text-decoration: none;
      transition: background 0.15s ease, transform 0.15s ease;
    }
    .invite-btn:hover {
      background: var(--accent-hover);
      transform: translateY(-1px);
    }
    .invite-btn svg { width: 20px; height: 20px; }
    .invite-disabled {
      display: inline-block;
      background: #2a2e3b; color: var(--muted);
      padding: 14px 28px; border-radius: 10px;
      font-size: 15px;
    }
    .features {
      margin-top: 40px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }
    .feat {
      background: rgba(255,255,255,0.02);
      border: 1px solid var(--border);
      padding: 14px 16px;
      border-radius: 10px;
      font-size: 14px;
      color: var(--muted);
    }
    .feat strong { color: var(--text); display: block; margin-bottom: 2px; }
    footer {
      margin-top: 32px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }
    code {
      background: rgba(255,255,255,0.05);
      padding: 2px 6px; border-radius: 4px;
      font-size: 13px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <span class="badge"><span class="dot"></span> Bot läuft</span>
      <h1>DPC Modmail Bot</h1>
      <p class="lead">
        Der Bot ist online und bereit. Klicke unten, um ihn auf deinen
        Discord-Server einzuladen.
      </p>

      __INVITE_BUTTON__

      <div class="features">
        <div class="feat"><strong>Modmail-Threads</strong>DM → Mod-Channel</div>
        <div class="feat"><strong>Tags & Prioritäten</strong>Bug, Appeal, dringend …</div>
        <div class="feat"><strong>Auto-Close</strong>Bei Inaktivität</div>
        <div class="feat"><strong>Transkripte</strong>Vollständig gespeichert</div>
        <div class="feat"><strong>Blacklist</strong>Spam-Schutz</div>
        <div class="feat"><strong>Slash-Commands</strong>Modern & komfortabel</div>
      </div>

      <footer>
        Setup im Server: <code>/setup</code> &nbsp;·&nbsp;
        Hilfe: <code>/help</code>
      </footer>
    </div>
  </div>
</body>
</html>"""


@app.route("/")
def home():
    invite = _build_invite_url()
    if invite:
        button = (
            f'<a class="invite-btn" href="{invite}" target="_blank" rel="noopener">'
            '<svg viewBox="0 0 24 24" fill="currentColor">'
            '<path d="M20.317 4.37a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>'
            '</svg>'
            'Auf Discord-Server einladen</a>'
        )
    else:
        button = (
            '<span class="invite-disabled">'
            '⚠️ DISCORD_CLIENT_ID fehlt – bitte in Replit Secrets setzen'
            '</span>'
        )
    return HTML_TEMPLATE.replace("__INVITE_BUTTON__", button)


@app.route("/health")
def health():
    return {"status": "ok", "bot": "DPC Modmail"}


@app.route("/invite")
def invite_redirect():
    invite = _build_invite_url()
    if invite:
        return f'<meta http-equiv="refresh" content="0; url={invite}">'
    return "DISCORD_CLIENT_ID nicht gesetzt", 500


def _free_port(start: int) -> int:
    """Findet einen freien Port ab `start`. Fällt notfalls auf 0 zurück (OS wählt)."""
    import socket
    for p in (start, 8080, 5000, 3000, 8000, 8888):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return 0  # OS wählt freien Port


def _run(port: int) -> None:
    try:
        app.run(host="0.0.0.0", port=port, use_reloader=False)
    except OSError as exc:
        print(f"⚠️  Web-Server konnte nicht starten ({exc}) – Bot läuft trotzdem weiter.")


def keep_alive() -> None:
    """Startet den Webserver in einem Hintergrund-Thread."""
    port = _free_port(config.KEEP_ALIVE_PORT)
    config.KEEP_ALIVE_PORT = port  # für Status-Ausgabe in main.py
    t = Thread(target=_run, args=(port,), daemon=True)
    t.start()
