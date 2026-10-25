#!/bin/sh
# Make http://stems.localhost (port 80) reach the app on 127.0.0.1:8000. Loopback only, run with sudo.
#   sudo scripts/stems-port80.sh on         until reboot
#   sudo scripts/stems-port80.sh install    on, and again at every boot (LaunchDaemon)
#   sudo scripts/stems-port80.sh off|uninstall
set -eu
ANCHOR="com.apple/stems"   # com.apple/* anchors are already evaluated by macOS's stock pf.conf
RULE="rdr pass on lo0 inet proto tcp from any to 127.0.0.1 port 80 -> 127.0.0.1 port ${STEMS_PORT:-8000}"
PLIST=/Library/LaunchDaemons/local.stems.port80.plist
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")

case "${1:-}" in
  on)
    echo "$RULE" | pfctl -a "$ANCHOR" -f - 2>&1 | grep -v "ALTQ\|Use of -f" || true
    pfctl -E 2>&1 | grep -v ALTQ || true ;;
  off)
    pfctl -a "$ANCHOR" -F all 2>&1 | grep -v ALTQ || true ;;
  install)
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>local.stems.port80</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>$SELF</string><string>on</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
PL
    chown root:wheel "$PLIST"; chmod 644 "$PLIST"
    "$0" on ;;
  uninstall)
    rm -f "$PLIST"; "$0" off ;;
  *) echo "usage: sudo $0 on|off|install|uninstall" >&2; exit 2 ;;
esac
