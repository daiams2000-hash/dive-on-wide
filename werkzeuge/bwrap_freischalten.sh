#!/bin/sh
# Ubuntu ab 24.04: bubblewrap (die Sandbox der Werkbank) darf keine Namensräume anlegen — AppArmor sperrt das
# für Programme ohne eigenes Profil. Dieses Skript legt ein Profil an, das genau /usr/bin/bwrap das erlaubt,
# sonst nichts. Die GitHub-CI führt dasselbe Skript aus; funktioniert es nicht mehr, wird sie rot.
#
#     sudo sh werkzeuge/bwrap_freischalten.sh
set -e
if [ "$(id -u)" != 0 ]; then
  echo "Bitte mit sudo ausführen: sudo sh $0"; exit 1
fi
if [ ! -x /usr/bin/bwrap ]; then
  echo "bubblewrap fehlt: sudo apt install bubblewrap"; exit 1
fi
cat > /etc/apparmor.d/bwrap <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  include if exists <local/bwrap>
}
EOF
apparmor_parser -r /etc/apparmor.d/bwrap
if /usr/bin/bwrap --ro-bind / / --dev /dev --proc /proc --unshare-net --die-with-parent true; then
  echo "bubblewrap ist freigeschaltet — die Werkbank nutzt es ab dem nächsten Start von Dive on Wide."
else
  echo "bubblewrap läuft weiterhin nicht. Bis dahin fragt die Werkbank vor jedem Befehl."; exit 1
fi
