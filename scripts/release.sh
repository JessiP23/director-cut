#!/usr/bin/env zsh
# ─────────────────────────────────────────────────────────────
# Director's Cut — Release Script
# Usage:
#   ./scripts/release.sh           → bumps patch (0.2.0 → 0.2.1)
#   ./scripts/release.sh minor     → bumps minor (0.2.0 → 0.3.0)
#   ./scripts/release.sh major     → bumps major (0.2.0 → 1.0.0)
#   ./scripts/release.sh 0.5.0     → sets exact version
#
# What it does:
#   1. Bumps version in tauri.conf.json + Cargo.toml
#   2. Runs cargo tauri build (local .dmg)
#   3. Git commits, tags, and pushes → triggers GitHub Actions draft release
# ─────────────────────────────────────────────────────────────
set -e

if [[ -f .env ]]; then
  source .env
  echo "Debug: .env sourced."
  export TAURI_SIGNING_PRIVATE_KEY
else
  echo "Debug: .env file not found."
fi

CONF="src-tauri/tauri.conf.json"
CARGO="src-tauri/Cargo.toml"
APP_NAME="director-cut"

# Gist Config
GIST_ID="f9c9818cbb6d8a28798e619fface794c"
# ── Read current version ────────────────────────────────────
CURRENT=$(python3 -c "import json; print(json.load(open('$CONF'))['version'])")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

BUMP="${1:-patch}"

case "$BUMP" in
  major)      NEW_VER="$((MAJOR+1)).0.0" ;;
  minor)      NEW_VER="${MAJOR}.$((MINOR+1)).0" ;;
  patch)      NEW_VER="${MAJOR}.${MINOR}.$((PATCH+1))" ;;
  [0-9]*)     NEW_VER="$BUMP" ;;  # exact version passed
  *)          echo "❌ Unknown bump type: $BUMP"; exit 1 ;;
esac

echo "🔖 Bumping $CURRENT → $NEW_VER"

# ── Update tauri.conf.json ──────────────────────────────────
python3 -c "
import json, sys
with open('$CONF') as f: d = json.load(f)
d['version'] = '$NEW_VER'
with open('$CONF', 'w') as f: json.dump(d, f, indent=2)
print('  ✅ tauri.conf.json updated')
"

# ── Update Cargo.toml (first [package] version = line) ──────
sed -i '' "0,/^version = \"[^\"]*\"/s//version = \"$NEW_VER\"/" "$CARGO"
echo "  ✅ Cargo.toml updated"

# ── Local build ─────────────────────────────────────────────
echo ""
echo "🔨 Building v$NEW_VER locally…"
cargo tauri build

# ── Find and print the .dmg ─────────────────────────────────
DMG=$(find src-tauri/target/release/bundle/dmg -name "*.dmg" 2>/dev/null | head -1)
if [[ -n "$DMG" ]]; then
  HASH=$(shasum -a 256 "$DMG" | awk '{print $1}')
  echo ""
  echo "✅ Build complete"
  echo "   DMG:    $DMG"
  echo "   SHA256: $HASH"
fi

# ── Find and process update artifacts ───────────────────────
UPDATE_TAR_GZ=$(find src-tauri/target/release/bundle/macos -name "*.app.tar.gz" 2>/dev/null | head -1)
UPDATE_SIG=$(find src-tauri/target/release/bundle/macos -name "*.app.tar.gz.sig" 2>/dev/null | head -1)

if [[ -n "$UPDATE_TAR_GZ" && -n "$UPDATE_SIG" ]]; then
  echo ""
  echo "📦 Found update artifacts:"
  echo "   Archive: $UPDATE_TAR_GZ"
  echo "   Signature: $UPDATE_SIG"

  # Read signature content
  SIGNATURE=$(cat "$UPDATE_SIG")
  ARTIFACT_FILENAME=$(basename "$UPDATE_TAR_GZ")

  # Construct releases.json content
  RELEASE_NOTES="New features and bug fixes for v$NEW_VER"
  GITHUB_REPO_OWNER="JessiP23"
  GITHUB_REPO_NAME="director-cut"
  ARTIFACT_URL="https://github.com/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/releases/download/v${NEW_VER}/${ARTIFACT_FILENAME}"

  RELEASES_JSON_CONTENT=$(cat <<-END
{
  "version": "$NEW_VER",
  "notes": "$RELEASE_NOTES",
  "pub_date": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "platforms": {
    "darwin-aarch64": {
      "signature": "$SIGNATURE",
      "url": "$ARTIFACT_URL"
    }
  }
}
END
)

  echo ""
  echo "📄 Generated releases.json content:"
  echo "$RELEASES_JSON_CONTENT" | head -n 10 # Show first 10 lines for brevity

  # Update GitHub Gist
  if [[ -z "$GITHUB_PAT" ]]; then
    echo "❌ GITHUB_PAT environment variable not set. Cannot update Gist."
  else
    echo "🚀 Updating Gist with releases.json…"
    JSON_PAYLOAD=$(jq -n \
      --arg content "$RELEASES_JSON_CONTENT" \
      '{files: {"releases.json": {content: $content}}}')

    curl -sS \
      -X PATCH \
      -H "Authorization: token $GITHUB_PAT" \
      -d "$JSON_PAYLOAD" \
      "https://api.github.com/gists/${GIST_ID}"
    echo "  ✅ Gist updated: https://gist.github.com/${GITHUB_REPO_OWNER}/${GIST_ID}"
  fi
else
  echo "❌ Could not find update artifacts (.app.tar.gz or .app.tar.gz.sig). Gist not updated."
fi

echo ""
echo "🚀 Pushing to GitHub (triggers draft release)…"
git add src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock
git commit -m "chore: bump version to v$NEW_VER"
git tag "v$NEW_VER"
git push origin HEAD
git push origin "v$NEW_VER"

echo ""
echo "🎉 Done! v$NEW_VER pushed."
echo "   GitHub Actions will build and create a DRAFT release."
echo "   Check: https://github.com/JessiP23/director-cut/releases"
echo ""
echo "   To publish: open the release on GitHub and click Publish release"