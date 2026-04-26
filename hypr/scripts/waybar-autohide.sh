#!/usr/bin/env bash
# Reveals waybar when cursor enters the top edge, hides it after the cursor leaves.
# OLED protection: keeps a static UI element off the panel during normal use.

REVEAL_Y=5      # cursor at logical y <= this → show
HIDE_Y=60       # cursor at logical y >= this → hide (after delay)
POLL_MS=80
HIDE_DELAY_MS=400

pgrep -f -x "bash $0" | grep -vx "$$" | xargs -r kill 2>/dev/null  # single instance
until pgrep -x waybar >/dev/null; do sleep 0.2; done

visible=1
hide_at=0

while :; do
    y=$(hyprctl cursorpos -j 2>/dev/null | jq -r '.y // empty')
    [ -z "$y" ] && { sleep "0.$POLL_MS"; continue; }

    now=$(date +%s%3N)

    if [ "$y" -le "$REVEAL_Y" ]; then
        if [ "$visible" -eq 0 ]; then
            pkill -SIGUSR1 -x waybar
            visible=1
        fi
        hide_at=0
    elif [ "$y" -ge "$HIDE_Y" ] && [ "$visible" -eq 1 ]; then
        if [ "$hide_at" -eq 0 ]; then
            hide_at=$((now + HIDE_DELAY_MS))
        elif [ "$now" -ge "$hide_at" ]; then
            pkill -SIGUSR1 -x waybar
            visible=0
            hide_at=0
        fi
    else
        hide_at=0
    fi

    sleep "0.$POLL_MS"
done
