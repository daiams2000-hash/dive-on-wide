# -*- coding: utf-8 -*-
"""MCP-Client — fremde Werkzeuge über das Model Context Protocol anbinden.

Claude Code, Codex und Hermes binden Werkzeuge über MCP an: eine Datenbank,
GitHub, ein Ticketsystem, ein Browser. Ein MCP-Server ist ein eigenes Programm;
Dive on Wide startet es, fragt, welche Werkzeuge es hat, und ruft sie im Auftrag des
Werkbank-Agenten auf.

Zwei Transporte: **stdio** (ein lokal gestartetes Programm, JSON-RPC 2.0 je
Zeile) und **Streamable HTTP** (entfernte Server; Antworten als JSON oder als
Server-Sent Events, Sitzung über `Mcp-Session-Id`). Anfragen des Servers an
den Client über einen eigenen SSE-Kanal bietet Dive on Wide nicht an.

Die Konfiguration hat dasselbe Format wie bei Claude Desktop und Claude Code,
eine vorhandene lässt sich also übernehmen:

    {"mcpServers": {"dateien": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/pfad"],
                                "env": {}, "vertraut": false}}}

`vertraut` ist die einzige Dive-on-Wide-Ergänzung: Ohne sie fragt die Werkbank vor
jedem Aufruf. Ein MCP-Server läuft nicht in der Sandbox der Werkbank — er darf,
was sein Programm darf.

Reine Standardbibliothek.
"""

import json
import os
import queue
import urllib.error
import urllib.request
import re
import subprocess
import threading
import time

PROTOKOLLE = ("2025-06-18", "2025-03-26", "2024-11-05")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
# Werkzeug- und Parameternamen kommen vom Server und landen im System-Prompt und
# in der Freigabe. Die Spezifikation erlaubt genau diese Zeichen; ein Name mit
# Zeilenumbruch oder Anweisungstext ist kein Werkzeug, sondern ein Einschleusversuch.
WERKZEUG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
ANTWORT_GRENZE = 6000


class McpFehler(Exception):
    pass


def konfiguration_pruefen(daten):
    """Normalisiert eine Konfiguration im Claude-Format. Wirft ValueError mit klarem Grund."""
    if not isinstance(daten, dict):
        raise ValueError("Die Konfiguration muss ein JSON-Objekt sein.")
    server = daten.get("mcpServers", daten.get("server", {}))
    if not isinstance(server, dict):
        raise ValueError("„mcpServers“ muss ein Objekt aus Name → Server sein.")
    aus = {}
    for name, s in server.items():
        if not NAME_RE.fullmatch(str(name)):
            raise ValueError("Servername „%s“: nur Buchstaben, Ziffern, _ . - (höchstens 40 Zeichen)." % name)
        if not isinstance(s, dict):
            raise ValueError("Server „%s“ muss ein Objekt sein." % name)
        if s.get("type") == "sse":
            raise ValueError("Server „%s“: der alte SSE-Transport wird nicht unterstützt — nutze \"type\": \"http\" "
                             "(Streamable HTTP)." % name)
        if s.get("url") or s.get("type") in ("http", "streamable-http"):
            url = str(s.get("url") or "")
            if not url.startswith(("https://", "http://")):
                raise ValueError("Server „%s“ braucht eine „url“ mit http:// oder https://." % name)
            kopf = s.get("headers", {})
            if not isinstance(kopf, dict) or not all(isinstance(v, str) for v in kopf.values()):
                raise ValueError("Server „%s“: „headers“ muss Name → Text sein." % name)
            aus[name] = {"type": "http", "url": url, "headers": kopf, "vertraut": bool(s.get("vertraut")),
                         "aktiv": s.get("aktiv", True) is not False, "env": {}}
            continue
        befehl = s.get("command")
        if not isinstance(befehl, str) or not befehl.strip():
            raise ValueError("Server „%s“ braucht „command“ (das zu startende Programm)." % name)
        args = s.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError("Server „%s“: „args“ muss eine Liste von Texten sein." % name)
        env = s.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
            raise ValueError("Server „%s“: „env“ muss Name → Text sein." % name)
        aus[name] = {"command": befehl, "args": args, "env": env, "vertraut": bool(s.get("vertraut")),
                     "aktiv": s.get("aktiv", True) is not False}
    return {"mcpServers": aus}


class Verbindung:
    """Ein laufender MCP-Server über stdio."""

    def __init__(self, name, konfig, frist=30):
        self.name = name
        self.konfig = konfig
        self.frist = frist
        self._id = 0
        self._wartend = {}
        self._lock = threading.Lock()
        self._schreiben = threading.Lock()     # Leser-Thread und Aufrufer schreiben beide
        self._stderr = []
        self.proc = None
        self.info = {}
        self.werkzeuge = []
        self.ausgelassen = 0                     # Werkzeuge mit unzulässigem Namen

    # ----------------------------------------------------------- Lebenszyklus
    def starten(self):
        umgebung = dict(os.environ)
        umgebung.update(self.konfig.get("env") or {})
        try:
            self.proc = subprocess.Popen([self.konfig["command"]] + list(self.konfig.get("args") or []),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=umgebung, start_new_session=(os.name != "nt"))
        except OSError as e:
            raise McpFehler("„%s“ ließ sich nicht starten: %s" % (self.name, e))
        threading.Thread(target=self._lesen, daemon=True).start()
        threading.Thread(target=self._fehler_lesen, daemon=True).start()
        antwort = self._anfrage("initialize", {
            "protocolVersion": PROTOKOLLE[0], "capabilities": {},
            "clientInfo": {"name": "Dive on Wide", "version": "werkbank"}})
        version = antwort.get("protocolVersion")
        if version not in PROTOKOLLE:
            self.schliessen()
            raise McpFehler("„%s“ spricht MCP-Version %s, Dive on Wide kennt %s." % (self.name, version, ", ".join(PROTOKOLLE)))
        self.info = {"version": version, "server": antwort.get("serverInfo") or {},
                     "hinweise": antwort.get("instructions") or ""}
        self._senden({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.werkzeuge = self._alle_werkzeuge()
        return self

    def _alle_werkzeuge(self):
        werkzeuge, cursor = [], None
        for _ in range(20):                      # Seiten; ein Server ohne Ende hängt uns nicht auf
            antwort = self._anfrage("tools/list", {"cursor": cursor} if cursor else {})
            for w in antwort.get("tools") or []:
                if isinstance(w, dict) and WERKZEUG_RE.fullmatch(str(w.get("name", ""))):
                    werkzeuge.append(w)
                else:
                    self.ausgelassen += 1
            cursor = antwort.get("nextCursor")
            if not cursor:
                break
        return werkzeuge

    def schliessen(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        with self._lock:
            for q in self._wartend.values():
                q.put({"error": {"message": "Verbindung geschlossen"}})

    def __enter__(self):
        return self.starten()

    def __exit__(self, *a):
        self.schliessen()

    # ------------------------------------------------------------- Transport
    def _senden(self, nachricht):
        try:
            with self._schreiben:
                self.proc.stdin.write((json.dumps(nachricht, ensure_ascii=False) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
        except (OSError, ValueError):
            raise McpFehler("„%s“ ist beendet%s" % (self.name, self._letzter_fehler()))

    def _letzter_fehler(self):
        return (": " + " | ".join(self._stderr[-3:])) if self._stderr else ""

    def _fehler_lesen(self):
        for zeile in self.proc.stderr:
            self._stderr.append(zeile.decode("utf-8", "replace").strip()[:300])
            del self._stderr[:-20]

    def _lesen(self):
        for zeile in self.proc.stdout:
            try:
                n = json.loads(zeile)
            except ValueError:
                continue                          # Server, die Logs auf stdout schreiben
            if not isinstance(n, dict):
                continue
            if "method" in n and "id" in n:
                # Anfrage des Servers an uns. Ping beantworten, alles andere
                # (sampling, roots, elicitation) bietet Dive on Wide nicht an.
                if n["method"] == "ping":
                    self._senden_still({"jsonrpc": "2.0", "id": n["id"], "result": {}})
                else:
                    self._senden_still({"jsonrpc": "2.0", "id": n["id"],
                                        "error": {"code": -32601, "message": "Dive on Wide unterstützt %s nicht" % n["method"]}})
                continue
            if "id" in n:
                with self._lock:
                    q = self._wartend.pop(n["id"], None)
                if q:
                    q.put(n)
        with self._lock:
            for q in self._wartend.values():
                q.put({"error": {"message": "Server beendet%s" % self._letzter_fehler()}})
            self._wartend.clear()

    def _senden_still(self, nachricht):
        try:
            self._senden(nachricht)
        except McpFehler:
            pass

    def _anfrage(self, methode, parameter, frist=None):
        with self._lock:
            self._id += 1
            kennung = self._id
            q = queue.Queue()
            self._wartend[kennung] = q
        self._senden({"jsonrpc": "2.0", "id": kennung, "method": methode, "params": parameter})
        try:
            antwort = q.get(timeout=frist or self.frist)
        except queue.Empty:
            with self._lock:
                self._wartend.pop(kennung, None)
            self._senden_still({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                "params": {"requestId": kennung, "reason": "Zeitüberschreitung"}})
            raise McpFehler("„%s“ hat auf %s nicht innerhalb von %d s geantwortet." % (self.name, methode, frist or self.frist))
        if "error" in antwort:
            raise McpFehler("„%s“: %s" % (self.name, (antwort["error"] or {}).get("message", "Fehler")))
        return antwort.get("result") or {}

    # ------------------------------------------------------------- Werkzeuge
    def aufrufen(self, werkzeug, eingabe, frist=120):
        if not any(w["name"] == werkzeug for w in self.werkzeuge):
            raise McpFehler("„%s“ hat kein Werkzeug „%s“." % (self.name, werkzeug))
        ergebnis = self._anfrage("tools/call", {"name": werkzeug, "arguments": eingabe or {}}, frist)
        teile = []
        for c in ergebnis.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                teile.append(c.get("text", ""))
            elif c.get("type") == "resource" and isinstance(c.get("resource"), dict):
                teile.append(c["resource"].get("text") or "[Ressource %s]" % c["resource"].get("uri", ""))
            else:
                teile.append("[%s-Inhalt ausgelassen]" % c.get("type", "unbekannt"))
        if not teile and ergebnis.get("structuredContent") is not None:
            teile.append(json.dumps(ergebnis["structuredContent"], ensure_ascii=False))
        text = "\n".join(teile) or "(keine Ausgabe)"
        if len(text) > ANTWORT_GRENZE:
            text = text[:ANTWORT_GRENZE] + "\n… [%d Zeichen ausgelassen]" % (len(text) - ANTWORT_GRENZE)
        return bool(ergebnis.get("isError")), text


class HttpVerbindung(Verbindung):
    """Ein entfernter MCP-Server über Streamable HTTP."""

    def starten(self):
        self._sitzung = None
        antwort = self._anfrage("initialize", {
            "protocolVersion": PROTOKOLLE[0], "capabilities": {},
            "clientInfo": {"name": "Dive on Wide", "version": "werkbank"}})
        version = antwort.get("protocolVersion")
        if version not in PROTOKOLLE:
            raise McpFehler("„%s“ spricht MCP-Version %s, Dive on Wide kennt %s." % (self.name, version, ", ".join(PROTOKOLLE)))
        self.info = {"version": version, "server": antwort.get("serverInfo") or {},
                     "hinweise": antwort.get("instructions") or ""}
        self._version = version
        self._senden({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.werkzeuge = self._alle_werkzeuge()
        return self

    def schliessen(self):
        if getattr(self, "_sitzung", None):
            try:
                req = urllib.request.Request(self.konfig["url"], method="DELETE", headers=self._kopf())
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:
                pass

    def _kopf(self):
        kopf = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        kopf.update(self.konfig.get("headers") or {})
        if getattr(self, "_sitzung", None):
            kopf["Mcp-Session-Id"] = self._sitzung
        if getattr(self, "_version", None):
            kopf["MCP-Protocol-Version"] = self._version
        return kopf

    def _post(self, nachricht, frist):
        req = urllib.request.Request(self.konfig["url"], data=json.dumps(nachricht).encode("utf-8"), headers=self._kopf())
        try:
            antwort = urllib.request.urlopen(req, timeout=frist)
        except urllib.error.HTTPError as e:
            raise McpFehler("„%s“: HTTP %d %s" % (self.name, e.code, e.read()[:200].decode("utf-8", "replace")))
        except (urllib.error.URLError, OSError) as e:
            raise McpFehler("„%s“ nicht erreichbar: %s" % (self.name, e))
        sitzung = antwort.headers.get("Mcp-Session-Id")
        if sitzung:
            self._sitzung = sitzung
        return antwort

    def _senden(self, nachricht):
        with self._post(nachricht, self.frist) as a:
            a.read()

    def _anfrage(self, methode, parameter, frist=None):
        with self._lock:
            self._id += 1
            kennung = self._id
        antwort = self._post({"jsonrpc": "2.0", "id": kennung, "method": methode, "params": parameter}, frist or self.frist)
        with antwort:
            art = antwort.headers.get("Content-Type", "")
            if "text/event-stream" in art:
                # Der Server kann vor der Antwort Benachrichtigungen schicken; gesucht ist die mit unserer id.
                ergebnis, daten = None, []
                for zeile in antwort:
                    zeile = zeile.decode("utf-8", "replace").rstrip("\r\n")
                    if zeile.startswith("data:"):
                        daten.append(zeile[5:].strip())
                    elif not zeile and daten:
                        try:
                            n = json.loads("\n".join(daten))
                        except ValueError:
                            n = None
                        daten = []
                        if isinstance(n, dict) and n.get("id") == kennung and ("result" in n or "error" in n):
                            ergebnis = n
                            break
                if ergebnis is None:
                    raise McpFehler("„%s“ hat auf %s nicht geantwortet." % (self.name, methode))
            else:
                try:
                    ergebnis = json.loads(antwort.read() or b"{}")
                except ValueError:
                    raise McpFehler("„%s“: Antwort ist kein JSON." % self.name)
        if "error" in ergebnis:
            raise McpFehler("„%s“: %s" % (self.name, (ergebnis["error"] or {}).get("message", "Fehler")))
        return ergebnis.get("result") or {}


def verbindung_fuer(name, konfig, frist=30):
    return (HttpVerbindung if konfig.get("type") == "http" else Verbindung)(name, konfig, frist)


class Werkzeugkasten:
    """Mehrere MCP-Server für einen Werkbank-Lauf — als ein zusätzliches Werkzeug „mcp“."""

    def __init__(self, konfig, namen, frist=30):
        self.verbindungen, self.fehler = {}, {}
        server = konfiguration_pruefen(konfig)["mcpServers"]
        for name in namen:
            if name not in server:
                self.fehler[name] = "nicht konfiguriert"
                continue
            try:
                self.verbindungen[name] = verbindung_fuer(name, server[name], frist).starten()
            except McpFehler as e:
                self.fehler[name] = str(e)
        self.vertraut = {n for n in self.verbindungen if server[n]["vertraut"]}

    def schliessen(self):
        for v in self.verbindungen.values():
            v.schliessen()

    def werkzeuge(self):
        return [("%s/%s" % (n, w["name"]), w) for n, v in self.verbindungen.items() for w in v.werkzeuge]

    def beschreibung(self, grenze=40):
        """Die Werkzeuge für den System-Prompt: Name, Zweck, Parameter."""
        zeilen = []
        for voll, w in self.werkzeuge()[:grenze]:
            schema = w.get("inputSchema") if isinstance(w.get("inputSchema"), dict) else {}
            felder = schema.get("properties") or {}
            pflicht = schema.get("required") if isinstance(schema.get("required"), list) else []
            if not isinstance(felder, dict):
                felder = {}
            namen = [k for k in felder if WERKZEUG_RE.fullmatch(str(k))][:20]
            parameter = ", ".join("%s%s" % (k, "" if k in pflicht else "?") for k in namen)
            zeilen.append("- %s (%s): %s" % (voll, parameter or "keine Parameter",
                                             re.sub(r"\s+", " ", str(w.get("description", "")))[:200]))
        if len(self.werkzeuge()) > grenze:
            zeilen.append("- … %d weitere nicht aufgeführt" % (len(self.werkzeuge()) - grenze))
        return "\n".join(zeilen)

    def aufrufen(self, voll, eingabe):
        server, _, werkzeug = str(voll).partition("/")
        if server not in self.verbindungen:
            raise ValueError("MCP-Server „%s“ ist in diesem Lauf nicht verbunden." % server)
        if not isinstance(eingabe, dict):
            raise ValueError("„eingabe“ muss ein JSON-Objekt sein.")
        try:
            fehler, text = self.verbindungen[server].aufrufen(werkzeug, eingabe)
        except McpFehler as e:
            raise ValueError(str(e))
        return ("Fehler vom Werkzeug: " if fehler else "") + text


def pruefen(name, konfig, frist=20):
    """Startet einen Server einmal, liest seine Werkzeuge und beendet ihn wieder."""
    server = konfiguration_pruefen(konfig)["mcpServers"]
    if name not in server:
        raise ValueError("Server „%s“ ist nicht konfiguriert." % name)
    t0 = time.time()
    with verbindung_fuer(name, server[name], frist) as v:
        return {"name": name, "version": v.info["version"], "server": v.info["server"],
                "werkzeuge": [{"name": w["name"], "beschreibung": str(w.get("description", ""))[:300]}
                              for w in v.werkzeuge],
                "ausgelassen": v.ausgelassen, "sekunden": round(time.time() - t0, 2)}
