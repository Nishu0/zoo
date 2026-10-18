#!/bin/sh
set -eu
# Let the API restore profiles before Firefox opens them. Standalone images start immediately.
if [ "${ZOO_BROWSER_WAIT:-0}" = 1 ]; then
    until [ -f /tmp/zoo-browser-start ]; do sleep 0.1; done
fi
# Xvfb and the window manager start concurrently with the browser.
until DISPLAY=:1 xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q 'window id'; do
    sleep 0.1
done
exec firefox-esr "${ZOO_BROWSER_HOME:-https://duckduckgo.com}"
