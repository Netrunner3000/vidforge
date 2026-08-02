#!/bin/bash
# Builds "vidforge.app" with PyInstaller and installs it into /Applications.
set -euo pipefail
cd "$(dirname "$0")"

source .venv/bin/activate
uv pip install -q pyinstaller

python make_icon.py

rm -rf build dist

# config.yaml / topics.txt / .env.example are seeded into
# ~/Library/Application Support/vidforge on first launch (see vidforge/config.py),
# so they ship as bundle resources rather than being written to in place.
pyinstaller --noconfirm --windowed \
  --name "vidforge" \
  --icon assets/icon.icns \
  --add-data "config.yaml:." \
  --add-data "topics.txt:." \
  --add-data ".env.example:." \
  --collect-submodules vidforge \
  app.py

rm -rf "/Applications/vidforge.app"
cp -R "dist/vidforge.app" /Applications/
touch "/Applications/vidforge.app"  # nudge Finder/Dock to refresh the cached icon

echo
echo "Installed: /Applications/vidforge.app"
echo "Settings, topics and rendered videos live in:"
echo "  ~/Library/Application Support/vidforge"
echo
echo "Put your OPENAI_API_KEY in that folder's .env before the first render"
echo "(the app's Settings tab has an 'Open config folder' button)."

# Remove build artifacts so Spotlight doesn't index a second copy of the .app.
rm -rf build dist
