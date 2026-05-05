#!/usr/bin/env zsh
set -euo pipefail

if [[ -f .env ]]; then
  source .env
  echo "Debug: .env sourced."
  export TAURI_SIGNING_PRIVATE_KEY
  export DIRECTOR_EMBED_GROQ_API_KEY DIRECTOR_EMBED_FAL_KEY
  export DIRECTOR_EMBED_SUPABASE_URL="$NEXT_PUBLIC_SUPABASE_URL"
  export DIRECTOR_EMBED_SUPABASE_ANON_KEY="$NEXT_PUBLIC_SUPABASE_ANON_KEY"
else
  echo "Debug: .env file not found."
fi

CONF="src-tauri/tauri.conf.json"
CARGO="src-tauri/Cargo.toml"

CURRENT=$(python3 -c "import json; print(json.load(open('$CONF'))['version'])")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

BUMP="${1:-patch}"

case "$BUMP" in
  major)  NEW_VER="$((MAJOR+1)).0.0" ;;
  minor)  NEW_VER="${MAJOR}.$((MINOR+1)).0" ;;
  patch)  NEW_VER="${MAJOR}.${MINOR}.$((PATCH+1))" ;;
  [0-9]*) NEW_VER="$BUMP" ;;
  *) echo "❌ Unknown bump type: $BUMP"; exit 1 ;;
esac

echo "🔖 Bumping $CURRENT → $NEW_VER"

python3 - <<PY
import json
with open("$CONF") as f:
    d = json.load(f)
d["version"] = "$NEW_VER"
with open("$CONF", "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
print("  ✅ tauri.conf.json updated")
PY

python3 - <<PY
from pathlib import Path
path = Path("$CARGO")
text = path.read_text()
old = 'version = "$CURRENT"'
new = 'version = "$NEW_VER"'
if old in text:
    text = text.replace(old, new, 1)
else:
    import re
    text = re.sub(r'^version = ".*?"$', new, text, count=1, flags=re.M)
path.write_text(text)
print("  ✅ Cargo.toml updated")
PY

echo ""
echo "🔨 Building v$NEW_VER locally for aarch64-apple-darwin…"
cargo tauri build --target aarch64-apple-darwin

DMG=$(find src-tauri/target/aarch64-apple-darwin/release/bundle/dmg -name "*.dmg" 2>/dev/null | head -1)
UPDATE_TAR_GZ=$(find src-tauri/target/aarch64-apple-darwin/release/bundle/macos -name "*.app.tar.gz" 2>/dev/null | head -1)
UPDATE_SIG=$(find src-tauri/target/aarch64-apple-darwin/release/bundle/macos -name "*.app.tar.gz.sig" 2>/dev/null | head -1)

if [[ -n "$DMG" ]]; then
  HASH=$(shasum -a 256 "$DMG" | awk '{print $1}')
  echo ""
  echo "✅ Build complete"
  echo "   DMG:    $DMG"
  echo "   SHA256: $HASH"
fi

if [[ -n "$UPDATE_TAR_GZ" && -n "$UPDATE_SIG" ]]; then
  echo ""
  echo "📦 Found update artifacts:"
  echo "   Archive: $UPDATE_TAR_GZ"
  echo "   Signature: $UPDATE_SIG"
fi

echo ""
echo "🚀 Pushing to GitHub…"
git add src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock
git commit -m "chore: bump version to v$NEW_VER"
git tag "v$NEW_VER"

# Push tag first, then code. 30s is paranoid but ensures propagation
git push origin "v$NEW_VER"
echo "Waiting 30s for tag to propagate to all GitHub servers..."
sleep 30
git push origin HEAD

echo ""
echo "🎉 Done! v$NEW_VER pushed."
echo "   Check: https://github.com/JessiP23/director-cut/releases"