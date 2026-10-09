# -*- coding: utf-8 -*-
"""Slack-Anbindung — dasselbe wie Telegram und Discord, über eine eigene Slack-App.

Befehle, Kopplung und Freigaben stammen vom Telegram-Boten; hier steckt nur,
was an Slack anders ist:

- **Socket Mode**: Slack schickt Ereignisse über eine WebSocket-Verbindung, die
  Dive on Wide selbst aufbaut — es braucht keine öffentlich erreichbare Adresse.
  Dafür zwei Schlüssel: der Bot-Schlüssel (`xoxb-…`) zum Senden und der
  App-Schlüssel (`xapp-…`, Scope `connections:write`) für die Verbindung.
- Jede Nachricht muss mit ihrer `envelope_id` bestätigt werden, sonst schickt
  Slack sie erneut.
- **Nur Direktnachrichten** an die App (`channel_type: im`) — wie bei Discord.
- Knöpfe sind Block-Kit-Buttons; die Rückmeldung zum Klick sieht nur der Klickende.

Einrichten: api.slack.com/apps → Create App → Socket Mode an → App-Level-Token
mit `connections:write` · OAuth-Scopes `chat:write`, `im:history` · Event
Subscriptions: `message.im` · Interactivity an · App installieren.
"""

import json
import threading
import time
import urllib.error
import urllib.request

from discord_bote import WebSocket, WebSocketFehler
from telegram_bote import TelegramBote

NACHRICHT_GRENZE = 3500


class SlackBote(TelegramBote):
    plattform = "Slack"
    nachricht_grenze = NACHRICHT_GRENZE

    def __init__(self, token, aktionen, api_basis="https://slack.com/api", chats=(), chats_speichern=None,
                 app_token=""):
        super().__init__(token, aktionen, api_basis=api_basis, chats=chats, chats_speichern=chats_speichern)
        self.app_token = app_token
        self._ws = None

    @staticmethod
    def kennung(chat_id):
        return str(chat_id)

    # ----------------------------------------------------------------- Web-API ---
    def api(self, methode, daten=None, frist=15, token=None):
        req = urllib.request.Request("%s/%s" % (self.api_basis, methode), data=json.dumps(daten or {}).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + (token or self.token),
                                              "Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=frist) as r:
                antwort = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError("Slack %s" % e.code)
        if not antwort.get("ok"):
            raise RuntimeError("Slack: %s" % (antwort.get("error") or "Fehler"))
        return antwort

    def senden(self, chat_id, text, knoepfe=None):
        text = str(text or "")
        if len(text) > self.nachricht_grenze:
            text = text[:self.nachricht_grenze] + "\n… (gekürzt, vollständig in Dive on Wide)"
        daten = {"channel": chat_id, "text": text or "(leer)", "unfurl_links": False}
        if knoepfe:
            daten["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900] or "(leer)"}},
                               {"type": "actions", "elements": [
                                   dict({"type": "button", "text": {"type": "plain_text", "text": t[:70]},
                                         "action_id": d[:250], "value": d[:2000]},
                                        style="primary" if d.startswith("ok:") else "danger")
                                   for t, d in knoepfe]}]
        return self.api("chat.postMessage", daten)

    def knopf_quittieren(self, anfrage, text):
        if not anfrage.get("response_url"):
            return
        req = urllib.request.Request(anfrage["response_url"], data=json.dumps(
            {"text": text, "response_type": "ephemeral", "replace_original": False}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except (urllib.error.URLError, OSError):
            pass

    # ------------------------------------------------------------- Socket Mode ---
    def ereignis(self, umschlag):
        art, nutzlast = umschlag.get("type"), umschlag.get("payload") or {}
        if art == "events_api":
            e = nutzlast.get("event") or {}
            if e.get("type") != "message" or e.get("channel_type") != "im" or e.get("bot_id") or e.get("subtype"):
                return
            return self.verarbeiten({"message": {"chat": {"id": e.get("channel")}, "text": e.get("text") or ""}})
        if art == "interactive" and nutzlast.get("type") == "block_actions":
            aktion = (nutzlast.get("actions") or [{}])[0]
            kanal = (nutzlast.get("channel") or (nutzlast.get("container") or {})).get("id") or \
                (nutzlast.get("container") or {}).get("channel_id")
            return self.verarbeiten({"callback_query": {"id": aktion.get("action_id"), "data": aktion.get("value", ""),
                                                        "response_url": nutzlast.get("response_url"),
                                                        "message": {"chat": {"id": kanal}}}})

    def _sicher(self, umschlag):
        try:
            self.ereignis(umschlag)
        except Exception as e:                       # ein Ereignis darf den Boten nicht umwerfen
            self.letzter_fehler = str(e)[:200]

    def einmal(self, frist=50):
        adresse = self.api("apps.connections.open", {}, token=self.app_token)["url"]
        ws = self._ws = WebSocket(adresse)
        try:
            ws.sock.settimeout(max(frist, 60) + 30)      # Slack schickt regelmäßig Pings
            while not self._stopp.is_set():
                roh = ws.empfangen()
                if roh is None:
                    raise WebSocketFehler("Slack hat die Verbindung geschlossen")
                n = json.loads(roh)
                if n.get("envelope_id"):
                    ws.senden(json.dumps({"envelope_id": n["envelope_id"]}))   # bestätigen, sonst kommt es erneut
                if n.get("type") == "hello":
                    self.letzter_fehler = ""
                elif n.get("type") == "disconnect":
                    raise WebSocketFehler("Slack verlangt eine neue Verbindung")
                else:
                    # Eigener Faden: Eine Modellantwort dauert, und solange muss die Verbindung
                    # weiter Pings beantworten, sonst trennt Slack sie.
                    threading.Thread(target=self._sicher, args=(n,), daemon=True).start()
        finally:
            self._ws = None
            ws.schliessen()
        return 0

    def schleife(self, frist=50):
        warten = 1
        while not self._stopp.is_set():
            beginn = time.time()
            try:
                self.einmal(frist)
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                self.letzter_fehler = str(e)[:200]
            if time.time() - beginn > 60:
                warten = 1
            self._stopp.wait(warten)
            warten = min(60, warten * 2)

    def stoppen(self):
        super().stoppen()
        ws = self._ws
        if ws:
            ws.schliessen()
