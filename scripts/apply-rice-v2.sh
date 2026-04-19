#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Rice v2 apply script — Structured Thunder
#  Run this after the code/template changes landed.
#  Idempotent where possible; safe to re-run.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

cd "$HOME/.config"

echo "━━━ 1/6 — Install AUR + repo packages ━━━"
# base-devel + cmake + cpio are required by hyprpm for plugin builds (step 2).
paru -S --needed \
    base-devel cmake cpio \
    cava swappy matugen-bin maplemono-nf

echo ""
echo "━━━ 2/6 — Hyprland plugins (hyprpm) ━━━"
hyprpm update
# hyprexpo + hyprfocus both ship in the official hyprwm/hyprland-plugins repo.
# hyprtrails is intentionally skipped — upstream currently fails to build.
if ! hyprpm list 2>/dev/null | grep -q "hyprexpo"; then
    hyprpm add https://github.com/hyprwm/hyprland-plugins
fi
# Tolerate individual enable failures (e.g. version skew) — the rest can still proceed.
hyprpm enable hyprexpo || echo "  ⚠ hyprexpo enable failed — skipping"
hyprpm enable hyprfocus || echo "  ⚠ hyprfocus enable failed — skipping"

echo ""
echo "━━━ 3/6 — Enable plugins source in hyprland.conf ━━━"
sed -i 's|^# source = ~/\.config/hypr/plugins\.conf|source = ~/.config/hypr/plugins.conf|' \
    ~/.config/hypr/hyprland.conf

echo ""
echo "━━━ 4/6 — Deploy SDDM + Plymouth themes (sudo) ━━━"
python ./theme-generate.py --sddm --plymouth

# SDDM config — point to the theme
sudo mkdir -p /etc/sddm.conf.d
echo -e "[Theme]\nCurrent=gruvbox-dark" | sudo tee /etc/sddm.conf.d/theme.conf >/dev/null

# Plymouth activation
theme_name=$(python -c "import tomllib; print(tomllib.load(open('$HOME/.config/theme.toml','rb'))['plymouth']['theme_name'])")
sudo plymouth-set-default-theme -R "$theme_name"

echo ""
echo "━━━ 5/6 — Hot reload Hyprland + Waybar ━━━"
hyprctl reload
pkill -SIGUSR2 waybar || true

echo ""
echo "━━━ 6/6 — Verification ━━━"
echo "Loaded Hyprland plugins:"
hyprctl plugin list
echo ""
echo "Maple Mono installed:"
fc-list | grep -i maple | head -3 || echo "  (none found — check pkg)"
echo ""
echo "Cava binary: $(command -v cava || echo 'MISSING')"
echo "Swappy binary: $(command -v swappy || echo 'MISSING')"
echo "Matugen binary: $(command -v matugen || echo 'MISSING')"
echo ""
echo "Done. A reboot will pick up the Plymouth splash."
