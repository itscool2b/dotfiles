#!/usr/bin/env bash
# Hides waybar until cursor enters the top edge of the screen.
# OLED protection: keeps the panel pixels off-screen during normal use.
#
# Mechanism: kill/respawn waybar. SIGUSR1 was unreliable on waybar 0.15.0 here —
# the signal delivered but the layer stayed full-size.

REVEAL_Y=5      # cursor at logical y <= this → show
HIDE_Y=60       # cursor at logical y >= this → hide (after delay)
POLL_MS=80
HIDE_DELAY_MS=400
STARTUP_GRACE=0.6   # let waybar finish first launch before we touch it

# single instance — kill any prior copy
pgrep -f -x "bash $0" | grep -vx "$$" | xargs -r kill 2>/dev/null

show_bar() {
    pgrep -x waybar >/dev/null || (setsid -f waybar >/dev/null 2>&1)
}

hide_bar() {
    pkill -x waybar
}

until pgrep -x waybar >/dev/null; do sleep 0.2; done
sleep "$STARTUP_GRACE"
hide_bar
visible=0
hide_at=0

while :; do
    y=$(hyprctl cursorpos -j 2>/dev/null | jq -r '.y // empty')
    [ -z "$y" ] && { sleep "0.$POLL_MS"; continue; }

    now=$(date +%s%3N)

    if [ "$y" -le "$REVEAL_Y" ]; then
        if [ "$visible" -eq 0 ]; then
            show_bar
            visible=1
        fi
        hide_at=0
    elif [ "$y" -ge "$HIDE_Y" ] && [ "$visible" -eq 1 ]; then
        if [ "$hide_at" -eq 0 ]; then
            hide_at=$((now + HIDE_DELAY_MS))
        elif [ "$now" -ge "$hide_at" ]; then
            hide_bar
            visible=0
            hide_at=0
        fi
    else
        hide_at=0
    fi

    sleep "0.$POLL_MS"
done
