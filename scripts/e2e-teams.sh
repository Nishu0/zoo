#!/usr/bin/env bash
# ok/bad pairs: bad runs whenever the check fails, which is the intent
# shellcheck disable=SC2015
set -uo pipefail
U=${URL:-http://127.0.0.1:8077}
ok() { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; FAILED=1; }
FAILED=0
code() { curl -s -o /tmp/zoo-body.json -w '%{http_code}' "$@"; }
expect() { local want=$1 what=$2; shift 2; local got; got=$(code "$@"); [ "$got" = "$want" ] && ok "$what ($got)" || bad "$what: wanted $want, got $got $(head -c 200 /tmp/zoo-body.json)"; }
signup() { curl -fsS -X POST "$U/auth/signup" -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"password123\",\"name\":\"$2\"}" | jq -r .access_token; }
A=$(signup alice@example.com Alice); B=$(signup bob@example.com Bob); ADM=$(signup admin@example.com Admin)
HA=(-H "Authorization: Bearer $A" -H 'Content-Type: application/json')
HB=(-H "Authorization: Bearer $B" -H 'Content-Type: application/json')

echo "== workspaces and invitations"
W=$(curl -fsS -X POST "$U/workspaces" "${HA[@]}" -d '{"name":"Team Zoo"}' | jq -r .id)
[ -n "$W" ] && ok "alice made workspace $W" || bad "create workspace"
HAW=("${HA[@]}" -H "X-Zoo-Workspace: $W"); HBW=("${HB[@]}" -H "X-Zoo-Workspace: $W")
expect 404 "bob, not a member, can't act in it" "$U/sandboxes" "${HBW[@]}"
TOKEN=$(curl -fsS -X POST "$U/workspaces/$W/invitations" "${HA[@]}" -d '{"email":"bob@example.com","role":"viewer"}' | jq -r .token)
expect 403 "alice can't accept bob's invitation" -X POST "$U/invitations/$TOKEN/accept" "${HA[@]}"
expect 200 "bob accepts as viewer" -X POST "$U/invitations/$TOKEN/accept" "${HB[@]}"
expect 404 "the invitation works once" -X POST "$U/invitations/$TOKEN/accept" "${HB[@]}"
[ "$(curl -fsS "$U/workspaces/$W/members" "${HB[@]}" | jq -r '[.[].role]|sort|join(",")')" = "owner,viewer" ] && ok "members: owner, viewer" || bad "members list"

echo "== roles"
expect 200 "viewer can list sandboxes" "$U/sandboxes" "${HBW[@]}"
expect 403 "viewer can't create a sandbox" -X POST "$U/sandboxes" "${HBW[@]}" -d '{"kind":"code"}'
expect 403 "viewer can't add a vault secret" -X POST "$U/vault/secrets" "${HBW[@]}" -d '{"name":"X","value":"y"}'
expect 200 "alice promotes bob to member" -X PATCH "$U/workspaces/$W/members/$(curl -fsS "$U/auth/me" "${HB[@]}" | jq -r .id)" "${HA[@]}" -d '{"role":"member"}'
expect 403 "member can't add a vault secret (admin)" -X POST "$U/vault/secrets" "${HBW[@]}" -d '{"name":"X","value":"y"}'
expect 201 "alice (owner) adds a shared secret" -X POST "$U/vault/secrets" "${HAW[@]}" -d '{"name":"SHARED","value":"s3cret"}'
[ "$(curl -fsS "$U/vault/secrets" "${HBW[@]}" | jq -r '.[].name')" = "SHARED" ] && ok "bob sees the workspace's secret" || bad "shared secret not visible"
[ "$(curl -fsS "$U/vault/secrets" "${HB[@]}" | jq 'length')" = "0" ] && ok "bob's personal workspace stays separate" || bad "personal workspace leak"
expect 409 "the last owner can't step down" -X PATCH "$U/workspaces/$W/members/$(curl -fsS "$U/auth/me" "${HA[@]}" | jq -r .id)" "${HA[@]}" -d '{"role":"admin"}'

echo "== scoped API keys"
RO=$(curl -fsS -X POST "$U/api-keys" "${HAW[@]}" -d '{"name":"ro","read_only":true}' | jq -r .key)
expect 200 "read-only key lists sandboxes" "$U/sandboxes" -H "Authorization: Bearer $RO"
expect 403 "read-only key can't create" -X POST "$U/sandboxes" -H "Authorization: Bearer $RO" -H 'Content-Type: application/json' -d '{"kind":"code"}'
expect 403 "a key can't switch workspaces" "$U/sandboxes" -H "Authorization: Bearer $RO" -H "X-Zoo-Workspace: nope"
OLD=$(curl -fsS -X POST "$U/api-keys" "${HAW[@]}" -d '{"name":"old","expires_at":"2020-01-01T00:00:00Z"}' -o /dev/null -w '%{http_code}')
[ "$OLD" = 422 ] && ok "an expiry in the past is refused" || bad "past expiry: $OLD"
expect 403 "member can't make keys (admin)" -X POST "$U/api-keys" "${HBW[@]}" -d '{"name":"x"}'

echo "== quota (admin sets, defaults from ZOO_DEFAULT_QUOTA_*)"
[ "$(curl -fsS "$U/workspaces/$W/quota" "${HB[@]}" | jq -r .max_running_sandboxes)" = "2" ] && ok "default quota of 2 running sandboxes" || bad "default quota"
expect 403 "a workspace owner can't raise its own quota" -X PUT "$U/workspaces/$W/quota" "${HA[@]}" -d '{"max_running_sandboxes":99}'
expect 200 "Zoo's admin sets it" -X PUT "$U/workspaces/$W/quota" -H "Authorization: Bearer $ADM" -H 'Content-Type: application/json' -d '{"max_running_sandboxes":1,"max_cpus":4}'

echo "== auto-stop defaults"
expect 200 "admin sets workspace auto-stop" -X PUT "$U/workspaces/$W/lifecycle" "${HA[@]}" -d '{"default_idle_timeout_minutes":30,"default_max_lifetime_minutes":480}'
expect 403 "member can't" -X PUT "$U/workspaces/$W/lifecycle" "${HB[@]}" -d '{"default_idle_timeout_minutes":1}'

echo "== audit log"
LOG=$(curl -fsS "$U/audit-logs" "${HAW[@]}")
echo "$LOG" | jq -e '[.entries[].action] | index("POST /workspaces/{workspace_id}/invitations") != null' >/dev/null && ok "invitation recorded" || bad "invitation not in audit log"
echo "$LOG" | jq -e '[.entries[].action] | index("PATCH /workspaces/{workspace_id}/members/{user_id}") != null' >/dev/null && ok "role change recorded" || bad "role change not in audit log"
echo "$LOG" | jq -e '[.entries[].action] | index("secret.create") != null' >/dev/null && ok "secret creation recorded (its own entry)" || bad "secret.create missing"
expect 403 "member can't read the audit log" "$U/audit-logs" "${HBW[@]}"
F=$(curl -fsS "$U/audit-logs?resource_type=api-keys" "${HAW[@]}" | jq -r '[.entries[].resource_type]|unique|join(",")')
[ "$F" = "api-keys" ] && ok "filtered by resource type" || bad "filter: $F"
CSV=$(curl -fsS "$U/audit-logs/export" "${HAW[@]}")
echo "$CSV" | head -1 | grep -q '^created_at,actor_email' && [ "$(echo "$CSV" | wc -l)" -gt 3 ] && ok "CSV export ($(($(echo "$CSV" | wc -l)-1)) rows)" || bad "csv export"

echo "== removal"
BID=$(curl -fsS "$U/auth/me" "${HB[@]}" | jq -r .id)
expect 204 "alice removes bob" -X DELETE "$U/workspaces/$W/members/$BID" "${HA[@]}"
expect 404 "bob is out" "$U/sandboxes" "${HBW[@]}"
exit $FAILED
