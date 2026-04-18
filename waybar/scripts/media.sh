#!/usr/bin/env bash
# Emit JSON for waybar custom/media module
# Fields: text (artist — title), alt (player status), percentage (progress 0-100), class (status)
# Dependencies: playerctl, jq

status=$(playerctl status 2>/dev/null)
if [[ -z "$status" ]]; then
    printf '%s\n' '{"text":"","alt":"none","percentage":0,"class":"none"}'
    exit 0
fi

artist=$(playerctl metadata artist 2>/dev/null || echo "")
title=$(playerctl metadata title 2>/dev/null || echo "")
position=$(playerctl position 2>/dev/null || echo "0")
length_us=$(playerctl metadata mpris:length 2>/dev/null || echo "0")

if [[ -z "$title" && -z "$artist" ]]; then
    printf '%s\n' '{"text":"","alt":"none","percentage":0,"class":"none"}'
    exit 0
fi

if [[ -n "$artist" ]]; then
    text="${artist} — ${title}"
else
    text="$title"
fi

# Trim overly long text
if (( ${#text} > 45 )); then
    text="${text:0:42}…"
fi

# Progress percentage (position in s, length in µs)
if [[ -n "$length_us" && "$length_us" != "0" ]]; then
    pct=$(awk -v p="$position" -v l="$length_us" 'BEGIN{if(l>0){v=(p*1000000/l)*100; if(v<0)v=0; if(v>100)v=100; printf "%d",v}else{print 0}}')
else
    pct=0
fi

# Lowercase status for class
class=$(printf '%s' "$status" | tr '[:upper:]' '[:lower:]')

jq -cn \
    --arg text "$text" \
    --arg alt "$class" \
    --arg class "$class" \
    --argjson pct "$pct" \
    '{text:$text, alt:$alt, percentage:$pct, class:$class}'
