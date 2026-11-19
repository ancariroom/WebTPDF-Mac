#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PRODUCT_ROOT=${SCRIPT_DIR:h:h}
BUILD_DIR="$SCRIPT_DIR/.build"
DIST_DIR="$PRODUCT_ROOT/40_Ops/dist"
STAGING_DIR="$BUILD_DIR/package/WebTPDF Mac"

APP_PATH=$(zsh "$SCRIPT_DIR/build_mac_app.sh")
VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP_PATH/Contents/Info.plist")
DMG_PATH="$DIST_DIR/WebTPDF-Mac-$VERSION.dmg"
ZIP_PATH="$DIST_DIR/WebTPDF-Mac-$VERSION.zip"
CHECKSUM_PATH="$DIST_DIR/SHA256SUMS.txt"

mkdir -p "$DIST_DIR"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

ditto "$APP_PATH" "$STAGING_DIR/WebTPDF Mac.app"
ln -s /Applications "$STAGING_DIR/Applications"

cat > "$STAGING_DIR/README_FIRST.txt" <<'TXT'
WebTPDF Mac

Install:
1. Drag "WebTPDF Mac.app" to Applications.
2. Open the app.

Notes:
- Captures paginated web viewers/PDFs into a single merged PDF saved to Desktop.
- Also supports converting non-DRM PDF / EPUB / MOBI / AZW3 files into audiobooks through Docker.
TXT

hdiutil create \
  -volname "WebTPDF Mac" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null

ditto -c -k --sequesterRsrc "$STAGING_DIR" "$ZIP_PATH"
shasum -a 256 "$DMG_PATH" "$ZIP_PATH" > "$CHECKSUM_PATH"

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_PATH"
fi

echo "$DMG_PATH"
echo "$ZIP_PATH"
echo "$CHECKSUM_PATH"
