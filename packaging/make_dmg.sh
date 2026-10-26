#!/bin/bash
# dist/Prismix.app -> dist/Prismix.dmg (drag-to-Applications). Signs with a Developer ID when
# MACOS_CERTIFICATE_BASE64 is set, otherwise ad-hoc. Notarizes when the Apple secrets are present.
set -euo pipefail
cd "$(dirname "$0")/.."
APP=dist/Prismix.app
DMG=dist/Prismix.dmg
IDENTITY="${MACOS_CODESIGN_IDENTITY:--}"

if [[ -n "${MACOS_CERTIFICATE_BASE64:-}" ]]; then
  KEYCHAIN="${RUNNER_TEMP:-/tmp}/prismix-signing.keychain-db"
  PASS="${MACOS_KEYCHAIN_PASSWORD:-prismix-ci-keychain}"
  echo "$MACOS_CERTIFICATE_BASE64" | base64 --decode > "${RUNNER_TEMP:-/tmp}/prismix.p12"
  security create-keychain -p "$PASS" "$KEYCHAIN"
  security set-keychain-settings -lut 21600 "$KEYCHAIN"
  security unlock-keychain -p "$PASS" "$KEYCHAIN"
  security import "${RUNNER_TEMP:-/tmp}/prismix.p12" -P "${MACOS_CERTIFICATE_PASSWORD:-}" -A -t cert -f pkcs12 -k "$KEYCHAIN"
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$PASS" "$KEYCHAIN"
  security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')
  if [[ "$IDENTITY" == "-" ]]; then
    IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN" | awk -F '"' '/Developer ID Application/ { print $2; exit }')"
  fi
fi

echo "Signing with: $IDENTITY"
if [[ "$IDENTITY" == "-" ]]; then
  # Hardened runtime would reject the bundled libraries (no shared Team ID), so ad-hoc builds skip it.
  codesign --force --deep --timestamp=none -s - "$APP"
else
  codesign --force --deep --options runtime --entitlements packaging/entitlements.plist --timestamp -s "$IDENTITY" "$APP"
fi
xattr -cr "$APP"

STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
for i in 1 2 3 4 5; do
  hdiutil create -volname "Prismix" -srcfolder "$STAGE" -ov -format ULMO "$DMG" && break
  [[ $i == 5 ]] && exit 1
  sleep 5
done
rm -rf "$STAGE"

if [[ "$IDENTITY" != "-" ]]; then
  codesign --force --timestamp -s "$IDENTITY" "$DMG"
fi

if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" && "$IDENTITY" != "-" ]]; then
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" --wait
  xcrun stapler staple "$DMG"
fi
ls -lh "$DMG"
