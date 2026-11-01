#!/bin/sh
# Builds an unsigned IPA with the bare Xcode tools (no Xcode project). Output: ios/build/Prismix.ipa
# Unsigned on purpose: AltStore, SideStore or Sideloadly re-sign it with the installer's own free Apple ID.
set -e
cd "$(dirname "$0")"
SDK=${PRISMIX_SDK:-iphoneos}
VN=${PRISMIX_VERSION:-0.0.0}; VN=${VN#v}
BUILD=$(echo "$VN" | awk -F. '{print $1*10000+$2*100+$3}')
rm -rf build && mkdir -p build/Prismix.app build/assets/Assets.xcassets/AppIcon.appiconset
SDKPATH=$(xcrun --sdk "$SDK" --show-sdk-path)
case "$SDK" in iphonesimulator) TRIPLE_SFX=-simulator ;; *) TRIPLE_SFX= ;; esac
for ARCH in arm64; do
  xcrun --sdk "$SDK" swiftc -O -swift-version 5 -parse-as-library -sdk "$SDKPATH" \
    -target $ARCH-apple-ios17.0$TRIPLE_SFX Sources/*.swift -o build/Prismix.app/Prismix
done
sed -e "s/@@VERSION@@/$VN/" -e "s/@@BUILD@@/$BUILD/" Info.plist > build/Prismix.app/Info.plist
sips -z 1024 1024 ../packaging/icon.png --out build/assets/Assets.xcassets/AppIcon.appiconset/icon.png >/dev/null
cat > build/assets/Assets.xcassets/AppIcon.appiconset/Contents.json <<'JSON'
{"images":[{"filename":"icon.png","idiom":"universal","platform":"ios","size":"1024x1024"}],"info":{"author":"xcode","version":1}}
JSON
xcrun actool build/assets/Assets.xcassets --compile build/Prismix.app --platform "$SDK" --minimum-deployment-target 17.0 \
  --app-icon AppIcon --output-partial-info-plist build/icon.plist --target-device iphone --target-device ipad >/dev/null
/usr/libexec/PlistBuddy -c "Merge build/icon.plist" build/Prismix.app/Info.plist
plutil -lint build/Prismix.app/Info.plist >/dev/null
if [ "$SDK" = iphoneos ]; then
  mkdir -p build/Payload && cp -R build/Prismix.app build/Payload/
  (cd build && zip -qry Prismix.ipa Payload)
  echo "ios/build/Prismix.ipa"
else
  echo "ios/build/Prismix.app (simulator)"
fi
