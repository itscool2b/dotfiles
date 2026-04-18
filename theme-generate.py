#!/usr/bin/env python3
"""Centralized theme generator — reads theme.toml, writes all config files."""

import argparse
import difflib
import glob as globmod
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import jinja2

BASE = Path.home() / ".config"
THEME_FILE = BASE / "theme.toml"
TEMPLATE_DIR = BASE / "theme-templates"

# ════════════════════════════════════════════
#  Color format conversion filters
# ════════════════════════════════════════════

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def f_bare(h):
    """#1d2021 -> 1d2021"""
    return h.lstrip("#")

def f_rgba(h, alpha="ff"):
    """#1d2021 -> rgba(1d2021ff)"""
    return f"rgba({f_bare(h)}{alpha})"

def f_ansi(h):
    """#1d2021 -> 38;2;29;32;33"""
    r, g, b = hex_to_rgb(h)
    return f"38;2;{r};{g};{b}"

def f_qt_rgb(h):
    """#1d2021 -> 29/255, 32/255, 33/255"""
    r, g, b = hex_to_rgb(h)
    return f"{r}/255, {g}/255, {b}/255"

def f_rgb_vals(h):
    """#1d2021 -> 29, 32, 33"""
    r, g, b = hex_to_rgb(h)
    return f"{r}, {g}, {b}"

# ════════════════════════════════════════════
#  Qt palette generation
# ════════════════════════════════════════════

# Qt palette roles (22 roles): WindowText, Button, Light, Midlight, Dark, Mid,
# Text, BrightText, ButtonText, Base, Window, Shadow, Highlight, HighlightedText,
# Link, LinkVisited, AlternateBase, ToolTipBase, ToolTipText, PlaceholderText, Accent, ???
# Last two vary by Qt version; we use the values from the existing Trolltech.conf

QT_PALETTE = {
    "active": [
        "fg", "bg1", "bg3", "bg2", "bg0", "bg2", "fg", "fg_light",
        "fg", "bg0", "bg1", "bg0", "yellow", "bg0", "bright_blue",
        "bright_purple", "bg2", "bg0", "bg1", "fg_dim", "fg_dim", "yellow"
    ],
    "disabled": [
        "fg_dim", "bg1", "bg3", "bg2", "bg0", "bg2", "bg4", "fg_light",
        "bg4", "bg0", "bg1", "bg0", "bg2", "fg_dim", "blue",
        "purple", "bg2", "bg0", "bg1", "fg_dim", "bg3", "bg2"
    ],
    "inactive": [
        "fg", "bg1", "bg3", "bg2", "bg0", "bg2", "fg", "fg_light",
        "fg", "bg0", "bg1", "bg0", "bg3", "fg", "bright_blue",
        "bright_purple", "bg2", "bg0", "bg1", "fg_dim", "fg_dim", "bg3"
    ],
}

def build_qt_palette_line(colors, state):
    """Build comma-separated Qt palette string for a given state."""
    return ", ".join(colors[role] for role in QT_PALETTE[state])

# ════════════════════════════════════════════
#  Template manifest
# ════════════════════════════════════════════

# (template_relative_path, output_path_relative_to_BASE)
MANIFEST = [
    ("waybar/style.css.tmpl",                       "waybar/style.css"),
    ("swaync/style.css.tmpl",                       "swaync/style.css"),
    ("wofi/style.css.tmpl",                         "wofi/style.css"),
    ("mozilla/firefox/chrome/userChrome.css.tmpl",  None),  # resolved at runtime
    ("dunst/dunstrc.tmpl",                          "dunst/dunstrc"),
    ("gtk-3.0/settings.ini.tmpl",                   "gtk-3.0/settings.ini"),
    ("gtk-4.0/settings.ini.tmpl",                   "gtk-4.0/settings.ini"),
    ("xsettingsd/xsettingsd.conf.tmpl",             "xsettingsd/xsettingsd.conf"),
    ("micro/colorschemes/gruvbox-dark.micro.tmpl",  "micro/colorschemes/gruvbox-dark.micro"),
    ("alacritty/alacritty.toml.tmpl",               "alacritty/alacritty.toml"),
    ("starship.toml.tmpl",                          "starship.toml"),
    ("kitty/kitty.conf.tmpl",                       "kitty/kitty.conf"),
    ("rofi/config.rasi.tmpl",                       "rofi/config.rasi"),
    ("rofi/theme.rasi.tmpl",                        "rofi/gruvbox-dark.rasi"),
    ("hypr/theme.conf.tmpl",                        "hypr/theme.conf"),
    ("hypr/hyprlock.conf.tmpl",                     "hypr/hyprlock.conf"),
    ("fastfetch/config.jsonc.tmpl",                 "fastfetch/config.jsonc"),
    ("Trolltech.conf.tmpl",                         "Trolltech.conf"),
    ("btop/themes/generated.theme.tmpl",            "btop/themes/generated.theme"),
    ("swayosd/style.css.tmpl",                      "swayosd/style.css"),
    ("fontconfig/fonts.conf.tmpl",                  "fontconfig/fonts.conf"),
]

# Paths to ensure executable after render
EXECUTABLE_OUTPUTS = set()

SDDM_TEMPLATE = "sddm/Main.qml.tmpl"
SDDM_OUTPUT = Path("/usr/share/sddm/themes/gruvbox-dark/Main.qml")

# ════════════════════════════════════════════
#  Core logic
# ════════════════════════════════════════════

def load_theme():
    with open(THEME_FILE, "rb") as f:
        return tomllib.load(f)

def build_context(theme):
    """Flatten theme into a single dict for Jinja2 rendering."""
    ctx = {}
    # Flatten all sections
    for section, data in theme.items():
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict):
                    for k2, v2 in val.items():
                        ctx[f"{section}_{key}_{k2}"] = v2
                else:
                    ctx[f"{section}_{key}"] = val
        else:
            ctx[section] = data

    # Keep nested access too
    ctx["colors"] = theme["colors"]
    ctx["fonts"] = theme["fonts"]
    ctx["cursor"] = theme["cursor"]
    ctx["icons"] = theme["icons"]
    # Expand ~ in wallpaper path
    wp = dict(theme["wallpaper"])
    wp["path"] = os.path.expanduser(wp["path"])
    ctx["wallpaper"] = wp
    # Expand ~ in fastfetch logo source (if section present)
    if "fastfetch" in theme:
        ff = dict(theme["fastfetch"])
        ff["logo_source"] = os.path.expanduser(ff["logo_source"])
        ctx["fastfetch"] = ff
    ctx["geometry"] = theme["geometry"]
    ctx["opacity"] = theme["opacity"]
    ctx["blur"] = theme["blur"]
    ctx["shadow"] = theme["shadow"]
    ctx["animations"] = theme["animations"]
    ctx["border"] = theme["border"]
    ctx["meta"] = theme["meta"]
    # Optional nested sections — pass through verbatim if present
    for opt in ("waybar", "layer_effects", "swayosd"):
        if opt in theme:
            ctx[opt] = theme[opt]

    # Qt palette lines
    c = theme["colors"]
    ctx["qt_palette_active"] = build_qt_palette_line(c, "active")
    ctx["qt_palette_disabled"] = build_qt_palette_line(c, "disabled")
    ctx["qt_palette_inactive"] = build_qt_palette_line(c, "inactive")

    # Border gradient for Hyprland
    border_colors = []
    for name in theme["border"]["active_colors"]:
        border_colors.append(c[name])
    ctx["border_gradient_colors"] = border_colors

    # Resolve accent references to hex values
    if "accents" in theme:
        resolved = {}
        for group, mappings in theme["accents"].items():
            resolved[group] = {}
            for key, color_name in mappings.items():
                resolved[group][key] = c.get(color_name, color_name)
        ctx["accents"] = resolved

    return ctx

def create_jinja_env():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["bare"] = f_bare
    env.filters["rgba"] = f_rgba
    env.filters["ansi"] = f_ansi
    env.filters["qt_rgb"] = f_qt_rgb
    env.filters["rgb_vals"] = f_rgb_vals
    return env

def resolve_firefox_output():
    """Find Firefox profile chrome dir."""
    matches = list(globmod.glob(str(BASE / "mozilla/firefox/*/chrome")))
    return [Path(m) / "userChrome.css" for m in matches]

def render_templates(env, ctx, only=None, dry_run=False, show_diff=False):
    results = []
    for tmpl_path, out_rel in MANIFEST:
        name = tmpl_path.split("/")[0] if "/" in tmpl_path else tmpl_path.split(".")[0]
        if only and name.lower() != only.lower():
            continue

        template = env.get_template(tmpl_path)
        rendered = template.render(**ctx)

        # Resolve output paths
        if out_rel is None:
            outputs = resolve_firefox_output()
        else:
            outputs = [BASE / out_rel]

        make_exec = out_rel in EXECUTABLE_OUTPUTS
        for out_path in outputs:
            results.append(_handle_output(out_path, rendered, dry_run, show_diff, executable=make_exec))

    return results

def _handle_output(out_path, rendered, dry_run, show_diff, executable=False):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    current = out_path.read_text() if out_path.exists() else ""
    changed = current != rendered

    if show_diff and changed:
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(out_path),
            tofile=str(out_path) + " (generated)",
        )
        sys.stdout.writelines(diff)

    if dry_run:
        status = "WOULD CHANGE" if changed else "unchanged"
        print(f"  {status}: {out_path}")
    else:
        if changed:
            out_path.write_text(rendered)
            print(f"  updated: {out_path}")
        else:
            print(f"  unchanged: {out_path}")
        if executable and out_path.exists():
            mode = out_path.stat().st_mode
            out_path.chmod(mode | 0o111)

    return {"path": out_path, "changed": changed}

# ════════════════════════════════════════════
#  JSON patching
# ════════════════════════════════════════════

def strip_jsonc_comments(text):
    """Strip // comments from JSONC (line comments only)."""
    lines = []
    for line in text.splitlines():
        # Don't strip inside strings - simple heuristic: only strip if // is not inside quotes
        stripped = re.sub(r'(?<!["\'])//.*$', '', line)
        lines.append(stripped)
    return "\n".join(lines)

def patch_json_files(ctx, dry_run=False, show_diff=False):
    c = ctx["colors"]
    fonts = ctx["fonts"]
    geom = ctx["geometry"]
    opacity = ctx["opacity"]
    anim = ctx["animations"]

    # VSCodium
    vsc_path = BASE / "VSCodium/User/settings.json"
    if vsc_path.exists():
        vsc = json.loads(vsc_path.read_text())
        vsc["terminal.integrated.fontFamily"] = fonts["mono"]
        vsc["terminal.integrated.fontSize"] = fonts["size"]["terminal"]
        vsc["terminal.integrated.cursorWidth"] = 2
        vsc["workbench.colorCustomizations"] = {
            "terminal.foreground": c["fg"],
            "terminal.background": c["bg0"],
            "terminal.ansiBlack": c["bg1"],
            "terminal.ansiBrightBlack": c["gray"],
            "terminal.ansiRed": c["red"],
            "terminal.ansiBrightRed": c["bright_red"],
            "terminal.ansiGreen": c["green"],
            "terminal.ansiBrightGreen": c["bright_green"],
            "terminal.ansiYellow": c["yellow"],
            "terminal.ansiBrightYellow": c["bright_yellow"],
            "terminal.ansiBlue": c["blue"],
            "terminal.ansiBrightBlue": c["bright_blue"],
            "terminal.ansiMagenta": c["purple"],
            "terminal.ansiBrightMagenta": c["bright_purple"],
            "terminal.ansiCyan": c["cyan"],
            "terminal.ansiBrightCyan": c["bright_cyan"],
            "terminal.ansiWhite": c["fg_dim"],
            "terminal.ansiBrightWhite": c["fg"],
            "terminal.selectionBackground": c["fg"],
            "terminal.selectionForeground": c["bg0"],
            "terminalCursor.foreground": c["fg"],
        }
        _write_json(vsc_path, vsc, dry_run, show_diff)

    # Waybar config
    wb_path = BASE / "waybar/config.jsonc"
    if wb_path.exists():
        raw = wb_path.read_text()
        cleaned = strip_jsonc_comments(raw)
        wb = json.loads(cleaned)
        wb["height"] = geom["bar_height"]
        wb["margin-top"] = geom["bar_margin_top"]
        wb["margin-left"] = geom["bar_margin_side"]
        wb["margin-right"] = geom["bar_margin_side"]
        _write_json(wb_path, wb, dry_run, show_diff)

    # Swaync config
    sn_path = BASE / "swaync/config.json"
    if sn_path.exists():
        sn = json.loads(sn_path.read_text())
        sn["control-center-width"] = geom["swaync_cc_width"]
        sn["notification-window-width"] = geom["swaync_notif_width"]
        sn["transition-time"] = anim["swaync_transition"]
        _write_json(sn_path, sn, dry_run, show_diff)

def _write_json(path, data, dry_run, show_diff):
    rendered = json.dumps(data, indent=4, ensure_ascii=False) + "\n"
    current = path.read_text() if path.exists() else ""
    changed = current != rendered

    if show_diff and changed:
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path) + " (patched)",
        )
        sys.stdout.writelines(diff)

    if dry_run:
        status = "WOULD CHANGE" if changed else "unchanged"
        print(f"  {status}: {path}")
    elif changed:
        path.write_text(rendered)
        print(f"  patched: {path}")
    else:
        print(f"  unchanged: {path}")

# ════════════════════════════════════════════
#  btop handling
# ════════════════════════════════════════════

def handle_btop(dry_run=False):
    conf = BASE / "btop/btop.conf"
    if not conf.exists():
        return
    text = conf.read_text()
    new_text = re.sub(
        r'^color_theme\s*=\s*".*"',
        'color_theme = "generated"',
        text,
        flags=re.MULTILINE,
    )
    if text != new_text:
        if dry_run:
            print(f"  WOULD CHANGE: {conf}")
        else:
            conf.write_text(new_text)
            print(f"  patched: {conf} (color_theme -> generated)")

# ════════════════════════════════════════════
#  SDDM handling
# ════════════════════════════════════════════

def handle_sddm(env, ctx, dry_run=False, show_diff=False):
    tmpl = env.get_template(SDDM_TEMPLATE)
    rendered = tmpl.render(**ctx)

    if dry_run:
        print(f"  WOULD CHANGE (sudo): {SDDM_OUTPUT}")
        return

    # Write to temp then sudo cp
    tmp = TEMPLATE_DIR / "sddm" / ".generated-Main.qml"
    tmp.write_text(rendered)
    print(f"  generated: {tmp}")

    try:
        subprocess.run(
            ["sudo", "cp", str(tmp), str(SDDM_OUTPUT)],
            check=True,
        )
        print(f"  installed (sudo): {SDDM_OUTPUT}")
    except subprocess.CalledProcessError:
        print(f"  ERROR: failed to copy to {SDDM_OUTPUT}", file=sys.stderr)

# ════════════════════════════════════════════
#  Verification
# ════════════════════════════════════════════

def verify(results):
    errors = 0
    for r in results:
        path = r["path"]
        if not path.exists():
            continue
        content = path.read_text()
        # Check for unrendered Jinja2 tags
        if "{{" in content or "{%" in content:
            # Exclude legitimate uses (e.g., waybar playerctl format)
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                if ("{{" in line or "{%" in line) and "playerctl" not in line and "exec" not in line:
                    print(f"  WARNING: possible unrendered template tag in {path}:{i}")
                    errors += 1

        # Validate JSON (skip .jsonc which may have URLs with //)
        if path.suffix == ".json":
            try:
                cleaned = strip_jsonc_comments(content)
                json.loads(cleaned)
            except json.JSONDecodeError as e:
                print(f"  ERROR: invalid JSON in {path}: {e}")
                errors += 1

    if errors:
        print(f"\n  {errors} verification issue(s) found")
    else:
        print("\n  All files verified OK")

# ════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate themed configs from theme.toml")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument("--diff", action="store_true", help="Show diffs")
    parser.add_argument("--only", type=str, help="Generate only one target (e.g., kitty)")
    parser.add_argument("--sddm", action="store_true", help="Also generate SDDM (needs sudo)")
    parser.add_argument("--verify", action="store_true", help="Run verification checks")
    args = parser.parse_args()

    print(f"Loading {THEME_FILE}...")
    theme = load_theme()
    ctx = build_context(theme)
    env = create_jinja_env()

    print(f"\nRendering templates...")
    results = render_templates(env, ctx, only=args.only, dry_run=args.dry_run, show_diff=args.diff)

    if not args.only:
        print(f"\nPatching JSON files...")
        patch_json_files(ctx, dry_run=args.dry_run, show_diff=args.diff)

        print(f"\nUpdating btop config...")
        handle_btop(dry_run=args.dry_run)

    if args.sddm:
        print(f"\nGenerating SDDM theme...")
        handle_sddm(env, ctx, dry_run=args.dry_run, show_diff=args.diff)

    if args.verify:
        print(f"\nVerifying...")
        verify(results)

    print("\nDone.")

if __name__ == "__main__":
    main()
