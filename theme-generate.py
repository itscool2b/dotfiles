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

def f_rgb_float(h):
    """#1d2021 -> 0.114, 0.125, 0.129  (Plymouth script format)"""
    r, g, b = hex_to_rgb(h)
    return f"{r/255:.3f}, {g/255:.3f}, {b/255:.3f}"

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
    ("hypr/plugins.conf.tmpl",                      "hypr/plugins.conf"),
    ("fastfetch/config.jsonc.tmpl",                 "fastfetch/config.jsonc"),
    ("Trolltech.conf.tmpl",                         "Trolltech.conf"),
    ("btop/themes/generated.theme.tmpl",            "btop/themes/generated.theme"),
    ("swayosd/style.css.tmpl",                      "swayosd/style.css"),
    ("fontconfig/fonts.conf.tmpl",                  "fontconfig/fonts.conf"),
    ("kdeglobals.tmpl",                             "kdeglobals"),
    ("cava/config.tmpl",                            "cava/config"),
    ("swappy/config.tmpl",                          "swappy/config"),
    ("waybar/scripts/cava-waybar.sh.tmpl",          "waybar/scripts/cava-waybar.sh"),
]

# Paths to ensure executable after render
EXECUTABLE_OUTPUTS = {"waybar/scripts/cava-waybar.sh"}

SDDM_TEMPLATE = "sddm/Main.qml.tmpl"
SDDM_OUTPUT = Path("/usr/share/sddm/themes/gruvbox-dark/Main.qml")

# Plymouth: multi-file theme deployed to /usr/share/plymouth/themes/<name>/
# Template → output-suffix pairs (output filename = <theme_name><suffix>).
PLYMOUTH_FILES = [
    ("plymouth/theme.plymouth.tmpl", ".plymouth"),
    ("plymouth/theme.script.tmpl",   ".script"),
]
PLYMOUTH_BASE_OUTPUT_DIR = Path("/usr/share/plymouth/themes")

PRESETS_DIR = BASE / "theme-presets"

# ════════════════════════════════════════════
#  Core logic
# ════════════════════════════════════════════

def deep_merge(base, overlay):
    """Recursively merge overlay dict into base dict (overlay wins on conflicts)."""
    result = dict(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_theme(preset=None):
    with open(THEME_FILE, "rb") as f:
        theme = tomllib.load(f)
    if preset:
        preset_path = PRESETS_DIR / f"{preset}.toml"
        if not preset_path.exists():
            print(f"  ERROR: preset not found: {preset_path}", file=sys.stderr)
            sys.exit(1)
        with open(preset_path, "rb") as f:
            overlay = tomllib.load(f)
        theme = deep_merge(theme, overlay)
        print(f"  applied preset: {preset}")
    return theme

def write_preset(name, preset_dict):
    """Write a preset dict to disk as a TOML file (hand-rolled for simplicity)."""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    out = PRESETS_DIR / f"{name}.toml"
    lines = ["# Auto-generated preset\n"]
    for section, values in preset_dict.items():
        lines.append(f"\n[{section}]\n")
        for k, v in values.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"\n')
            elif isinstance(v, bool):
                lines.append(f'{k} = {str(v).lower()}\n')
            elif isinstance(v, list):
                items = ", ".join(f'"{x}"' if isinstance(x, str) else str(x) for x in v)
                lines.append(f'{k} = [{items}]\n')
            else:
                lines.append(f'{k} = {v}\n')
    out.write_text("".join(lines))
    print(f"  wrote preset: {out}")
    return out

def derive_preset_from_wallpaper(wallpaper_path, preset_name="wallpaper-derived"):
    """Run matugen on a wallpaper, map its Material You roles to theme.toml keys."""
    wp = Path(wallpaper_path).expanduser()
    if not wp.exists():
        print(f"  ERROR: wallpaper not found: {wp}", file=sys.stderr)
        sys.exit(1)
    try:
        result = subprocess.run(
            ["matugen", "image", str(wp), "--json", "hex"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        print("  ERROR: matugen not installed. Run: paru -S matugen-bin", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: matugen failed: {e.stderr}", file=sys.stderr)
        sys.exit(1)

    # matugen v2 JSON shape: {"colors": {"dark": {...}, "light": {...}}, ...}
    # We always derive from dark scheme — rice is dark.
    data = json.loads(result.stdout)
    dark = data.get("colors", {}).get("dark", {})
    if not dark:
        print("  ERROR: matugen output missing colors.dark", file=sys.stderr)
        sys.exit(1)

    # Material You role → theme.toml colors key.
    # Kept conservative: map core palette, let the user hand-tune from the written preset.
    mapping = {
        "primary":                   "bright_blue",
        "secondary":                 "bright_cyan",
        "tertiary":                  "bright_purple",
        "surface":                   "bg0",
        "surface_container_low":     "bg1",
        "surface_container":         "bg2",
        "surface_container_high":    "bg3",
        "surface_container_highest": "bg4",
        "on_surface":                "fg",
        "on_surface_variant":        "fg_dim",
        "inverse_on_surface":        "fg_light",
    }
    colors = {}
    for matugen_key, theme_key in mapping.items():
        if matugen_key in dark:
            colors[theme_key] = dark[matugen_key]

    write_preset(preset_name, {"colors": colors})
    return preset_name

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
    for opt in ("waybar", "layer_effects", "swayosd", "motion",
                "glass", "plugins", "cava", "plymouth", "swappy"):
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

    # Resolve accent references to hex values.
    # Supports both scalar (single color name) and list (gradient) values.
    if "accents" in theme:
        resolved = {}
        for group, mappings in theme["accents"].items():
            resolved[group] = {}
            for key, val in mappings.items():
                if isinstance(val, list):
                    resolved[group][key] = [c.get(x, x) for x in val]
                else:
                    resolved[group][key] = c.get(val, val)
        ctx["accents"] = resolved

    # Font display fallback — templates can use fonts.mono_display safely even
    # if the user hasn't set it yet; fall back to fonts.mono.
    if "fonts" in ctx and "mono_display" not in ctx["fonts"]:
        ctx["fonts"]["mono_display"] = ctx["fonts"]["mono"]

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
    env.filters["rgb_float"] = f_rgb_float
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

        # Cava module injection (only if [cava] section is configured)
        if "cava" in ctx:
            modules_right = wb.get("modules-right", [])
            if "custom/cava" not in modules_right:
                # Insert before custom/media if present, else at start
                try:
                    idx = modules_right.index("custom/media")
                except ValueError:
                    idx = 0
                modules_right.insert(idx, "custom/cava")
                wb["modules-right"] = modules_right

            wb["custom/cava"] = {
                "exec": "~/.config/waybar/scripts/cava-waybar.sh",
                "format": "{}",
                "tooltip": False,
                "return-type": "",
            }

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
            ["sudo", "mkdir", "-p", str(SDDM_OUTPUT.parent)],
            check=True,
        )
        subprocess.run(
            ["sudo", "cp", str(tmp), str(SDDM_OUTPUT)],
            check=True,
        )
        print(f"  installed (sudo): {SDDM_OUTPUT}")
    except subprocess.CalledProcessError:
        print(f"  ERROR: failed to copy to {SDDM_OUTPUT}", file=sys.stderr)

# ════════════════════════════════════════════
#  Plymouth handling
# ════════════════════════════════════════════

def handle_plymouth(env, ctx, dry_run=False, show_diff=False):
    """Render Plymouth theme files and sudo-install to /usr/share/plymouth/themes/<name>/.
    Does NOT run plymouth-set-default-theme or mkinitcpio — user runs those once manually."""
    theme_name = ctx["plymouth"]["theme_name"]
    output_dir = PLYMOUTH_BASE_OUTPUT_DIR / theme_name

    tmp_dir = TEMPLATE_DIR / "plymouth" / ".generated"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    rendered_files = []
    for tmpl_path, suffix in PLYMOUTH_FILES:
        tmpl = env.get_template(tmpl_path)
        rendered = tmpl.render(**ctx)
        out_name = f"{theme_name}{suffix}"
        tmp_file = tmp_dir / out_name
        dst = output_dir / out_name
        if dry_run:
            print(f"  WOULD CHANGE (sudo): {dst}")
        else:
            tmp_file.write_text(rendered)
            rendered_files.append((tmp_file, dst))
            print(f"  generated: {tmp_file}")

    if dry_run or not rendered_files:
        return

    try:
        subprocess.run(["sudo", "mkdir", "-p", str(output_dir)], check=True)
        for src, dst in rendered_files:
            subprocess.run(["sudo", "cp", str(src), str(dst)], check=True)
            print(f"  installed (sudo): {dst}")
        print(f"\n  Plymouth installed. To activate:")
        print(f"    sudo plymouth-set-default-theme -R {theme_name}")
    except subprocess.CalledProcessError:
        print(f"  ERROR: failed to install Plymouth theme", file=sys.stderr)

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
    parser.add_argument("--plymouth", action="store_true", help="Also generate Plymouth (needs sudo)")
    parser.add_argument("--verify", action="store_true", help="Run verification checks")
    parser.add_argument("--preset", type=str, help="Overlay a preset from theme-presets/<name>.toml")
    parser.add_argument("--from-wallpaper", type=str, metavar="PATH",
                        help="Derive a preset from a wallpaper via matugen, then apply it")
    args = parser.parse_args()

    # Wallpaper → preset derivation. Writes the preset and re-routes to --preset.
    preset = args.preset
    if args.from_wallpaper:
        preset = derive_preset_from_wallpaper(args.from_wallpaper)

    print(f"Loading {THEME_FILE}...")
    theme = load_theme(preset=preset)
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

    if args.plymouth:
        print(f"\nGenerating Plymouth theme...")
        handle_plymouth(env, ctx, dry_run=args.dry_run, show_diff=args.diff)

    if args.verify:
        print(f"\nVerifying...")
        verify(results)

    print("\nDone.")

if __name__ == "__main__":
    main()
