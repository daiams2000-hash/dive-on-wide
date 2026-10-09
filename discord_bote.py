# -*- coding: utf-8 -*-
"""Discord-Anbindung — dasselbe wie der Telegram-Bote, über einen eigenen Discord-Bot.

Befehle, Kopplung per Einmal-Code und Freigaben per Knopf sind dieselben wie bei
Telegram (telegram_bote.py); hier steckt nur, was an Discord anders ist:

- Nachrichten kommen über das Gateway (WebSocket), gesendet wird über REST.
- **Nur Direktnachrichten.** In einem Server-Kanal könnte jedes Mitglied einen
  gekoppelten Kanal mitbenutzen. Direktnachrichten brauchen außerdem keine
  privilegierte Berechtigung („Message Content Intent“).
- Knöpfe sind Discord-Komponenten; die Antwort auf einen Klick muss binnen drei
  Sekunden kommen und ist nur für den Klickenden sichtbar.

Eingerichtet wird ein Bot unter https://discord.com/developers/applications
(Bot → Token). Ihn einmal zu einem eigenen Server hinzufügen, damit man ihm
Direktnachrichten schreiben kann.

Reine Standardbibliothek, samt eines kleinen WebSocket-Clients (RFC 6455:
Textrahmen, Ping/Pong, Fragmentierung, Schließen).
"""

import base64
import json
import os
import random
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from telegram_bote import TelegramBote

INTENT_DIREKTNACHRICHTEN = 1 << 12
NACHRICHT_GRENZE = 1900                  # Discord: 2000 Zeichen je Nachricht


class WebSocketFehler(OSError):
    pass


AUSGANG = None      # vom Server gesetzt: vermerkt Verbindungen nach draußen im Ausgangsbuch


class WebSocket:
    """Gerade genug WebSocket für ein Gateway: verbinden, Text senden und empfangen."""

    def __init__(self, url, frist=30):
        teile = urllib.parse.urlsplit(url)
        sicher = teile.scheme == "wss"
        port = teile.port or (443 if sicher else 80)
        if AUSGANG:
            try:
                AUSGANG(url)          # ins Ausgangsbuch: Wer verbindet sich wohin (kein Inhalt)
            except Exception:
                pass
        roh = socket.create_connection((teile.hostname, port), timeout=frist)
        self.sock = ssl.create_default_context().wrap_socket(roh, server_hostname=teile.hostname) if sicher else roh
        schluessel = base64.b64encode(os.urandom(16)).decode()
        pfad = (teile.path or "/") + ("?" + teile.query if teile.query else "")
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nUser-Agent: DowOS\r\n\r\n"
                           % (pfad, teile.hostname, schluessel)).encode())
        kopf = b""
        while b"\r\n\r\n" not in kopf:
            stueck = self.sock.recv(1)
            if not stueck:
                raise WebSocketFehler("Verbindung beim Handschlag beendet")
            kopf += stueck
            if len(kopf) > 16384:
                raise WebSocketFehler("Handschlag zu lang")
        if b" 101 " not in kopf.split(b"\r\n", 1)[0]:
            raise WebSocketFehler("Kein WebSocket: %s" % kopf.split(b"\r\n", 1)[0].decode("latin-1"))
        self._schreiben = threading.Lock()

    def _genau(self, n):
        daten = b""
        while len(daten) < n:
            stueck = self.sock.recv(n - len(daten))
            if not stueck:
                raise WebSocketFehler("Verbindung beendet")
            daten += stueck
        return daten

    def _rahmen_senden(self, opcode, nutzlast):
        maske = os.urandom(4)
        laenge = len(nutzlast)
        kopf = bytes([0x80 | opcode])
        if laenge < 126:
            kopf += bytes([0x80 | laenge])
        elif laenge < 65536:
            kopf += bytes([0x80 | 126]) + struct.pack("!H", laenge)
        else:
            kopf += bytes([0x80 | 127]) + struct.pack("!Q", laenge)
        maskiert = bytes(b ^ maske[i % 4] for i, b in enumerate(nutzlast))
        with self._schreiben:
            self.sock.sendall(kopf + maske + maskiert)

    def senden(self, text):
        self._rahmen_senden(0x1, text.encode("utf-8"))

    def empfangen(self):
        """Nächste Textnachricht; None, wenn die Gegenseite schließt."""
        teile = []
        while True:
            b1, b2 = self._genau(2)
            opcode, laenge = b1 & 0x0F, b2 & 0x7F
            if laenge == 126:
                laenge = struct.unpack("!H", self._genau(2))[0]
            elif laenge == 127:
                laenge = struct.unpack("!Q", self._genau(8))[0]
            maske = self._genau(4) if b2 & 0x80 else None
            nutzlast = self._genau(laenge)
            if maske:
                nutzlast = bytes(b ^ maske[i % 4] for i, b in enumerate(nutzlast))
            if opcode == 0x8:
                # Schließcode merken: 4014 heißt „Intent im Developer Portal nicht freigeschaltet“
                self.schlusscode = struct.unpack("!H", nutzlast[:2])[0] if len(nutzlast) >= 2 else None
                return None
            if opcode == 0x9:
                self._rahmen_senden(0xA, nutzlast)
                continue
            if opcode == 0xA:
                continue
            teile.append(nutzlast)
            if b1 & 0x80:
                return b"".join(teile).decode("utf-8", "replace")

    def schliessen(self):
        try:
            self._rahmen_senden(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class DiscordBote(TelegramBote):
    plattform = "Discord"
    nachricht_grenze = NACHRICHT_GRENZE
    intents = INTENT_DIREKTNACHRICHTEN

    def __init__(self, token, aktionen, api_basis="https://discord.com/api/v10", chats=(), chats_speichern=None,
                 gateway=None):
        super().__init__(token, aktionen, api_basis=api_basis, chats=chats, chats_speichern=chats_speichern)
        self.gateway = gateway
        self._ws = None
        self.nutzer_id = None

    @staticmethod
    def kennung(chat_id):
        return str(chat_id)                      # Snowflakes: in JavaScript keine sicheren Zahlen

    # ----------------------------------------------------------------- REST ---
    def api(self, methode, daten=None, frist=15, verb="POST"):
        req = urllib.request.Request("%s/%s" % (self.api_basis, methode.lstrip("/")),
                                     data=json.dumps(daten).encode("utf-8") if daten is not None else None,
                                     method=verb,
                                     headers={"Authorization": "Bot " + self.token, "Content-Type": "application/json",
                                              "User-Agent": "DiscordBot (https://github.com/daiams2000-hash/dive-on-wide, 1)"})
        try:
            with urllib.request.urlopen(req, timeout=frist) as r:
                roh = r.read()
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                text = json.loads(text).get("message") or text
            except ValueError:
                pass
            raise RuntimeError("Discord %s: %s" % (e.code, text[:200]))
        return json.loads(roh) if roh else None

    def senden(self, chat_id, text, knoepfe=None):
        text = str(text or "")
        if len(text) > self.nachricht_grenze:
            text = text[:self.nachricht_grenze] + "\n… (gekürzt, vollständig in Dive on Wide)"
        daten = {"content": text or "(leer)", "allowed_mentions": {"parse": []}}
        if knoepfe:
            daten["components"] = [{"type": 1, "components": [
                {"type": 2, "style": 3 if d.startswith("ok:") else 4, "label": t[:80], "custom_id": d[:100]}
                for t, d in knoepfe]}]
        return self.api("channels/%s/messages" % chat_id, daten)

    def knopf_quittieren(self, anfrage, text):
        # Typ 4: Antwort mit Nachricht; Flag 64: nur für den Klickenden sichtbar.
        self.api("interactions/%s/%s/callback" % (anfrage["id"], anfrage["token"]),
                 {"type": 4, "data": {"content": text, "flags": 64}})

    # -------------------------------------------------------------- Gateway ---
    def ereignis(self, art, d):
        """Ein Gateway-Ereignis in das Format, das der gemeinsame Bote versteht."""
        if art == "READY":
            self.nutzer_id = ((d or {}).get("user") or {}).get("id")
            return
        if art == "MESSAGE_CREATE":
            autor = d.get("author") or {}
            if autor.get("bot") or d.get("guild_id") or autor.get("id") == self.nutzer_id:
                return                           # nur Direktnachrichten von Menschen
            return self.verarbeiten({"message": {"chat": {"id": self.kennung(d.get("channel_id"))},
                                                 "text": d.get("content") or ""}})
        if art == "INTERACTION_CREATE" and d.get("type") == 3:
            if d.get("guild_id"):
                return
            return self.verarbeiten({"callback_query": {
                "id": d.get("id"), "token": d.get("token"), "data": (d.get("data") or {}).get("custom_id", ""),
                "message": {"chat": {"id": self.kennung(d.get("channel_id"))}}}})

    def _gateway_adresse(self):
        if self.gateway:
            return self.gateway
        return (self.api("gateway/bot", None, verb="GET") or {}).get("url", "wss://gateway.discord.gg") \
            + "/?v=10&encoding=json"

    def einmal(self, frist=50):
        """Eine Gateway-Sitzung: verbinden, anmelden, Ereignisse verarbeiten, bis sie endet."""
        ws = self._ws = WebSocket(self._gateway_adresse())
        folge = {"s": None}
        try:
            hallo = json.loads(ws.empfangen() or "{}")
            if hallo.get("op") != 10:
                raise WebSocketFehler("Gateway grüßte nicht")
            takt = hallo["d"]["heartbeat_interval"] / 1000.0

            def herzschlag():
                self._stopp.wait(takt * random.random())
                while not self._stopp.is_set() and self._ws is ws:
                    try:
                        ws.senden(json.dumps({"op": 1, "d": folge["s"]}))
                    except OSError:
                        return
                    self._stopp.wait(takt)
            threading.Thread(target=herzschlag, daemon=True).start()
            ws.senden(json.dumps({"op": 2, "d": {"token": self.token, "intents": self.intents,
                                                 "properties": {"os": "dowos", "browser": "dowos", "device": "dowos"}}}))
            ws.sock.settimeout(takt * 2 + 5)      # ohne Herzschlag-Antwort ist die Verbindung tot
            while not self._stopp.is_set():
                roh = ws.empfangen()
                if roh is None:
                    self.geschlossen(getattr(ws, "schlusscode", None))
                    raise WebSocketFehler("Gateway hat geschlossen")
                n = json.loads(roh)
                if n.get("s") is not None:
                    folge["s"] = n["s"]
                if n.get("op") == 0:
                    try:
                        self.ereignis(n.get("t"), n.get("d") or {})
                    except Exception as e:           # ein Ereignis darf die Verbindung nicht umwerfen
                        self.letzter_fehler = str(e)[:200]
                elif n.get("op") == 1:
                    ws.senden(json.dumps({"op": 1, "d": folge["s"]}))
                elif n.get("op") in (7, 9):          # neu verbinden bzw. Sitzung ungültig
                    raise WebSocketFehler("Gateway verlangt neue Verbindung")
                if n.get("op") == 0 and n.get("t") == "READY":
                    self.letzter_fehler = ""
        finally:
            self._ws = None
            ws.schliessen()
        return 0

    def geschlossen(self, code):
        """Unterklassen reagieren auf Schließcodes (z. B. fehlender Intent)."""

    def schleife(self, frist=50):
        warten = 1
        while not self._stopp.is_set():
            beginn = time.time()
            try:
                self.einmal(frist)
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                self.letzter_fehler = str(e)[:200]
            if time.time() - beginn > 60:
                warten = 1                           # lief eine Weile: kein Grund zu zögern
            self._stopp.wait(warten)
            warten = min(60, warten * 2)

    def stoppen(self):
        super().stoppen()
        ws = self._ws
        if ws:
            ws.schliessen()
