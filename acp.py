# -*- coding: utf-8 -*-
"""ACP — der Werkbank-Agent in Zed, JetBrains, Neovim und jedem anderen Editor,
der das Agent Client Protocol spricht (https://agentclientprotocol.com).

So sind auch Claude Code und Codex in diese Editoren eingebunden: Der Editor
startet `dowos acp` als Kindprozess und spricht JSON-RPC 2.0 über stdin/stdout,
eine Nachricht je Zeile. Beispiel für Zed (settings.json):

    "agent_servers": {
      "Dive on Wide": {"command": "/Users/ich/DowOS/app/dowos", "args": ["acp"]}
    }

Was ankommt:
  initialize · authenticate · session/new · session/set_mode · session/prompt ·
  session/cancel
Was Dive on Wide schickt:
  session/update (tool_call, tool_call_update, agent_thought_chunk,
  agent_message_chunk) · session/request_permission (jede Freigabe)

Die Werkbank bleibt dieselbe wie in der Oberfläche und im Terminal: Rechte,
Sandbox, Regeln, Checkpunkte, Skills, Gedächtnis, eigene /Befehle. Freigaben
fragt der Editor. Folgenachrichten in derselben Sitzung setzen das Gespräch
fort. Läufe erscheinen in Dive on Wide.

stdout gehört allein dem Protokoll — alles andere geht nach stderr.
"""

import json
import os
import re
import sys
import threading
import time
import uuid

import dowos_cli as cli
import mcp as mcp_client
from dowos_cli import (agentenprofile, agentskills, checkpunkte, externe_agenten, schrittprotokoll, werkbank,
                       werkbank_befehle, werkbank_gedaechtnis, werkbank_regeln)

PROTOKOLL_VERSION = 1

MODI = [
    {"id": "projekt", "name": "Im Projekt schreiben", "description": werkbank.STUFEN["projekt"]},
    {"id": "plan", "name": "Erst planen", "description": "Nur lesen und einen Plan vorlegen; ändern erst nach Freigabe."},
    {"id": "lesen", "name": "Nur lesen", "description": werkbank.STUFEN["lesen"]},
    {"id": "voll", "name": "Voller Zugriff", "description": werkbank.STUFEN["voll"]},
]

# Erstes Wort einer Schrittmeldung → ACP-„kind“; der Editor wählt danach Symbol und
# Darstellung. Die Meldungen nennen mal das Werkzeug („lesen …“), mal die
# Beschreibung aus werkbank._beschreibung („Ändern: …“, „Befehl ausführen: …“).
ARTEN = {"liste": "read", "lesen": "read", "suchen": "search", "erinnern": "search", "ersetzen": "edit",
         "schreiben": "edit", "ausfuehren": "execute", "websuche": "fetch", "webseite": "fetch",
         "Websuche": "fetch", "Seite": "fetch", "Unteragent": "think", "Skill": "read", "gemerkt": "other",
         "Ändern": "edit", "Schreiben": "edit", "Befehl": "execute", "MCP": "other", "mcp": "other",
         "externer": "think", "Externer": "think"}
SCHRITT_RE = re.compile(r"^Schritt \d+: (\w+)(.*)$", re.S)


class Abgebrochen(Exception):
    pass


class RpcFehler(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


class Sitzung:
    def __init__(self, ordner):
        self.id = "sitzung_" + uuid.uuid4().hex[:12]
        self.ordner = ordner
        self.modus = "projekt"
        self.vorher = None
        self.letzter_lauf = ""
        self.mcp = {"mcpServers": {}}
        self.abbrechen = threading.Event()
        self.laeuft = False


class Agent:
    def __init__(self, ein=None, aus=None, modell=None, freigabe=None, max_schritte=None):
        self.ein = ein or sys.stdin
        self.aus = aus or sys.stdout
        self.modell = modell
        self.politik = freigabe or werkbank.STANDARD_FREIGABE
        self.max_schritte = max_schritte or werkbank.MAX_SCHRITTE
        self.sitzungen = {}
        self._schreiben = threading.Lock()
        self._wartend = {}
        self._naechste = 0
        self._ende = threading.Event()

    # ------------------------------------------------------------ JSON-RPC ---
    def senden(self, nachricht):
        with self._schreiben:
            self.aus.write(json.dumps(dict(nachricht, jsonrpc="2.0"), ensure_ascii=False) + "\n")
            self.aus.flush()

    def melden(self, sitzung, update):
        self.senden({"method": "session/update", "params": {"sessionId": sitzung.id, "update": update}})

    def anfragen(self, methode, params):
        """Anfrage an den Editor; wartet auf die Antwort (oder das Ende der Verbindung)."""
        with self._schreiben:
            self._naechste += 1
            kennung = self._naechste
        warten = {"ereignis": threading.Event(), "antwort": None}
        self._wartend[kennung] = warten
        self.senden({"id": kennung, "method": methode, "params": params})
        while not warten["ereignis"].wait(0.5):
            if self._ende.is_set():
                break
        self._wartend.pop(kennung, None)
        return warten["antwort"] or {}

    def schleife(self):
        for zeile in self.ein:
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                n = json.loads(zeile)
            except ValueError:
                self.senden({"id": None, "error": {"code": -32700, "message": "Kein gültiges JSON."}})
                continue
            if "method" in n:
                if "id" in n:
                    # Jede Anfrage in eigenem Faden: session/prompt läuft lange, und
                    # währenddessen müssen Freigaben und session/cancel ankommen.
                    threading.Thread(target=self._anfrage, args=(n,), daemon=True).start()
                else:
                    self._benachrichtigung(n)
            elif n.get("id") in self._wartend:
                w = self._wartend[n["id"]]
                w["antwort"] = n.get("result") if "result" in n else {"fehler": n.get("error")}
                w["ereignis"].set()
        self._ende.set()
        for s in self.sitzungen.values():
            s.abbrechen.set()

    def _anfrage(self, n):
        try:
            methode = getattr(self, "rpc_" + n["method"].replace("/", "_"), None)
            if not methode:
                raise RpcFehler(-32601, "Unbekannte Methode: %s" % n["method"])
            self.senden({"id": n["id"], "result": methode(n.get("params") or {})})
        except RpcFehler as e:
            self.senden({"id": n["id"], "error": {"code": e.code, "message": str(e)}})
        except (Exception, SystemExit) as e:          # SystemExit: Meldungen aus dowos_cli
            sys.stderr.write("acp: %s: %s\n" % (n.get("method"), e))
            self.senden({"id": n["id"], "error": {"code": -32603, "message": str(e) or type(e).__name__}})

    def _benachrichtigung(self, n):
        if n["method"] == "session/cancel":
            s = self.sitzungen.get((n.get("params") or {}).get("sessionId"))
            if s:
                s.abbrechen.set()

    def _sitzung(self, params):
        s = self.sitzungen.get(params.get("sessionId"))
        if not s:
            raise RpcFehler(-32602, "Unbekannte Sitzung.")
        return s

    # ------------------------------------------------------------- Methoden ---
    def rpc_initialize(self, params):
        return {"protocolVersion": PROTOKOLL_VERSION,
                "agentCapabilities": {"loadSession": False,
                                      "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                                      "mcpCapabilities": {"http": True, "sse": False}},
                "authMethods": [],
                "agentInfo": {"name": "dowos", "title": "Dive on Wide Werkbank", "version": "1"}}

    def rpc_authenticate(self, params):
        return {}

    def rpc_session_new(self, params):
        ordner = params.get("cwd") or ""
        if not os.path.isabs(ordner) or not os.path.isdir(ordner):
            raise RpcFehler(-32602, "cwd muss ein vorhandener, absoluter Ordner sein.")
        s = Sitzung(os.path.realpath(ordner))
        self.sitzungen[s.id] = s
        try:
            s.mcp = mcp_konfig(params.get("mcpServers") or [])
        except ValueError as e:
            raise RpcFehler(-32602, "MCP-Server des Editors: %s" % e)
        return {"sessionId": s.id, "modes": {"currentModeId": s.modus, "availableModes": self._modi(s)}}

    def _modi(self, s):
        """Die vier Grundmodi, dazu jedes Agentenprofil als eigener Modus."""
        profile = agentenprofile.finden(s.ordner, os.path.join(cli.Einstellungen().storage, "werkbank", "agenten"))
        return MODI + [{"id": "profil:" + p["name"], "name": "Profil: " + p["name"], "description": p["beschreibung"]}
                       for p in profile.values()]

    def rpc_session_set_mode(self, params):
        s = self._sitzung(params)
        if params.get("modeId") not in {m["id"] for m in self._modi(s)}:
            raise RpcFehler(-32602, "Unbekannter Modus.")
        s.modus = params["modeId"]
        return {}

    def rpc_session_prompt(self, params):
        s = self._sitzung(params)
        if s.laeuft:
            raise RpcFehler(-32600, "In dieser Sitzung arbeitet der Agent noch.")
        s.laeuft = True
        s.abbrechen.clear()
        try:
            bloecke = params.get("prompt") or []
            return {"stopReason": self._arbeiten(s, prompt_text(bloecke), prompt_bilder(bloecke))}
        finally:
            s.laeuft = False

    # ------------------------------------------------------------- Arbeiten ---
    def _arbeiten(self, s, text, bilder=None):
        if not text.strip():
            raise RpcFehler(-32602, "Leere Nachricht.")
        e = cli.Einstellungen()
        ablage = os.path.join(e.storage, "werkbank")
        profile = agentenprofile.finden(s.ordner, os.path.join(ablage, "agenten"))
        profil = profile.get(s.modus[len("profil:"):]) if s.modus.startswith("profil:") else None
        chat, modell_ref = e.chat(self.modell or (profil or {}).get("modell"))
        for pr in profile.values():
            if pr.get("modell"):
                pr["chat"] = e.chat(pr["modell"])[0]
        stufe = {"lesen": "lesen", "voll": "voll"}.get(s.modus, "projekt")
        stufe, politik = agentenprofile.anwenden(profil, stufe, self.politik)
        externe = externe_agenten.Externe.aus_einstellung(e.get("WERKBANK_EXTERN"),
                                                          {"claude": e.get("CLAUDE_BIN"), "codex": e.get("CODEX_BIN")})
        wb = werkbank.Werkbank(s.ordner, stufe)
        aufgabe, befehl = werkbank_befehle.anwenden(text, werkbank_befehle.finden(s.ordner, os.path.join(ablage, "befehle")))
        offen = {"id": None, "vorbereitet": False}

        def schliessen(status="completed"):
            if offen["id"]:
                self.melden(s, {"sessionUpdate": "tool_call_update", "toolCallId": offen["id"], "status": status})
            offen.update(id=None, vorbereitet=False)

        def melden(zeile, zustand="done"):
            if s.abbrechen.is_set():
                raise Abgebrochen()
            m = SCHRITT_RE.match(zeile)
            if m and m.group(1) in ARTEN:
                titel = zeile.split(": ", 1)[1][:200]
                if offen["vorbereitet"]:            # der Schritt, der eben freigegeben wurde
                    self.melden(s, {"sessionUpdate": "tool_call_update", "toolCallId": offen["id"],
                                    "title": titel, "status": "in_progress"})
                    offen["vorbereitet"] = False
                    return
                schliessen()
                offen["id"] = "schritt_" + uuid.uuid4().hex[:10]
                self.melden(s, {"sessionUpdate": "tool_call", "toolCallId": offen["id"], "title": titel,
                                "kind": ARTEN[m.group(1)], "status": "in_progress"})
            else:
                schliessen()
                self.melden(s, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": zeile + "\n"}})

        def freigabe(beschreibung):
            if s.abbrechen.is_set():
                return False
            schliessen()
            offen.update(id="schritt_" + uuid.uuid4().hex[:10], vorbereitet=True)
            titel = beschreibung.strip().splitlines()[0][:200] if beschreibung.strip() else "Freigabe"
            erstes = re.match(r"\W*(\w+)", beschreibung)
            self.melden(s, {"sessionUpdate": "tool_call", "toolCallId": offen["id"], "title": titel,
                            "kind": ARTEN.get(erstes.group(1) if erstes else "", "other"), "status": "pending",
                            "content": [{"type": "content", "content": {"type": "text", "text": beschreibung}}]})
            antwort = self.anfragen("session/request_permission", {
                "sessionId": s.id,
                "toolCall": {"toolCallId": offen["id"], "title": titel, "status": "pending"},
                "options": [{"optionId": "erlauben", "name": "Erlauben", "kind": "allow_once"},
                            {"optionId": "ablehnen", "name": "Ablehnen", "kind": "reject_once"}]})
            ergebnis = antwort.get("outcome") or {}
            ok = ergebnis.get("outcome") == "selected" and ergebnis.get("optionId") == "erlauben"
            if not ok:
                schliessen("failed")
            return ok

        regeln, hinweise = werkbank_regeln.fuer_projekt(
            s.ordner, e.get("WERKBANK_REGELN"), werkbank_regeln.Vertrauen(os.path.join(ablage, "vertrauen.json")),
            fragen=lambda frage: freigabe("🔐 " + frage))
        if befehl:
            melden("Befehl /%s eingesetzt" % befehl)
        for h in hinweise:
            melden(h)
        melden("Projekt %s · %s · Sandbox %s · Modell %s" % (s.ordner, stufe, wb.sandbox or "keine", modell_ref))
        lauf_id = "acp" + uuid.uuid4().hex[:10]
        cp = checkpunkte.Checkpunkte(os.path.join(ablage, "checkpunkte"), s.ordner) if stufe != "lesen" else None
        kasten = None
        if s.mcp["mcpServers"]:
            # Server, die der Editor mitgibt, sind nicht „vertraut“: Jeder Aufruf wird gefragt.
            kasten = mcp_client.Werkzeugkasten(s.mcp, list(s.mcp["mcpServers"]))
            for name, grund in kasten.fehler.items():
                melden("⚠️ MCP „%s“ nicht verbunden: %s" % (name, grund))
            if kasten.verbindungen:
                melden("MCP des Editors verbunden: %s (%d Werkzeuge)" % (", ".join(kasten.verbindungen), len(kasten.werkzeuge())))
        try:
            ergebnis = werkbank.arbeiten(aufgabe, wb, chat, freigabe=freigabe, politik=politik,
                                         max_schritte=self.max_schritte,
                                         budget=int(int(e.get("NUM_CTX", "16384")) * 0.67),
                                         melden=melden, checkpunkte=cp, lauf=lauf_id, vorher=s.vorher,
                                         planmodus=s.modus == "plan" or bool((profil or {}).get("planmodus")), regeln=regeln,
                                         werkzeuge=(profil or {}).get("werkzeuge"), zusatz=(profil or {}).get("zusatz", ""),
                                         profile=profile, extern=externe if externe.verfuegbar() else None,
                                         mcp=kasten, bilder=bilder or None,
                                         ereignisse=schrittprotokoll.Schreiber(
                                             ablage, lauf_id, ordner=s.ordner, modell=modell_ref, stufe=stufe,
                                             freigabe=politik, quelle="acp", vorgaenger=s.letzter_lauf, zeit=time.time()),
                                         skills=agentskills.finden(s.ordner, os.path.join(ablage, "skills")),
                                         gedaechtnis=werkbank_gedaechtnis.Gedaechtnis(ablage))
        except Abgebrochen:
            schliessen("failed")
            return "cancelled"
        except checkpunkte.ZuGross as fehler:
            raise RpcFehler(-32603, "%s — Checkpunkte nicht möglich. Kleineren Ordner öffnen." % fehler)
        finally:
            if kasten:
                kasten.schliessen()
        schliessen()
        os.makedirs(ablage, exist_ok=True)
        for n_ in ergebnis.get("nachrichten", []):          # Bilder nicht als Base64 in die Trajektorie
            if n_.pop("images", None):
                n_["content"] += " [Bild entfernt]"
        with open(os.path.join(ablage, lauf_id + ".json"), "w", encoding="utf-8") as f:
            json.dump({"aufgabe": aufgabe, "ordner": s.ordner, "modell": modell_ref, "stufe": stufe,
                       "freigabe": politik, "zeit": time.time(), "ergebnis": ergebnis, "session_id": "",
                       "vorgaenger": s.letzter_lauf, "quelle": "acp"}, f, ensure_ascii=False, indent=1)
        s.vorher, s.letzter_lauf = ergebnis.get("nachrichten"), lauf_id
        if s.abbrechen.is_set():
            return "cancelled"
        self.melden(s, {"sessionUpdate": "agent_message_chunk", "content": {
            "type": "text", "text": werkbank.protokoll(aufgabe, s.ordner, ergebnis, stufe, politik, modell_ref)}})
        return "max_turn_requests" if ergebnis["beendet"] == "limit" else "end_turn"


def mcp_konfig(liste):
    """MCP-Server im ACP-Format (Liste, env/headers als [{name, value}]) → Dive-on-Wide-Konfiguration."""
    server = {}
    for eintrag in liste[:20]:
        if not isinstance(eintrag, dict):
            raise ValueError("Eintrag ist kein Objekt.")
        name = re.sub(r"[^\w.-]+", "-", str(eintrag.get("name") or "editor"))[:40].strip("-") or "editor"
        while name in server:
            name = (name[:37] + "-" + str(len(server)))
        paare = lambda schluessel: {str(x.get("name")): str(x.get("value", "")) for x in eintrag.get(schluessel) or []
                                     if isinstance(x, dict) and x.get("name")}
        art = eintrag.get("type")
        if art == "sse":
            raise ValueError("Server „%s“: SSE wird nicht unterstützt." % name)
        if art == "http" or eintrag.get("url"):
            server[name] = {"type": "http", "url": eintrag.get("url"), "headers": paare("headers")}
        else:
            server[name] = {"command": eintrag.get("command"), "args": eintrag.get("args") or [], "env": paare("env")}
    return mcp_client.konfiguration_pruefen({"mcpServers": server})


def prompt_bilder(bloecke):
    return [b["data"] for b in bloecke if b.get("type") == "image" and isinstance(b.get("data"), str)][:4]


def prompt_text(bloecke):
    """ContentBlocks → Aufgabentext. Vom Editor mitgeschickte Dateien werden angehängt."""
    teile = []
    for b in bloecke:
        art = b.get("type")
        if art == "text":
            teile.append(b.get("text", ""))
        elif art == "resource":
            r = b.get("resource") or {}
            if "text" in r:
                teile.append("\n\nDatei %s:\n```\n%s\n```" % (r.get("uri", ""), r["text"][:100_000]))
        elif art == "resource_link":
            teile.append(" (%s)" % (b.get("uri") or b.get("name") or ""))
    return "".join(teile)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="dowos acp", description="Werkbank-Agent über das Agent Client Protocol")
    ap.add_argument("--modell")
    ap.add_argument("--freigabe", choices=tuple(werkbank.FREIGABEN), default=werkbank.STANDARD_FREIGABE)
    ap.add_argument("--max-schritte", type=int, default=werkbank.MAX_SCHRITTE)
    a = ap.parse_args(argv)
    Agent(modell=a.modell, freigabe=a.freigabe, max_schritte=a.max_schritte).schleife()
    return 0


if __name__ == "__main__":
    sys.exit(main())
