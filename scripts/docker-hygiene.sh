#!/bin/sh
# Image/disk hygiene (PLAN-macos-speed.md §6.4): Docker Desktop degrades under disk
# pressure, and sandbox images + build cache grow without bound. Run weekly, or wire
# into CI. Fails (exit 1) when reclaimable space crosses the threshold so a cron/CI
# job can alert. `make hygiene` runs the safe parts; --prune also reclaims.
set -e

THRESHOLD_GB="${ZOO_HYGIENE_THRESHOLD_GB:-5}"

df_line=$(docker system df --format '{{.Type}} {{.Reclaimable}}' 2>/dev/null)
echo "$df_line"

images_gb=$(docker system df --format '{{.Type}} {{.Size}} {{.Reclaimable}}' | awk '$1 == "Images" {print $3}' | sed 's/GB//;s/B//')
cache_gb=$(docker system df --format '{{.Type}} {{.Size}} {{.Reclaimable}}' | awk '$1 == "BuildCache" {print $3}' | sed 's/GB//;s/B//')

fail=0
if [ "${images_gb%%.*}" -ge "$THRESHOLD_GB" ] 2>/dev/null; then
  echo "ALERT: images reclaimable ~${images_gb}GB (>= ${THRESHOLD_GB}GB) — delete superseded version tags"
  fail=1
fi
if [ "${cache_gb%%.*}" -ge "$THRESHOLD_GB" ] 2>/dev/null; then
  echo "ALERT: build cache reclaimable ~${cache_gb}GB (>= ${THRESHOLD_GB}GB) — run: docker builder prune -f"
  fail=1
fi

# duplicate version tags of the same image (keep latest + newest version only)
docker images --format '{{.Repository}} {{.Tag}}' | grep zoo- | awk '{print $1}' | sort | uniq -d | while read -r repo; do
  count=$(docker images --format '{{.Tag}}' "$repo" | grep -vc latest)
  if [ "$count" -gt 1 ]; then
    echo "NOTE: $repo has $count non-latest tags — old version tags are pure noise, delete them"
  fi
done

if [ "$1" = "--prune" ]; then
  docker builder prune -f | tail -1
  docker image prune -f | tail -1
fi

exit $fail
