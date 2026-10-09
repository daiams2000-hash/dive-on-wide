# -*- coding: utf-8 -*-
"""Telegram-Anbindung — Dive on Wide vom Handy aus, wie das Gateway von Hermes oder
Remote Control von Claude Code.

Was geht:
  Nachricht               → Antwort des Standardmodells (Chat „Telegram“ in Dive on Wide)
  /werkbank <auftrag>     → Werkbank-Lauf im eingestellten Projekt; Freigaben
                            kommen als Knöpfe ✓/✕, das Ergebnis als Nachricht
  /status                 → was gerade läuft
  /abbrechen              → die von diesem Chat gestarteten Läufe abbrechen
  /koppeln <code>         → diesen Chat mit Dive on Wide verbinden

Sicherheit:
- Standardmäßig aus. Eingeschaltet wird mit einem eigenen Bot-Schlüssel.
- Nur gekoppelte Chats werden bedient. Gekoppelt wird mit einem Einmal-Code,
  den nur die Oberfläche von Dive on Wide zeigt (10 Minuten gültig, 5 Fehlversuche).
- Freigaben per Knopf gelten nur für Läufe, die dieser Chat gestartet hat.
- Alles läuft über die Server von Telegram — die Oberfläche sagt das.

Reine Standardbibliothek; die Verbindung zu Dive on Wide geschieht über `aktionen`,
damit sich das Modul ohne Server testen lässt.
"""

import json
import secrets
import threading
import time
import urllib.error
import urllib.request

KOPPEL_MINUTEN = 10
MAX_FEHLVERSUCHE = 5
NACHRICHT_GRENZE = 3900

HILFE = ("Dive on Wide ist verbunden.\n\n"
         "Nachricht — Antwort des Modells\n"
         "/werkbank <Auftrag> — Werkbank-Agent im eingestellten Projekt\n"
         "/status — was gerade läuft\n"
         "/abbrechen — deine laufenden Aufträge abbrechen")


class TelegramBote:
    plattform = "Telegram"
    nachricht_grenze = NACHRICHT_GRENZE
    def __init__(self, token, aktionen, api_basis="https://api.telegram.org", chats=(), chats_speichern=None):
        self.token = token
        self.aktionen = aktionen
        self.api_basis = api_basis.rstrip("/")
        self.chats = set(self.kennung(c) for c in chats)
        self.chats_speichern = chats_speichern or (lambda chats: None)
        self._code = None
        self._fehlversuche = 0
        self._stopp = threading.Event()
        self._offset = 0
        self.letzter_fehler = ""
        self.laeufe = {}                         # run_id -> chat_id

    @staticmethod
    def kennung(chat_id):
        """Chat-Kennungen sind bei Telegram Zahlen; andere Boten überschreiben das."""
        return int(chat_id)

    # ----------------------------------------------------------------- API ---
    def api(self, methode, daten=None, frist=15):
        req = urllib.request.Request("%s/bot%s/%s" % (self.api_basis, self.token, methode),
                                     data=json.dumps(daten or {}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=frist) as r:
            antwort = json.load(r)
        if not antwort.get("ok"):
            raise RuntimeError(antwort.get("description") or "Telegram-Fehler")
        return antwort.get("result")

    def senden(self, chat_id, text, knoepfe=None):
        text = str(text or "")
        if len(text) > self.nachricht_grenze:
            text = text[:self.nachricht_grenze] + "\n… (gekürzt, vollständig in Dive on Wide)"
        daten = {"chat_id": chat_id, "text": text or "(leer)", "disable_web_page_preview": True}
        if knoepfe:
            daten["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in knoepfe]]}
        return self.api("sendMessage", daten)

    # ----------------------------------------------------------- Kopplung ---
    def koppelcode(self):
        self._code = ("%06d" % secrets.randbelow(1_000_000), time.time() + KOPPEL_MINUTEN * 60)
        self._fehlversuche = 0
        return self._code[0]

    def _koppeln(self, chat_id, versuch):
        if not self._code or time.time() > self._code[1]:
            return "Kein gültiger Code. Hole in Dive on Wide unter Einstellungen → %s einen neuen." % self.plattform
        if not secrets.compare_digest(versuch.strip(), self._code[0]):
            self._fehlversuche += 1
            if self._fehlversuche >= MAX_FEHLVERSUCHE:
                self._code = None
                return "Zu viele Fehlversuche — der Code ist verfallen."
            return "Falscher Code."
        self._code = None
        self.chats.add(self.kennung(chat_id))
        self.chats_speichern(sorted(self.chats))
        return "✓ Gekoppelt.\n\n" + HILFE

    # ---------------------------------------------------------- Verarbeiten ---
    def verarbeiten(self, update):
        if "callback_query" in update:
            return self._knopf(update["callback_query"])
        nachricht = update.get("message") or {}
        chat_id = (nachricht.get("chat") or {}).get("id")
        text = (nachricht.get("text") or "").strip()
        if chat_id is None or not text:
            return
        befehl, _, rest = text.partition(" ")
        befehl = befehl.split("@")[0].lower()
        if chat_id not in self.chats:
            if befehl == "/koppeln":
                return self.senden(chat_id, self._koppeln(chat_id, rest))
            return self.senden(chat_id, "Dieser Chat ist nicht mit Dive on Wide gekoppelt. Hole in Dive on Wide unter "
                                        "Einstellungen → %s einen Code und sende: /koppeln CODE" % self.plattform)
        try:
            if befehl in ("/start", "/hilfe", "/help"):
                return self.senden(chat_id, HILFE)
            if befehl == "/werkbank":
                if not rest.strip():
                    return self.senden(chat_id, "Welcher Auftrag? Beispiel: /werkbank Behebe den Fehler in rechnen.py")
                run_id = self.aktionen["werkbank"](chat_id, rest.strip())
                self.laeufe[run_id] = chat_id
                return self.senden(chat_id, "▶ Werkbank-Agent arbeitet (Lauf %s). Freigaben und Ergebnis kommen hierher." % run_id)
            if befehl == "/status":
                return self.senden(chat_id, self.aktionen["status"]() or "Nichts läuft.")
            if befehl == "/abbrechen":
                eigene = [r for r, c in self.laeufe.items() if c == chat_id]
                n = sum(1 for r in eigene if self.aktionen["abbrechen"](r))
                return self.senden(chat_id, "⏹ %d Lauf/Läufe abgebrochen." % n if n else "Nichts von dir läuft.")
            if befehl.startswith("/"):
                return self.senden(chat_id, "Unbekannter Befehl.\n\n" + HILFE)
            return self.senden(chat_id, self.aktionen["chat"](chat_id, text))
        except Exception as e:                        # der Bote darf nie selbst ausfallen
            return self.senden(chat_id, "⚠️ %s" % e)

    def _knopf(self, anfrage):
        chat_id = ((anfrage.get("message") or {}).get("chat") or {}).get("id")
        art, _, run_id = str(anfrage.get("data") or "").partition(":")
        erlaubt = chat_id in self.chats and self.laeufe.get(run_id) == chat_id and art in ("ok", "nein")
        if erlaubt:
            erlaubt = self.aktionen["freigabe"](run_id, art == "ok")
        self.knopf_quittieren(anfrage, ("✓ freigegeben" if art == "ok" else "✕ abgelehnt") if erlaubt
                              else "Nicht (mehr) möglich.")

    def knopf_quittieren(self, anfrage, text):
        self.api("answerCallbackQuery", {"callback_query_id": anfrage.get("id"), "text": text})

    # ------------------------------------------------ Läufe beobachten ---
    def lauf_melden(self, run_id, wartet_auf=None, ergebnis=None):
        """Vom Server gerufen: eine Freigabe wartet, oder der Lauf ist fertig."""
        chat_id = self.laeufe.get(run_id)
        if chat_id is None:
            return
        if wartet_auf:
            self.senden(chat_id, "🔐 Freigabe nötig:\n%s" % wartet_auf, [("✓ Ausführen", "ok:" + run_id), ("✕ Ablehnen", "nein:" + run_id)])
        if ergebnis is not None:
            self.senden(chat_id, ergebnis)
            self.laeufe.pop(run_id, None)

    # ------------------------------------------------------------ Schleife ---
    def einmal(self, frist=50):
        updates = self.api("getUpdates", {"offset": self._offset, "timeout": frist,
                                          "allowed_updates": ["message", "callback_query"]}, frist=frist + 10)
        for u in updates or []:
            self._offset = max(self._offset, u.get("update_id", 0) + 1)
            self.verarbeiten(u)
        return len(updates or [])

    def schleife(self, frist=50):
        warten = 1
        while not self._stopp.is_set():
            try:
                self.einmal(frist)
                warten, self.letzter_fehler = 1, ""
            except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
                self.letzter_fehler = str(e)[:200]
                self._stopp.wait(warten)
                warten = min(60, warten * 2)

    def starten(self, frist=50):
        t = threading.Thread(target=self.schleife, args=(frist,), daemon=True)
        t.start()
        return t

    def stoppen(self):
        self._stopp.set()
