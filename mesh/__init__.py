"""Dive on Wide Mesh — der dezentrale Unterbau, auf dem Dive on Wide laufen soll.

Reines Peer-to-Peer, kein zentraler Server, alles nur im RAM.
Die Schichten von unten nach oben:

    crypto     Ed25519, X25519, XChaCha20-Poly1305 (gegen RFC-Vektoren geprüft)
    fluechtig  Geheimnisse im RAM, Vernichtung am Sitzungsende,
               nicht verknüpfbare Identitäten je Sitzung/Thema
"""

from . import (anker, arbeit, crypto, fluechtig, forum, inhalt, knoten,   # noqa: F401
               kademlia, qr, ratsche, ticket, verteilt, vertrauen,
               ressourcen, transport)

__all__ = ["anker", "arbeit", "crypto", "fluechtig", "forum", "inhalt",
           "kademlia", "knoten", "qr", "ratsche", "ticket", "verteilt",
           "vertrauen",
           "ressourcen", "transport"]
