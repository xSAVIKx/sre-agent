#!/usr/bin/env bash
# Renders overrides/assets/og-image.svg to overrides/assets/og-image.png (1200x630), the
# link preview image of the workshop website. Run it after a change to the SVG, and commit
# both files. It needs Chromium (or Chrome). optipng is optional. Put more Chromium flags in
# CHROMIUM_FLAGS, for example "--no-sandbox" in a container.
set -euo pipefail
cd "$(dirname "$0")/../overrides/assets"
browser=$(command -v chromium || command -v chromium-browser || command -v google-chrome)
"$browser" ${CHROMIUM_FLAGS:-} --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --window-size=1200,630 --screenshot="$PWD/og-image.png" "file://$PWD/og-image.svg"
if command -v optipng >/dev/null; then optipng -quiet -o5 og-image.png; fi
