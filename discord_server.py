# -*- coding: utf-8 -*-
"""Discord-Server-Chat: die lokalen Modelle von Dive on Wide als Chat auf dem eigenen Discord-Server.

Anders als der Bote (discord_bote.py, persönliche Fernbedienung per Direktnachricht) antwortet
dieser Bot in freigegebenen Kanälen eines Servers — für alle, die ihn ansprechen.

Zwei Betriebsarten, im Einrichten ausdrücklich gewählt:
- **öffentlich (empfohlen für Server mit Fremden):** nur Chat. Keine Werkbank, kein Code, kein
  Dateizugriff. Grenzen je Mitglied, Warteschlange, Hinweis „KI-Antwort“.
- **privat (voller Zugriff):** zusätzlich die Befehle der Fernbedienung (/werkbank, /status,
  /abbrechen) mit Freigabe per Knopf — aber nur für ausdrücklich eingetragene Discord-Nutzer.
  Wer sonst im Kanal ist, bekommt weiter nur den Chat.

Angesprochen wird er mit @Erwähnung oder /frage; jedes Gespräch bekommt einen eigenen Thread.
Ohne den privilegierten „Message Content Intent“ liest der Bot nur, was an ihn gerichtet ist;
mit ihm (Schalter im Developer Portal) antwortet er in seinen Threads auf jede Nachricht.
"""
import json
import queue
import re
import threading
import time

from discord_bote import DiscordBote

INTENT_SERVER = 1 << 0
INTENT_NACHRICHTEN = 1 << 9
INTENT_INHALT = 1 << 15
TEIL = 1900
SYSTEM = ("Du bist ein hilfreicher Assistent in einem Discord-Server. Antworte in der Sprache der Frage, knapp und "
          "freundlich, mit Discord-Markdown. Du hast keinen Zugriff auf Dateien, das Netz oder Werkzeuge. Erfinde "
          "nichts: Wenn du etwas nicht weißt, sag es.\n"
          "Du bist das Sprachmodell „%s“, lokal ausgeführt über Dive on Wide. Frühere Antworten in diesem Chat können von "
          "anderen Modellen stammen — übernimm deren Namen nicht.")
MODELL_HINWEIS = "🧠 "                           # Bot-Meldung „Ab jetzt antwortet …“ — gehört nicht in den Verlauf
STANDARD = {"kanaele": [], "modus": "oeffentlich", "modelle": [], "standard_modell": "", "limit_stunde": 10,
            "rollen": [], "voll_nutzer": [], "fusszeile": True, "inhalt_lesen": True, "verlauf": 12,
            "privat_threads": True, "original_entfernen": True}
FRAGE = "🗨 "                                     # vom Bot in den Thread übernommene Frage eines Mitglieds
KNOPF_NEU, KNOPF_ENDE, AUSWAHL_MODELL = "dcs:neu", "dcs:ende", "dcs:modell"


def konfig_pruefen(k):
    """Einstellungen säubern — was nicht passt, fällt auf die sichere Vorgabe zurück."""
    k = dict(STANDARD, **{a: b for a, b in (k or {}).items() if a in STANDARD})
    k["modus"] = k["modus"] if k["modus"] in ("oeffentlich", "privat") else "oeffentlich"
    for feld in ("kanaele", "rollen", "voll_nutzer"):
        k[feld] = [str(x) for x in k[feld] if re.fullmatch(r"\d{5,25}", str(x))]
    k["modelle"] = [str(x) for x in k["modelle"] if str(x).strip()][:25]
    for feld in ("fusszeile", "inhalt_lesen", "privat_threads", "original_entfernen"):
        k[feld] = bool(k[feld])
    try:
        k["limit_stunde"] = max(0, min(int(k["limit_stunde"]), 1000))
        k["verlauf"] = max(2, min(int(k["verlauf"]), 40))
    except (TypeError, ValueError):
        k["limit_stunde"], k["verlauf"] = STANDARD["limit_stunde"], STANDARD["verlauf"]
    return k


def zerlegen(text, grenze=TEIL):
    """Discord nimmt 2000 Zeichen je Nachricht: an Absätzen, sonst an Zeilen, sonst hart teilen."""
    teile, rest = [], text.strip()
    while len(rest) > grenze:
        schnitt = rest.rfind("\n\n", 0, grenze)
        if schnitt < grenze // 2:
            schnitt = rest.rfind("\n", 0, grenze)
        if schnitt < grenze // 2:
            schnitt = grenze
        teile.append(rest[:schnitt].rstrip())
        rest = rest[schnitt:].lstrip("\n")
    return teile + ([rest] if rest else [])


def ohne_denken(text):
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.S).strip()


def kurzname(modell):
    return str(modell).split("@@")[-1]


class ServerChat(DiscordBote):
    plattform = "Discord-Server"

    def __init__(self, token, aktionen, konfig, api_basis="https://discord.com/api/v10", gateway=None, sofort=False,
                 wahl_datei=None):
        super().__init__(token, aktionen, api_basis=api_basis, gateway=gateway)
        self.k = konfig_pruefen(konfig)
        self.intents = INTENT_SERVER | INTENT_NACHRICHTEN | (INTENT_INHALT if self.k["inhalt_lesen"] else 0)
        self.app_id = None
        self.threads = {}                        # Thread-ID -> Elternkanal (nur eigene Threads)
        # Kanal/Thread -> Modell; überdauert Neustarts, sonst antwortet nach jedem Neustart wieder das Standardmodell
        self.wahl_datei = wahl_datei
        self.modellwahl = {}
        if wahl_datei:
            try:
                self.modellwahl = {str(a): str(b) for a, b in json.load(open(wahl_datei, encoding="utf-8")).items()}
            except (OSError, ValueError, AttributeError):
                self.modellwahl = {}
        self.nutzung = {}                        # Nutzer -> [Zeitpunkte]
        self.warteschlange = queue.Queue()
        self.sofort = sofort                     # Tests: ohne Arbeiter-Thread
        self.beantwortet = 0
        self.hinweis = ""
        self._fremd = set()                      # Kanäle, die nachweislich nicht zu uns gehören
        self._arbeiter = None

    # ----------------------------------------------------------- Zugriff ---
    def darf_chatten(self, nutzer_id, rollen):
        if self.k["rollen"] and not set(rollen or ()) & set(self.k["rollen"]) and nutzer_id not in self.k["voll_nutzer"]:
            return "Dieser Bot antwortet hier nur bestimmten Rollen."
        if self.k["limit_stunde"] and nutzer_id not in self.k["voll_nutzer"]:
            jetzt = time.time()
            liste = [t for t in self.nutzung.get(nutzer_id, []) if jetzt - t < 3600]
            if len(liste) >= self.k["limit_stunde"]:
                return "Limit erreicht: %d Fragen pro Stunde. Bitte später wieder." % self.k["limit_stunde"]
            self.nutzung[nutzer_id] = liste + [jetzt]
        return None

    def voll(self, nutzer_id):
        return self.k["modus"] == "privat" and nutzer_id in self.k["voll_nutzer"]

    def erlaubter_ort(self, kanal_id):
        if kanal_id in self.k["kanaele"] or self.threads.get(kanal_id) in self.k["kanaele"]:
            return True
        if kanal_id in self._fremd or not self.k["kanaele"]:
            return False
        # Nach einem Neustart kennt der Bot seine Threads nicht mehr: einmal bei Discord nachfragen
        try:
            k = self.api("channels/%s" % kanal_id, None, verb="GET") or {}
        except RuntimeError:
            k = {}
        if k.get("type") in (11, 12) and str(k.get("owner_id")) == str(self.nutzer_id) \
                and str(k.get("parent_id")) in self.k["kanaele"]:
            self.threads[kanal_id] = str(k["parent_id"])
            return True
        self._fremd.add(kanal_id)
        return False

    def modell_fuer(self, ort):
        wahl = self.modellwahl.get(ort) or self.modellwahl.get(self.threads.get(ort, ""))
        erlaubt = self.k["modelle"]
        if wahl and (not erlaubt or wahl in erlaubt):
            return wahl
        return self.k["standard_modell"] or (erlaubt[0] if erlaubt else "")

    # ------------------------------------------------------- Ereignisse ---
    def geschlossen(self, code):
        if code == 4014 and self.intents & INTENT_INHALT:
            self.intents &= ~INTENT_INHALT
            self.k["inhalt_lesen"] = False
            self.hinweis = ("Der „Message Content Intent“ ist im Developer Portal nicht eingeschaltet — der Bot "
                            "antwortet deshalb nur auf @Erwähnung und /frage. Er versucht es alle 10 Minuten erneut.")
            # Schaltet der Nutzer den Intent später ein, soll das ohne Neustart von Dive on Wide wirken
            t = threading.Timer(600, self.intent_erneut)
            t.daemon = True
            t.start()

    def intent_erneut(self):
        if self._stopp.is_set() or self.intents & INTENT_INHALT:
            return
        self.intents |= INTENT_INHALT
        self.k["inhalt_lesen"] = True
        self.hinweis = ""
        ws = self._ws
        if ws:
            ws.schliessen()                      # neu verbinden; fehlt der Intent weiter, fällt er wieder zurück

    def ereignis(self, art, d):
        if art == "READY":
            self.nutzer_id = ((d or {}).get("user") or {}).get("id")
            self.app_id = ((d or {}).get("application") or {}).get("id") or self.nutzer_id
            return
        if art == "GUILD_CREATE":
            self.befehle_anmelden(d.get("id"))
            for c in d.get("channels") or []:
                if str(c.get("id")) in self.k["kanaele"]:
                    self.panel_sicherstellen(str(c["id"]))
            return
        if art == "MESSAGE_CREATE":
            return self.nachricht(d)
        if art == "INTERACTION_CREATE":
            return self.interaktion(d)

    def befehle_anmelden(self, guild_id):
        befehle = [
            {"name": "frage", "description": "Frag das lokale Modell", "type": 1,
             "options": [{"type": 3, "name": "text", "description": "Deine Frage", "required": True}]},
            {"name": "modell", "description": "Modell für diesen Kanal oder Thread wählen", "type": 1,
             "options": [{"type": 3, "name": "name", "description": "Modell", "required": True, "autocomplete": True}]},
        ]
        if self.k["modus"] == "privat":
            befehle += [
                {"name": "werkbank", "description": "Werkbank-Auftrag an Dive on Wide (nur eingetragene Nutzer)", "type": 1,
                 "options": [{"type": 3, "name": "auftrag", "description": "Was soll getan werden?", "required": True}]},
                {"name": "status", "description": "Was läuft gerade in Dive on Wide?", "type": 1},
                {"name": "abbrechen", "description": "Eigene laufende Aufträge abbrechen", "type": 1}]
        if self.app_id and guild_id:
            self.api("applications/%s/guilds/%s/commands" % (self.app_id, guild_id), befehle, verb="PUT")

    def nachricht(self, d):
        autor = d.get("author") or {}
        if autor.get("bot") or not d.get("guild_id") or autor.get("id") == self.nutzer_id:
            return
        kanal = str(d.get("channel_id"))
        if not self.erlaubter_ort(kanal):
            return
        erwaehnt = any(str(m.get("id")) == str(self.nutzer_id) for m in d.get("mentions") or [])
        im_eigenen_thread = kanal in self.threads
        # Mit Leserecht genügt Schreiben (im Kanal öffnet es einen Chat, im Thread geht es weiter);
        # ohne nur die @Erwähnung
        if not erwaehnt and not self.k["inhalt_lesen"]:
            return
        text = re.sub(r"<@!?%s>" % re.escape(str(self.nutzer_id)), "", d.get("content") or "").strip()
        if not text:
            return
        grund = self.darf_chatten(str(autor.get("id")), (d.get("member") or {}).get("roles"))
        if grund:
            return self.api("channels/%s/messages" % kanal, {"content": "⏳ " + grund,
                                                              "message_reference": {"message_id": d.get("id")},
                                                              "allowed_mentions": {"parse": []}})
        name = autor.get("global_name") or autor.get("username") or "?"
        if not im_eigenen_thread:
            ort = self.thread_oeffnen(kanal, str(autor.get("id")), name, text, d.get("id"))
        else:
            ort = kanal
        self.einreihen({"ort": ort, "frage": text, "name": name})

    def thread_oeffnen(self, kanal, nutzer_id, name, text="", nachricht_id=None):
        """Ein neues Gespräch: privater Thread (nur Fragende/r und Bot) oder öffentlicher am Beitrag."""
        titel = ("💬 " + (text[:60] if text else name)).strip()
        if self.k["privat_threads"]:
            thread = self.api("channels/%s/threads" % kanal, {"name": titel, "type": 12, "invitable": False,
                                                              "auto_archive_duration": 1440})
            ort = str(thread["id"])
            self.api("channels/%s/thread-members/%s" % (ort, nutzer_id), None, verb="PUT")
            if text:
                # Die Frage wandert in den privaten Thread; im Kanal soll sie niemand sonst lesen
                self.api("channels/%s/messages" % ort, {"content": "%s**%s:** %s" % (FRAGE, name, text[:1800]),
                                                        "allowed_mentions": {"parse": []}})
                if nachricht_id and self.k["original_entfernen"]:
                    try:
                        self.api("channels/%s/messages/%s" % (kanal, nachricht_id), None, verb="DELETE")
                    except RuntimeError:
                        self.hinweis = ("Die Frage im Kanal ließ sich nicht entfernen — dem Bot fehlt das Recht "
                                        "„Nachrichten verwalten“.")
        else:
            thread = self.api("channels/%s/messages/%s/threads" % (kanal, nachricht_id),
                              {"name": titel, "auto_archive_duration": 1440})
            ort = str(thread["id"])
        self.threads[ort] = kanal
        if kanal in self.modellwahl:
            self.wahl_merken(ort, self.modellwahl[kanal])
        self.api("channels/%s/messages" % ort, self.bedienung(ort, "<@%s> " % nutzer_id + (
            "Dein privater Chat — nur du und der Bot sehen ihn. Schreib einfach hier weiter."
            if self.k["privat_threads"] else "Schreib einfach hier im Thread weiter.")
            + "\n🧠 Es antwortet: **%s**" % kurzname(self.modell_fuer(ort))))
        return ort

    def wahl_merken(self, ort, modell):
        self.modellwahl[ort] = modell
        if self.wahl_datei:
            try:
                with open(self.wahl_datei, "w", encoding="utf-8") as f:
                    json.dump(dict(list(self.modellwahl.items())[-2000:]), f)
            except OSError:
                pass

    def bedienung(self, ort, text):
        """Modellauswahl und Knöpfe — statt Schrägstrich-Befehlen."""
        auswahl = self.k["modelle"] or list(self.aktionen["modelle"]())[:25]
        aktuell = self.modell_fuer(ort)
        zeilen = [{"type": 1, "components": [{"type": 2, "style": 2, "label": "Neuer Chat", "emoji": {"name": "💬"},
                                              "custom_id": KNOPF_NEU},
                                             {"type": 2, "style": 2, "label": "Chat beenden", "emoji": {"name": "🔒"},
                                              "custom_id": KNOPF_ENDE}]}]
        if len(auswahl) > 1:
            zeilen.insert(0, {"type": 1, "components": [{"type": 3, "custom_id": AUSWAHL_MODELL, "placeholder": "Modell wählen",
                                                         "options": [{"label": kurzname(m)[:100], "value": m[:100],
                                                                      "default": m == aktuell} for m in auswahl[:25]]}]})
        return {"content": text, "components": zeilen, "allowed_mentions": {"parse": ["users"]}}

    def panel_sicherstellen(self, kanal):
        """Ein Knopf „Neuer Chat“ im Kanal — einmal, nicht bei jedem Start erneut."""
        try:
            alt = self.api("channels/%s/messages?limit=30" % kanal, None, verb="GET") or []
        except RuntimeError:
            return
        if any(str((m.get("author") or {}).get("id")) == str(self.nutzer_id) and KNOPF_NEU in json.dumps(m.get("components") or [])
               for m in alt):
            return
        self.api("channels/%s/messages" % kanal, {
            "content": "**Chat mit lokalen KI-Modellen** — schreib einfach hier oder klick auf den Knopf. Jede Frage "
                       "öffnet einen privaten Chat, den nur du und der Bot sehen.\n-# Gerechnet wird auf dem Rechner "
                       "eines Mitglieds. Experimentell: Antworten sind KI-generiert und ungeprüft.",
            "components": [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Neuer Chat",
                                                       "emoji": {"name": "💬"}, "custom_id": KNOPF_NEU}]}],
            "allowed_mentions": {"parse": []}})

    # ------------------------------------------------------- Befehle ---
    def interaktion(self, d):
        typ, daten = d.get("type"), d.get("data") or {}
        nutzer = str(((d.get("member") or {}).get("user") or d.get("user") or {}).get("id"))
        kanal = str(d.get("channel_id"))
        if typ == 4:                             # Autovervollständigung für /modell
            getippt = str(next((o.get("value") for o in daten.get("options", []) if o.get("focused")), "")).lower()
            auswahl = self.k["modelle"] or list(self.aktionen["modelle"]())
            return self.api("interactions/%s/%s/callback" % (d["id"], d["token"]), {"type": 8, "data": {"choices": [
                {"name": kurzname(m)[:100], "value": m[:100]} for m in auswahl if getippt in m.lower()][:25]}})
        cid = str(daten.get("custom_id", ""))
        if typ == 3 and cid == KNOPF_NEU:
            heimat = self.threads.get(kanal, kanal)
            if not self.erlaubter_ort(heimat):
                return self.antwort(d, "Hier ist der Bot nicht freigegeben.", privat=True)
            grund = self.darf_chatten(nutzer, (d.get("member") or {}).get("roles"))
            if grund:
                return self.antwort(d, "⏳ " + grund, privat=True)
            name = (((d.get("member") or {}).get("user") or {}).get("global_name")
                    or ((d.get("member") or {}).get("user") or {}).get("username") or "Chat")
            ort = self.thread_oeffnen(heimat, nutzer, name)
            return self.antwort(d, "💬 Dein Chat: <#%s>" % ort, privat=True)
        if typ == 3 and cid == KNOPF_ENDE:
            if kanal not in self.threads:
                return self.antwort(d, "Das geht nur in einem Chat-Thread.", privat=True)
            self.antwort(d, "🔒 Chat beendet. Einen neuen startest du im Kanal.")
            self.api("channels/%s" % kanal, {"archived": True, "locked": True}, verb="PATCH")
            return
        if typ == 3 and cid == AUSWAHL_MODELL:
            wahl = str((daten.get("values") or [""])[0])
            erlaubt = self.k["modelle"] or list(self.aktionen["modelle"]())
            if wahl not in erlaubt:
                return self.antwort(d, "Dieses Modell ist hier nicht freigegeben.", privat=True)
            self.wahl_merken(kanal, wahl)
            # Die Nachricht mit der Auswahl selbst aktualisieren: Sie zeigt immer das Modell, das wirklich antwortet
            alt = ((d.get("message") or {}).get("content") or "").split("\n🧠")[0]
            neu = self.bedienung(kanal, "%s\n🧠 Es antwortet: **%s**" % (alt, kurzname(wahl)))
            neu.pop("allowed_mentions", None)
            return self.api("interactions/%s/%s/callback" % (d["id"], d["token"]), {"type": 7, "data": neu})
        if typ == 3:                             # Freigabe-Knopf eines Werkbank-Laufs
            art, _, run_id = cid.partition(":")
            erlaubt = self.voll(nutzer) and run_id in self.laeufe and art in ("ok", "nein") \
                and self.aktionen["freigabe"](run_id, art == "ok")
            return self.knopf_quittieren(d, ("✓ freigegeben" if art == "ok" else "✕ abgelehnt") if erlaubt
                                         else "Nicht (mehr) möglich — oder du darfst hier nichts freigeben.")
        if typ != 2:
            return
        name = daten.get("name")
        opt = {o.get("name"): o.get("value") for o in daten.get("options", [])}
        if not self.erlaubter_ort(kanal) and not (name in ("werkbank", "status", "abbrechen") and self.voll(nutzer)):
            return self.antwort(d, "In diesem Kanal ist der Bot nicht freigegeben.", privat=True)
        if name == "modell":
            wahl = str(opt.get("name") or "")
            if self.k["modelle"] and wahl not in self.k["modelle"]:
                return self.antwort(d, "Dieses Modell ist hier nicht freigegeben.", privat=True)
            if not self.k["modelle"] and wahl not in self.aktionen["modelle"]():
                return self.antwort(d, "Dieses Modell gibt es in Dive on Wide nicht.", privat=True)
            self.wahl_merken(kanal, wahl)
            return self.antwort(d, "🧠 Modell hier: **%s**" % kurzname(wahl))
        if name == "frage":
            grund = self.darf_chatten(nutzer, (d.get("member") or {}).get("roles"))
            if grund:
                return self.antwort(d, "⏳ " + grund, privat=True)
            text = str(opt.get("text") or "").strip()
            self.antwort(d, "**%s:** %s" % ((((d.get("member") or {}).get("user") or {}).get("global_name") or "Frage"),
                                             text[:1800]))
            if kanal in self.threads:
                ort = kanal
            else:
                original = self.api("webhooks/%s/%s/messages/@original" % (self.app_id, d["token"]), None, verb="GET")
                thread = self.api("channels/%s/messages/%s/threads" % (kanal, original["id"]),
                                  {"name": text[:80] or "Frage", "auto_archive_duration": 1440})
                ort = str(thread["id"])
                self.threads[ort] = kanal
                if kanal in self.modellwahl:
                    self.modellwahl[ort] = self.modellwahl[kanal]
            return self.einreihen({"ort": ort, "frage": text, "name": "Frage"})
        if name in ("werkbank", "status", "abbrechen"):
            if not self.voll(nutzer):
                return self.antwort(d, "🔒 Nur für eingetragene Nutzer im privaten Modus.", privat=True)
            try:
                if name == "werkbank":
                    run_id = self.aktionen["werkbank"](kanal, str(opt.get("auftrag") or "").strip())
                    self.laeufe[run_id] = kanal
                    return self.antwort(d, "▶ Werkbank-Agent arbeitet (Lauf %s). Freigaben und Ergebnis kommen hierher." % run_id)
                if name == "status":
                    return self.antwort(d, self.aktionen["status"]() or "Nichts läuft.", privat=True)
                eigene = [r for r, c in self.laeufe.items() if c == kanal]
                n = sum(1 for r in eigene if self.aktionen["abbrechen"](r))
                return self.antwort(d, "⏹ %d Lauf/Läufe abgebrochen." % n if n else "Nichts von dir läuft.", privat=True)
            except Exception as e:
                return self.antwort(d, "⚠️ %s" % str(e)[:300], privat=True)

    def antwort(self, d, text, privat=False):
        return self.api("interactions/%s/%s/callback" % (d["id"], d["token"]),
                        {"type": 4, "data": dict({"content": text[:1990], "allowed_mentions": {"parse": []}},
                                                 **({"flags": 64} if privat else {}))})

    # ------------------------------------------------------- Antworten ---
    def einreihen(self, auftrag):
        wartend = self.warteschlange.qsize() + (1 if getattr(self, "_beschaeftigt", False) else 0)
        if wartend and not self.sofort:
            self.api("channels/%s/messages" % auftrag["ort"],
                     {"content": "⏳ In der Warteschlange — Platz %d. Das Modell rechnet eine Antwort nach der anderen." % wartend,
                      "allowed_mentions": {"parse": []}})
        if self.sofort:
            return self.beantworten(auftrag)
        self.warteschlange.put(auftrag)
        if not self._arbeiter or not self._arbeiter.is_alive():
            self._arbeiter = threading.Thread(target=self._arbeiten, daemon=True, name="discord-server-chat")
            self._arbeiter.start()

    def _arbeiten(self):
        while not self._stopp.is_set():
            try:
                auftrag = self.warteschlange.get(timeout=5)
            except queue.Empty:
                continue
            self._beschaeftigt = True
            try:
                self.beantworten(auftrag)
            except Exception as e:
                self.letzter_fehler = str(e)[:200]
            finally:
                self._beschaeftigt = False

    def verlauf(self, ort, frage):
        """Der Faden des Threads als Chatverlauf: eigene Nachrichten = Assistent, alle anderen = Nutzer."""
        try:
            alt = self.api("channels/%s/messages?limit=%d" % (ort, self.k["verlauf"] + 2), None, verb="GET") or []
        except RuntimeError:
            alt = []
        nachrichten = []
        for m in reversed(alt):
            text = (m.get("content") or "").strip()
            if not text or text.startswith("⏳"):
                continue
            if str((m.get("author") or {}).get("id")) == str(self.nutzer_id):
                if m.get("components"):
                    continue                     # Begrüßung mit Auswahl und Knöpfen
                if text.startswith(MODELL_HINWEIS):
                    continue
                if text.startswith(FRAGE):
                    nachrichten.append({"role": "user", "content": re.sub(r"^🗨 \*\*[^*]*:\*\* ", "", text)})
                    continue
                nachrichten.append({"role": "assistant", "content": re.sub(r"\n-# 🤖.*$", "", text, flags=re.S)})
            else:
                nachrichten.append({"role": "user", "content": re.sub(r"<@!?\d+>", "", text).strip()})
        if not nachrichten or nachrichten[-1]["content"] != frage:
            nachrichten.append({"role": "user", "content": frage})
        return [{"role": "system", "content": SYSTEM % kurzname(self.modell_fuer(ort))}] + nachrichten[-self.k["verlauf"]:]

    def beantworten(self, auftrag):
        ort, modell = auftrag["ort"], self.modell_fuer(auftrag["ort"])
        fertig = threading.Event()

        def tippen():                            # „schreibt …“ läuft nach 10 s ab
            while not fertig.is_set():
                try:
                    self.api("channels/%s/typing" % ort, {})
                except RuntimeError:
                    pass
                fertig.wait(8)
        if not self.sofort:
            threading.Thread(target=tippen, daemon=True).start()
        try:
            text = ohne_denken(self.aktionen["antworten"](modell, self.verlauf(ort, auftrag["frage"])))
        except Exception as e:
            text = "⚠️ Das Modell hat nicht geantwortet: %s" % str(e)[:300]
        finally:
            fertig.set()
        teile = zerlegen(text or "(keine Antwort)")
        if self.k["fusszeile"]:
            fuss = "\n-# 🤖 %s · KI-Antwort, lokal berechnet, ungeprüft" % kurzname(modell)
            if len(teile[-1]) + len(fuss) > 1990:
                teile.append(fuss.strip())
            else:
                teile[-1] += fuss
        for t in teile:
            self.api("channels/%s/messages" % ort, {"content": t, "allowed_mentions": {"parse": []}})
        self.beantwortet += 1
        return teile
