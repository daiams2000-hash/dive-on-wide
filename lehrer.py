# -*- coding: utf-8 -*-
"""Lehrer — große Modelle über ihre API, mit Kostenrechnung und harter Budgetgrenze.

Ein Lehrer ist ein Modell eines eingerichteten Anbieters (OpenAI, Anthropic über
seine OpenAI-kompatible Schnittstelle, Moonshot/Kimi, OpenRouter, lokal …). Kein
Modellname ist fest verdrahtet: GPT-6, Fable 5.1, Kimi K3 oder was danach kommt,
sind nur Einträge mit einem Preis.

**Budget** (in der Währung der Preise, meist USD): Vor jedem Aufruf wird der
ungünstigste Fall reserviert — geschätzte Eingabe-Token plus die volle
`max_tokens`-Ausgabe. Reicht das Restbudget dafür nicht, kommt der Aufruf gar nicht
erst zustande (`BudgetErschoepft`). Nach der Antwort wird mit den Token aus der
API abgerechnet; fehlen sie, wird geschätzt und das so vermerkt. So kann ein Lauf
das Budget nie überschreiten.

**Provenance** je Aufruf: Anbieter, angefragtes und tatsächlich antwortendes
Modell, SHA-256 des System-Prompts, Decoding-Einstellungen, Token, Kosten.

Nutzungsbedingungen: Viele Anbieter untersagen, mit ihren Ausgaben Modelle zu
entwickeln, die mit ihnen konkurrieren. Ob eine private Nutzung erlaubt ist,
entscheidet der Nutzer anhand der Bedingungen seines Anbieters.
"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

ZEICHEN_JE_TOKEN = 3.2


class BudgetErschoepft(RuntimeError):
    abbruch = True               # Aufrufer, die Modellfehler sonst abfangen (Fabrik), sollen hier aufhören


class KontingentErschoepft(RuntimeError):
    """Abo-Lehrer: 5-Stunden- oder Wochenlimit erreicht, oder das eigene Aufrufkontingent des Auftrags ist verbraucht."""
    abbruch = True


class LehrerFehler(RuntimeError):
    def __init__(self, text, unklar=False):
        super().__init__(text)
        self.unklar = unklar          # Verbindung abgerissen: Der Anbieter hat womöglich trotzdem berechnet


def token_schaetzen(nachrichten):
    return int(sum(len(str(n.get("content") or "")) for n in nachrichten) / ZEICHEN_JE_TOKEN) + 8 * len(nachrichten)


class Preis:
    """Preis je 1 Mio. Token. cache_eingabe: Preis für zwischengespeicherte Eingabe-Token, falls der Anbieter sie meldet."""

    def __init__(self, eingabe, ausgabe, cache_eingabe=None, waehrung="USD"):
        self.eingabe, self.ausgabe = float(eingabe), float(ausgabe)
        self.cache_eingabe = None if cache_eingabe in (None, "") else float(cache_eingabe)
        self.waehrung = waehrung
        if self.eingabe < 0 or self.ausgabe < 0:
            raise ValueError("Preise können nicht negativ sein.")

    def kosten(self, ein, aus, ein_cache=0):
        cache = min(ein_cache or 0, ein)
        teuer = ein - cache
        return (teuer * self.eingabe + cache * (self.cache_eingabe if self.cache_eingabe is not None else self.eingabe)
                + aus * self.ausgabe) / 1_000_000

    def als_dict(self):
        return {"eingabe": self.eingabe, "ausgabe": self.ausgabe, "cache_eingabe": self.cache_eingabe,
                "waehrung": self.waehrung}


class Budget:
    """Thread-sicher: parallele Lehreraufrufe reservieren gegen dasselbe Limit."""

    def __init__(self, grenze, bisher=0.0):
        self.grenze = None if grenze in (None, "") else float(grenze)
        self.ausgegeben = float(bisher)
        self.reserviert = 0.0
        self._schloss = threading.Lock()
        self.aufrufe = 0

    def reservieren(self, betrag):
        with self._schloss:
            if self.grenze is not None and self.ausgegeben + self.reserviert + betrag > self.grenze:
                raise BudgetErschoepft("Budget reicht nicht: %.4f ausgegeben, %.4f reserviert, nächster Aufruf bis %.4f, "
                                       "Grenze %.4f." % (self.ausgegeben, self.reserviert, betrag, self.grenze))
            self.reserviert += betrag

    def abrechnen(self, reserviert, tatsaechlich):
        with self._schloss:
            self.reserviert = max(0.0, self.reserviert - reserviert)
            self.ausgegeben += tatsaechlich
            self.aufrufe += 1

    def rest(self):
        return None if self.grenze is None else max(0.0, self.grenze - self.ausgegeben - self.reserviert)

    def stand(self):
        return {"grenze": self.grenze, "ausgegeben": round(self.ausgegeben, 6), "rest": None if self.grenze is None
                else round(self.rest(), 6), "aufrufe": self.aufrufe}


def _sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class Lehrer:
    """chat(nachrichten) -> Text, mit Budget und Protokoll. Spricht die OpenAI-Schnittstelle."""

    def __init__(self, name, basis_url, modell, api_key="", preis=None, budget=None, max_tokens=4096,
                 temperatur=0.2, anbieter="", frist=600, protokoll=None, ollama=False):
        self.name, self.basis_url, self.modell = name, basis_url.rstrip("/"), modell
        if self.basis_url.endswith("/v1"):
            self.basis_url = self.basis_url[:-3]
        self.api_key, self.preis, self.budget = api_key, preis, budget
        self.max_tokens, self.temperatur, self.anbieter, self.frist = max_tokens, temperatur, anbieter, frist
        self.protokoll = protokoll or (lambda eintrag: None)
        self.ollama = ollama
        self.tokens = 0                                    # wie stufe2-Chats: Ausgabe-Token gesamt
        self.summe = {"ein": 0, "aus": 0, "kosten": 0.0, "aufrufe": 0, "geschaetzt": 0}
        self._schalter = {"temperatur": True, "max_feld": "max_tokens"}

    def __call__(self, nachrichten):
        return self.chat(nachrichten)

    def _anfrage(self, nachrichten):
        if self.ollama:
            return self._ollama(nachrichten)
        nutzlast = {"model": self.modell, "messages": [{"role": n["role"], "content": n.get("content", "")} for n in nachrichten],
                    self._schalter["max_feld"]: self.max_tokens}
        if self._schalter["temperatur"] and self.temperatur is not None:
            nutzlast["temperature"] = self.temperatur
        kopf = {"Content-Type": "application/json"}
        if self.api_key:
            kopf["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.basis_url + "/v1/chat/completions", data=json.dumps(nutzlast).encode(), headers=kopf)
        try:
            with urllib.request.urlopen(req, timeout=self.frist) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:500]
            niedrig = text.lower()
            # Reasoning-Modelle lehnen temperature ab oder wollen max_completion_tokens — einmal angepasst wiederholen.
            if e.code == 400 and "temperature" in niedrig and self._schalter["temperatur"]:
                self._schalter["temperatur"] = False
                return self._anfrage(nachrichten)
            if e.code == 400 and "max_completion_tokens" in niedrig and self._schalter["max_feld"] == "max_tokens":
                self._schalter["max_feld"] = "max_completion_tokens"
                return self._anfrage(nachrichten)
            raise LehrerFehler("%s antwortet %s: %s" % (self.name, e.code, text))
        except (urllib.error.URLError, OSError) as e:
            raise LehrerFehler("%s nicht erreichbar: %s" % (self.name, e), unklar=True)

    def _ollama(self, nachrichten):
        nutzlast = {"model": self.modell, "messages": nachrichten, "stream": False, "think": False,
                    "options": {"temperature": self.temperatur, "num_predict": self.max_tokens}}
        req = urllib.request.Request(self.basis_url + "/api/chat", data=json.dumps(nutzlast).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.frist) as r:
                d = json.load(r)
        except (urllib.error.URLError, OSError) as e:
            raise LehrerFehler("%s nicht erreichbar: %s" % (self.name, e))
        return {"model": d.get("model"), "choices": [{"message": d.get("message") or {}}],
                "usage": {"prompt_tokens": d.get("prompt_eval_count"), "completion_tokens": d.get("eval_count")}}

    def chat(self, nachrichten):
        ein_schaetzung = token_schaetzen(nachrichten)
        reserve = self.preis.kosten(ein_schaetzung, self.max_tokens) if self.preis else 0.0
        if self.budget:
            self.budget.reservieren(reserve)
        t0 = time.time()
        abgerechnet = False
        try:
            try:
                d = self._anfrage(nachrichten)
            except LehrerFehler as fehler:
                if fehler.unklar and self.budget:
                    # Ob berechnet wurde, weiß nur der Anbieter — lieber den ungünstigsten Fall verbuchen.
                    self.budget.abrechnen(reserve, reserve)
                    abgerechnet = True
                raise
            text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            usage = d.get("usage") or {}
            ein, aus = usage.get("prompt_tokens"), usage.get("completion_tokens")
            cache = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
            geschaetzt = ein is None or aus is None
            if geschaetzt:
                ein, aus = ein_schaetzung, int(len(text) / ZEICHEN_JE_TOKEN)
            kosten = self.preis.kosten(ein, aus, cache) if self.preis else 0.0
            if self.budget:
                self.budget.abrechnen(reserve, kosten)
                abgerechnet = True
            self.tokens += aus
            self.summe["ein"] += ein
            self.summe["aus"] += aus
            self.summe["kosten"] += kosten
            self.summe["aufrufe"] += 1
            self.summe["geschaetzt"] += int(geschaetzt)
            system = next((n.get("content", "") for n in nachrichten if n.get("role") == "system"), "")
            self.protokoll({"ebene": "RAW_FACT", "art": "lehrer_aufruf", "lehrer": self.name, "anbieter": self.anbieter,
                            "modell_angefragt": self.modell, "modell_antwortet": d.get("model"),
                            "system_prompt_sha256": _sha(system), "nachrichten": len(nachrichten),
                            "decoding": {"temperature": self.temperatur if self._schalter["temperatur"] else None,
                                         self._schalter["max_feld"]: self.max_tokens},
                            "token_ein": ein, "token_aus": aus, "token_cache": cache, "token_geschaetzt": geschaetzt,
                            "kosten": round(kosten, 6), "sekunden": round(time.time() - t0, 2),
                            "antwort_sha256": _sha(text), "zeit": time.time()})
            return text
        finally:
            if self.budget and not abgerechnet:
                # Abgelehnter Aufruf (etwa 400): nichts berechnet — Reserve freigeben.
                self.budget.abrechnen(reserve, 0.0)


# ------------------------------------------------------------ Schätzung ---

def kosten_schaetzen(preis, anzahl, lauf_statistik=None, fabrik_statistik=None, fabrik_durch_lehrer=True):
    """Kosten für `anzahl` geprüfte Aufgaben — niedrig / erwartet / hoch.

    lauf_statistik: {"schritte": [..], "kontext_summe": [..], "aus_je_schritt": [..]} gemessen aus Schrittprotokollen
    (kontext_summe = Summe der Eingabe-Token über alle Schritte eines Laufs; der Kontext wächst mit jedem Schritt).
    fabrik_statistik: {"ausbeute": 0.45, "ein_je_entwurf": 1500, "aus_je_entwurf": 3000}
    Ohne Messwerte gelten vorsichtige Annahmen, und die Schätzung sagt das."""
    def quantile(werte, q, vorgabe):
        werte = sorted(w for w in (werte or []) if w is not None)
        if not werte:
            return vorgabe
        return werte[min(len(werte) - 1, int(q * (len(werte) - 1) + 0.5))]
    st = lauf_statistik or {}
    gemessen = bool(st.get("kontext_summe"))
    fb = dict({"ausbeute": 0.4, "ein_je_entwurf": 1800, "aus_je_entwurf": 3500}, **(fabrik_statistik or {}))
    aus = {}
    for stufe, q in (("niedrig", 0.25), ("erwartet", 0.5), ("hoch", 0.9)):
        kontext = quantile(st.get("kontext_summe"), q, {"niedrig": 40_000, "erwartet": 120_000, "hoch": 400_000}[stufe])
        schritte = quantile(st.get("schritte"), q, {"niedrig": 8, "erwartet": 15, "hoch": 30}[stufe])
        aus_schritt = quantile(st.get("aus_je_schritt"), q, {"niedrig": 120, "erwartet": 250, "hoch": 600}[stufe])
        ausbeute = max(0.05, fb["ausbeute"] * {"niedrig": 1.25, "erwartet": 1.0, "hoch": 0.7}[stufe])
        loesen = anzahl * preis.kosten(kontext, schritte * aus_schritt)
        entwuerfe = anzahl / ausbeute
        fabrik = preis.kosten(entwuerfe * fb["ein_je_entwurf"], entwuerfe * fb["aus_je_entwurf"]) if fabrik_durch_lehrer else 0.0
        aus[stufe] = {"kosten": round(loesen + fabrik, 4), "loesen": round(loesen, 4), "fabrik": round(fabrik, 4),
                      "entwuerfe": round(entwuerfe), "token_ein": int(anzahl * kontext + (entwuerfe * fb["ein_je_entwurf"] if fabrik_durch_lehrer else 0)),
                      "token_aus": int(anzahl * schritte * aus_schritt + (entwuerfe * fb["aus_je_entwurf"] if fabrik_durch_lehrer else 0))}
    aus["grundlage"] = "gemessen aus %d Läufen" % len(st.get("kontext_summe") or []) if gemessen else \
        "Annahmen (noch keine eigenen Läufe) — nach dem Probelauf genauer"
    aus["waehrung"] = preis.waehrung
    return aus


def openrouter_preise(basis_url="https://openrouter.ai", frist=15):
    """{modell_id: Preis} aus der öffentlichen Modellliste von OpenRouter (Preise je Token → je 1 Mio.)."""
    with urllib.request.urlopen(basis_url.rstrip("/") + "/api/v1/models", timeout=frist) as r:
        daten = json.load(r).get("data", [])
    aus = {}
    for m in daten:
        p = m.get("pricing") or {}
        try:
            aus[m["id"]] = Preis(float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6,
                                 float(p["input_cache_read"]) * 1e6 if p.get("input_cache_read") else None)
        except (KeyError, ValueError, TypeError):
            continue
    return aus


# ------------------------------------------------------------ Abo-Lehrer ---

import os as _os
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile

ABO_LIMIT_RE = _re.compile(r"usage limit|rate limit|limit reached|5-hour|weekly limit|resets? at|too many requests|overloaded",
                           _re.I)


def claude_befehl_finden(vorgabe=""):
    """Der Claude-Code-Befehl: Einstellung, PATH oder die Kopie der Desktop-App (neueste Version)."""
    if vorgabe and _os.path.isfile(_os.path.expanduser(vorgabe)):
        return _os.path.expanduser(vorgabe)
    im_pfad = _shutil.which("claude")
    if im_pfad:
        return im_pfad
    basis = _os.path.expanduser("~/Library/Application Support/Claude/claude-code")
    try:
        versionen = sorted(_os.listdir(basis), key=lambda v: [int(x) for x in _re.findall(r"\d+", v)], reverse=True)
    except OSError:
        return None
    for v in versionen:
        kandidat = _os.path.join(basis, v, "claude.app", "Contents", "MacOS", "claude")
        if _os.path.isfile(kandidat):
            return kandidat
    return None


def verlauf_als_text(nachrichten):
    """Das Werkbank-Gespräch für einen Aufruf ohne Mehrfach-Nachrichten: System-Prompt getrennt, der Rest als Protokoll."""
    teile = []
    for n in nachrichten:
        if n.get("role") == "system":
            continue
        rolle = {"user": "NUTZER / ERGEBNIS", "assistant": "DU (vorherige Antwort)"}.get(n.get("role"), n.get("role"))
        teile.append("### %s\n%s" % (rolle, n.get("content") or ""))
    teile.append("### AUFGABE JETZT\nAntworte mit genau dem nächsten Schritt im verlangten Format und sonst nichts.")
    return "\n\n".join(teile)


class AboLehrer(Lehrer):
    """Lehrer über ein Abo statt einer bezahlten API — derzeit Claude Code (Pro/Max).

    Jeder Schritt ist ein Aufruf `claude -p` ohne Werkzeuge, ohne Sitzungsspeicher, mit dem
    System-Prompt der Werkbank. Es entstehen keine Token-Kosten, aber das 5-Stunden- und
    Wochenlimit des Abos zählt: Meldet der Befehl ein Limit, endet der Auftrag mit
    KontingentErschoepft und lässt sich später fortsetzen. `max_aufrufe` begrenzt einen
    Auftrag zusätzlich.

    **Nutzungsbedingungen:** Die Bedingungen von Anthropic schränken ein, Ausgaben zum
    Trainieren von KI-Modellen zu verwenden. Proben eines Abo-Lehrers tragen deshalb
    `training_erlaubt: False` — sie prüfen das System (Orakel, Tore, Kostenstatistik),
    der Export nimmt sie nicht in Trainingsdaten auf."""

    training_erlaubt = False
    art = "abo"

    def __init__(self, name, modell, befehl=None, max_aufrufe=None, frist=900, protokoll=None, aufwand=None):
        super().__init__(name, "", modell, preis=None, budget=None, max_tokens=0, temperatur=None,
                         anbieter="Claude-Abo (Claude Code)", frist=frist, protokoll=protokoll)
        self.befehl = befehl or claude_befehl_finden()
        self.max_aufrufe = max_aufrufe
        self.aufwand = aufwand

    def _anfrage(self, nachrichten):
        if not self.befehl:
            raise LehrerFehler("Claude Code nicht gefunden — Desktop-App installieren oder den Pfad eintragen.")
        if self.max_aufrufe is not None and self.summe["aufrufe"] >= self.max_aufrufe:
            raise KontingentErschoepft("Aufrufkontingent dieses Auftrags erreicht (%d)." % self.max_aufrufe)
        system = next((n.get("content", "") for n in nachrichten if n.get("role") == "system"), "")
        argv = [self.befehl, "-p", "--output-format", "json", "--model", self.modell, "--tools", "",
                "--no-session-persistence", "--strict-mcp-config", "--system-prompt", system or "Du hilfst knapp."]
        if self.aufwand:
            argv += ["--effort", self.aufwand]
        arbeit = _tempfile.mkdtemp(prefix="dowos-abo-")
        try:
            # Ohne Werkzeuge, in einem leeren Ordner: Der Lehrer liest nichts vom Rechner, er antwortet nur.
            umgebung = {k: v for k, v in _os.environ.items() if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "ANTHROPIC_"))}
            p = _subprocess.run(argv, input=verlauf_als_text(nachrichten), capture_output=True, text=True,
                                timeout=self.frist, cwd=arbeit, env=umgebung)
        except _subprocess.TimeoutExpired:
            raise LehrerFehler("%s: keine Antwort nach %d s" % (self.name, self.frist))
        except OSError as e:
            raise LehrerFehler("%s: Befehl nicht startbar: %s" % (self.name, e))
        finally:
            _shutil.rmtree(arbeit, ignore_errors=True)
        try:
            d = json.loads(p.stdout)
        except ValueError:
            text = (p.stdout + p.stderr)[-500:]
            if ABO_LIMIT_RE.search(text):
                raise KontingentErschoepft("Abo-Limit erreicht: %s" % text.strip()[:200])
            raise LehrerFehler("%s: unlesbare Ausgabe: %s" % (self.name, text))
        ergebnis = str(d.get("result") or "")
        if d.get("is_error"):
            if "not logged in" in ergebnis.lower() or "/login" in ergebnis:
                fehler = LehrerFehler("Claude Code ist nicht angemeldet — einmal im Terminal `claude` starten und /login ausführen.")
                fehler.abbruch = True            # betrifft jeden weiteren Aufruf: den Auftrag beenden, nicht weiterprobieren
                raise fehler
            if ABO_LIMIT_RE.search(ergebnis) or d.get("api_error_status") == 429:
                raise KontingentErschoepft("Abo-Limit erreicht: %s" % ergebnis[:200])
            raise LehrerFehler("%s: %s" % (self.name, ergebnis[:300]))
        usage = d.get("usage") or {}
        modelle = list((d.get("modelUsage") or {}).keys())
        return {"model": modelle[0] if modelle else self.modell, "choices": [{"message": {"content": ergebnis}}],
                "usage": {"prompt_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
                          + (usage.get("cache_creation_input_tokens") or 0),
                          "completion_tokens": usage.get("output_tokens"),
                          "prompt_tokens_details": {"cached_tokens": usage.get("cache_read_input_tokens") or 0}}}
