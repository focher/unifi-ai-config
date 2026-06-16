#!/usr/bin/env bash
# Wrap the standalone macOS binary into a proper .app bundle and a distributable
# DMG (with an /Applications drop target). The .app is ad-hoc signed.
#
# Usage: packaging/macos_package.sh <binary> <out.dmg> <version>
set -euo pipefail

BIN="${1:?path to the built binary}"
OUT_DMG="${2:?output .dmg path}"
VERSION="${3:-0.0.0}"

APPNAME="UniFi AI Config Auditor"
BUNDLE_ID="com.focher.unifi-ai-auditor"
EXE="unifi-ai-auditor"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
APP="$WORK/$APPNAME.app"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$EXE"
chmod +x "$APP/Contents/MacOS/$EXE"
cp "$ROOT/assets/icon.icns" "$APP/Contents/Resources/icon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APPNAME</string>
  <key>CFBundleDisplayName</key><string>$APPNAME</string>
  <key>CFBundleExecutable</key><string>$EXE</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>icon.icns</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# Ad-hoc sign the bundle (Developer ID signing, if configured, happens upstream).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

# Stage the .app + Applications symlink, then build a compressed DMG.
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT_DMG"
hdiutil create -volname "$APPNAME" -srcfolder "$STAGE" -ov -format UDZO "$OUT_DMG" >/dev/null
echo "Built $OUT_DMG"
hdiutil imageinfo "$OUT_DMG" | grep -E "Format:|Compressed" || true
