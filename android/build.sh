#!/bin/sh
# Builds a debug-signed APK with the bare SDK tools (no Gradle). Output: android/build/prismix-band.apk
set -e
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}
BT=$SDK/build-tools/34.0.0
JAR=$SDK/platforms/android-34/android.jar
rm -rf build && mkdir -p build/classes
javac --release 8 -g:none -Xlint:-options -cp "$JAR:lib/zxing-core-3.5.3.jar" -d build/classes src/app/*.java
$BT/d8 --min-api 24 --lib "$JAR" --output build $(find build/classes -name '*.class') lib/zxing-core-3.5.3.jar
VN=${PRISMIX_VERSION:-}
VN=${VN#v}
cp AndroidManifest.xml build/AndroidManifest.xml
case "$VN" in
  [0-9]*.[0-9]*.[0-9]*)
    # the app's version follows the release: 3.2.0 -> versionCode 30200
    VC=$(echo "$VN" | awk -F. '{print $1*10000+$2*100+$3}')
    sed -i.bak -e "s/android:versionCode=\"[0-9]*\"/android:versionCode=\"$VC\"/" -e "s/android:versionName=\"[^\"]*\"/android:versionName=\"$VN\"/" build/AndroidManifest.xml
    rm -f build/AndroidManifest.xml.bak ;;
esac
$BT/aapt2 compile --dir res -o build/res.zip
# The phone page ships inside the app: it is what runs when no host is around (offline songs).
mkdir -p build/assets/web && cp -R ../prismix/web/. build/assets/web/
rm -rf build/assets/web/vendor/jspdf.umd.min.js
$BT/aapt2 link -o build/base.apk -I "$JAR" -A build/assets --manifest build/AndroidManifest.xml build/res.zip
(cd build && zip -q -j base.apk classes.dex)
$BT/zipalign -f 4 build/base.apk build/aligned.apk
KS=${PRISMIX_KEYSTORE:-$HOME/.android/debug.keystore}
[ -f "$KS" ] || { mkdir -p "$(dirname "$KS")"; keytool -genkeypair -keystore "$KS" -storepass android -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1; }
$BT/apksigner sign --ks "$KS" --ks-pass pass:android --key-pass pass:android --out build/prismix-band.apk build/aligned.apk
echo build/prismix-band.apk
