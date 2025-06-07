#!/usr/bin/env bash

# To kill all the "local-chromium/588429/chrome-linux/chrome" threads which
# have been running since more than 300 seconds:
#   ./contify/website_tracking/kill_linux_chrome_thread.sh
#
# T0 kill all the "local-chromium/588429/chrome-linux/chrome" thread which
# have been running since 90 seconds:
#   ./contify/website_tracking/kill_linux_chrome_thread.sh 90
#
# Default time delta is 300 seconds, and it accepts the first argument as a
# custom time delta
#



if [[ -z "$1" ]]; then
  R_TIME=300  # 300 seconds
else
  R_TIME=$1
fi

L_CHROME_PIDS=$(pgrep -f "local-chromium/588429/chrome-linux/chrome")

if [[ -n "${L_CHROME_PIDS}" ]]; then
  F_PIDS=$(ps -o pid,etimes -p $L_CHROME_PIDS | awk '{if ($1 != "PID" && $2 > '${R_TIME}') { print $1 ; }}')

  if [[ -n "${F_PIDS}" ]]; then
    echo "The process ids to kill are:"
    echo "${F_PIDS}"
    echo
    kill -9 $F_PIDS
  else
    echo "There are no such processess to kill, which are running since more tham ${R_TIME} seconds"
  fi

else
  echo "No Process IDs found"
fi
