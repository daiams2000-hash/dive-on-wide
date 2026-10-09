# Dive on Wide — ein eigener Bildschirm für Computer-Use.
#
# Der Agent soll nicht auf DEINEM Schirm klicken, sondern auf einem, den man
# wegwerfen kann. Drin ist genau so viel, wie dafür nötig ist:
#
#   Xvfb        ein X-Server ohne Grafikkarte — ein Bildschirm im Speicher
#   xdotool     Maus und Tastatur
#   maim        Bildschirmfotos (schreibt nach stdout, nichts bleibt liegen)
#   openbox     ein winziger Fenstermanager; ohne ihn haben Fenster keine
#               Rahmen und lassen sich nicht bewegen — der Agent säße vor
#               einem Bild, das er nicht bedienen kann
#   x11vnc      damit DU zusehen kannst, was dort geschieht (optional)
#
# Bewusst NICHT drin: ein Browser, ein Mailprogramm, irgendwelche Anmeldedaten.
# Was der Agent braucht, wird ausdrücklich hineingegeben — nicht vorsorglich
# bereitgelegt.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc openbox xdotool maim x11-utils xterm \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV DISPLAY=:99
ENV SCHIRM_BREITE=1280
ENV SCHIRM_HOEHE=800

COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

# 5900 ist der VNC-Port. Er wird NICHT automatisch nach außen gereicht —
# das entscheidet das Startskript, und standardmäßig bleibt er zu.
EXPOSE 5900

CMD ["/usr/local/bin/start.sh"]
