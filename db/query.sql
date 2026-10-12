-- name: CreateUser :one
INSERT INTO users (id, email, name, avatar_url, password)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: GetUser :one
SELECT * FROM users WHERE id = $1 LIMIT 1;

-- name: GetUserByEmail :one
SELECT * FROM users WHERE email = $1 LIMIT 1;

-- name: ListUsers :many
SELECT * FROM users ORDER BY created_at DESC;

-- name: UpdateUser :one
UPDATE users
SET name = $1, avatar_url = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3
RETURNING *;

-- name: DeleteUser :exec
DELETE FROM users WHERE id = $1;

-- name: CreateWorkspace :one
INSERT INTO workspaces (id, name, slug, created_by)
VALUES ($1, $2, $3, $4)
RETURNING *;

-- name: GetWorkspace :one
SELECT * FROM workspaces WHERE id = $1 LIMIT 1;

-- name: GetWorkspaceBySlug :one
SELECT * FROM workspaces WHERE slug = $1 LIMIT 1;

-- name: ListWorkspacesByUser :many
SELECT w.*, wm.role
FROM workspaces w
JOIN workspace_members wm ON wm.workspace_id = w.id
WHERE wm.user_id = $1 AND wm.status = 'active'
ORDER BY w.created_at ASC;

-- name: CountWorkspaceOwners :one
SELECT COUNT(*) FROM workspace_members WHERE workspace_id = $1 AND role = 'owner' AND status = 'active';

-- name: CountRunningSandboxesByWorkspace :one
SELECT COUNT(*) FROM sandboxes WHERE workspace_id = $1 AND status IN ('pending', 'provisioning', 'running');

-- name: UpdateWorkspace :one
UPDATE workspaces
SET name = $1, slug = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3
RETURNING *;

-- name: DeleteWorkspace :exec
DELETE FROM workspaces WHERE id = $1;

-- name: AddWorkspaceMember :one
INSERT INTO workspace_members (workspace_id, user_id, role, status, joined_at)
VALUES ($1, $2, $3, 'active', CURRENT_TIMESTAMP)
RETURNING *;

-- name: GetWorkspaceMember :one
SELECT * FROM workspace_members
WHERE workspace_id = $1 AND user_id = $2 LIMIT 1;

-- name: ListWorkspaceMembers :many
SELECT wm.*, u.email, u.name, u.avatar_url
FROM workspace_members wm
JOIN users u ON u.id = wm.user_id
WHERE wm.workspace_id = $1
ORDER BY wm.created_at ASC;

-- name: UpdateWorkspaceMemberRole :one
UPDATE workspace_members
SET role = $1
WHERE workspace_id = $2 AND user_id = $3
RETURNING *;

-- name: UpdateWorkspaceMemberStatus :one
UPDATE workspace_members
SET status = $1
WHERE workspace_id = $2 AND user_id = $3
RETURNING *;

-- name: RemoveWorkspaceMember :exec
DELETE FROM workspace_members
WHERE workspace_id = $1 AND user_id = $2;

-- name: CreateWorkspaceInvitation :one
INSERT INTO workspace_invitations (
    id, workspace_id, email, role, invited_by, token_hash, expires_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING *;

-- name: GetWorkspaceInvitationByToken :one
SELECT * FROM workspace_invitations
WHERE token_hash = $1 AND accepted_at IS NULL AND revoked_at IS NULL
AND expires_at > CURRENT_TIMESTAMP
LIMIT 1;

-- name: ListWorkspaceInvitations :many
SELECT * FROM workspace_invitations
WHERE workspace_id = $1 AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
ORDER BY created_at DESC;

-- name: RevokeWorkspaceInvitation :one
UPDATE workspace_invitations SET revoked_at = CURRENT_TIMESTAMP
WHERE id = $1 AND workspace_id = $2 AND accepted_at IS NULL AND revoked_at IS NULL
RETURNING *;

-- name: AcceptWorkspaceInvitation :one
UPDATE workspace_invitations
SET accepted_at = CURRENT_TIMESTAMP
WHERE id = $1 AND accepted_at IS NULL AND revoked_at IS NULL
AND expires_at > CURRENT_TIMESTAMP
RETURNING *;

-- name: DeleteWorkspaceInvitation :exec
DELETE FROM workspace_invitations WHERE id = $1;

-- name: CreateSandboxImage :one
INSERT INTO sandbox_images (
    id, workspace_id, name, slug, description, is_public, created_by
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING *;

-- name: GetSandboxImage :one
SELECT * FROM sandbox_images WHERE id = $1 LIMIT 1;

-- name: ListSandboxImages :many
SELECT * FROM sandbox_images
WHERE workspace_id = $1
ORDER BY created_at DESC;

-- name: ListPublicSandboxImages :many
SELECT * FROM sandbox_images
WHERE is_public = true
ORDER BY created_at DESC;

-- name: UpdateSandboxImage :one
UPDATE sandbox_images
SET name = $1, slug = $2, description = $3, is_public = $4,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $5
RETURNING *;

-- name: DeleteSandboxImage :exec
DELETE FROM sandbox_images WHERE id = $1;

-- name: CreateSandboxImageVersion :one
INSERT INTO sandbox_image_versions (
    id, image_id, version, image_uri, image_digest,
    build_config, default_resources, created_by
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: GetSandboxImageVersion :one
SELECT * FROM sandbox_image_versions WHERE id = $1 LIMIT 1;

-- name: ListSandboxImageVersions :many
SELECT * FROM sandbox_image_versions
WHERE image_id = $1
ORDER BY created_at DESC;

-- name: GetSandboxImageVersionByTag :one
SELECT * FROM sandbox_image_versions
WHERE image_id = $1 AND version = $2
LIMIT 1;

-- name: DeleteSandboxImageVersion :exec
DELETE FROM sandbox_image_versions WHERE id = $1;

-- name: CreateSandbox :one
INSERT INTO sandboxes (
    id, workspace_id, image_version_id, created_by,
    name, runtime, resources, config
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: GetSandbox :one
SELECT * FROM sandboxes WHERE id = $1 LIMIT 1;

-- name: GetSandboxByRuntimeID :one
SELECT * FROM sandboxes WHERE runtime_id = $1 LIMIT 1;

-- name: ListSandboxesByWorkspace :many
SELECT * FROM sandboxes
WHERE workspace_id = $1 AND deleted_at IS NULL
ORDER BY created_at DESC;

-- name: ListSandboxesByStatus :many
SELECT * FROM sandboxes
WHERE status = $1 AND deleted_at IS NULL
ORDER BY created_at ASC;

-- name: UpdateSandbox :one
UPDATE sandboxes
SET name = $1, resources = $2, config = $3,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $4
RETURNING *;

-- name: UpdateSandboxStatus :one
UPDATE sandboxes
SET status = $1, updated_at = CURRENT_TIMESTAMP
WHERE id = $2
RETURNING *;

-- name: UpdateSandboxRuntime :one
UPDATE sandboxes
SET runtime_id = $1, runtime_host = $2, access_url = $3,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $4
RETURNING *;

-- name: SetSandboxStarted :one
UPDATE sandboxes
SET status = 'running',
    started_at = CURRENT_TIMESTAMP,
    stopped_at = NULL,
    error_message = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
RETURNING *;

-- name: SetSandboxStopped :one
UPDATE sandboxes
SET status = 'stopped',
    stopped_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
RETURNING *;

-- name: SetSandboxFailed :one
UPDATE sandboxes
SET status = 'failed',
    error_message = $1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $2
RETURNING *;

-- name: SoftDeleteSandbox :one
UPDATE sandboxes
SET status = 'deleted',
    deleted_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
RETURNING *;

-- name: DeleteSandbox :exec
DELETE FROM sandboxes WHERE id = $1;

-- name: AddSandboxMember :one
INSERT INTO sandbox_members (sandbox_id, user_id, role, granted_by)
VALUES ($1, $2, $3, $4)
RETURNING *;

-- name: GetSandboxMember :one
SELECT * FROM sandbox_members
WHERE sandbox_id = $1 AND user_id = $2 LIMIT 1;

-- name: ListSandboxMembers :many
SELECT sm.*, u.email, u.name, u.avatar_url
FROM sandbox_members sm
JOIN users u ON u.id = sm.user_id
WHERE sm.sandbox_id = $1
ORDER BY sm.created_at ASC;

-- name: UpdateSandboxMemberRole :one
UPDATE sandbox_members
SET role = $1
WHERE sandbox_id = $2 AND user_id = $3
RETURNING *;

-- name: RemoveSandboxMember :exec
DELETE FROM sandbox_members
WHERE sandbox_id = $1 AND user_id = $2;

-- name: CreateApp :one
INSERT INTO apps (id, workspace_id, name, slug, description, install_config)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING *;

-- name: GetApp :one
SELECT * FROM apps WHERE id = $1 LIMIT 1;

-- name: ListAppsByWorkspace :many
SELECT * FROM apps
WHERE workspace_id = $1 OR workspace_id IS NULL
ORDER BY name ASC;

-- name: UpdateApp :one
UPDATE apps
SET name = $1, slug = $2, description = $3, install_config = $4
WHERE id = $5
RETURNING *;

-- name: DeleteApp :exec
DELETE FROM apps WHERE id = $1;

-- name: GrantSandboxAppPermission :one
INSERT INTO sandbox_app_permissions (
    id, sandbox_id, app_id, action, effect
)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (sandbox_id, app_id, action)
DO UPDATE SET effect = excluded.effect
RETURNING *;

-- name: GetSandboxAppPermission :one
SELECT * FROM sandbox_app_permissions
WHERE sandbox_id = $1 AND app_id = $2 AND action = $3
LIMIT 1;

-- name: ListSandboxAppPermissions :many
SELECT sap.*, a.name AS app_name, a.slug AS app_slug
FROM sandbox_app_permissions sap
JOIN apps a ON a.id = sap.app_id
WHERE sap.sandbox_id = $1
ORDER BY a.name ASC;

-- name: DeleteSandboxAppPermission :exec
DELETE FROM sandbox_app_permissions
WHERE sandbox_id = $1 AND app_id = $2 AND action = $3;

-- name: CreateSandboxNetworkPolicy :one
INSERT INTO sandbox_network_policies (
    id, sandbox_id, default_action, allow_dns
)
VALUES ($1, $2, $3, $4)
RETURNING *;

-- name: GetSandboxNetworkPolicy :one
SELECT * FROM sandbox_network_policies
WHERE sandbox_id = $1 LIMIT 1;

-- name: UpdateSandboxNetworkPolicy :one
UPDATE sandbox_network_policies
SET default_action = $1, allow_dns = $2,
    updated_at = CURRENT_TIMESTAMP
WHERE sandbox_id = $3
RETURNING *;

-- name: UpsertSandboxNetworkPolicy :one
INSERT INTO sandbox_network_policies (
    id, sandbox_id, default_action, allow_dns
)
VALUES ($1, $2, $3, $4)
ON CONFLICT (sandbox_id)
DO UPDATE SET
    default_action = excluded.default_action,
    allow_dns = excluded.allow_dns,
    updated_at = CURRENT_TIMESTAMP
RETURNING *;

-- name: CreateSandboxNetworkRule :one
INSERT INTO sandbox_network_rules (
    id, policy_id, rule_type, value, effect
)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: GetSandboxNetworkRule :one
SELECT * FROM sandbox_network_rules WHERE id = $1 LIMIT 1;

-- name: ListSandboxNetworkRules :many
SELECT * FROM sandbox_network_rules
WHERE policy_id = $1
ORDER BY created_at ASC;

-- name: UpdateSandboxNetworkRule :one
UPDATE sandbox_network_rules
SET rule_type = $1, value = $2, effect = $3
WHERE id = $4
RETURNING *;

-- name: DeleteSandboxNetworkRule :exec
DELETE FROM sandbox_network_rules WHERE id = $1;

-- name: DeleteSandboxNetworkRulesByPolicy :exec
DELETE FROM sandbox_network_rules WHERE policy_id = $1;

-- name: UpsertSandboxPermission :one
INSERT INTO sandbox_permissions (
    id, sandbox_id, permission, action, effect, rules
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (sandbox_id, permission, action)
DO UPDATE SET
    effect = excluded.effect,
    rules = excluded.rules
RETURNING *;

-- name: GetSandboxPermission :one
SELECT * FROM sandbox_permissions
WHERE sandbox_id = $1 AND permission = $2 AND action = $3
LIMIT 1;

-- name: ListSandboxPermissions :many
SELECT * FROM sandbox_permissions
WHERE sandbox_id = $1
ORDER BY permission, action;

-- name: DeleteSandboxPermission :exec
DELETE FROM sandbox_permissions
WHERE sandbox_id = $1 AND permission = $2 AND action = $3;

-- name: CreateSandboxSecret :one
INSERT INTO sandbox_secrets (
    id, sandbox_id, name, secret_ref, injection_config, enabled
)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING *;

-- name: GetSandboxSecret :one
SELECT * FROM sandbox_secrets WHERE id = $1 LIMIT 1;

-- name: GetSandboxSecretByName :one
SELECT * FROM sandbox_secrets
WHERE sandbox_id = $1 AND name = $2 LIMIT 1;

-- name: ListSandboxSecrets :many
SELECT id, sandbox_id, name, injection_config, enabled,
       created_at, updated_at
FROM sandbox_secrets
WHERE sandbox_id = $1
ORDER BY name ASC;

-- name: UpdateSandboxSecret :one
UPDATE sandbox_secrets
SET name = $1, secret_ref = $2, injection_config = $3,
    enabled = $4, updated_at = CURRENT_TIMESTAMP
WHERE id = $5
RETURNING *;

-- name: SetSandboxSecretEnabled :one
UPDATE sandbox_secrets
SET enabled = $1, updated_at = CURRENT_TIMESTAMP
WHERE id = $2
RETURNING *;

-- name: DeleteSandboxSecret :exec
DELETE FROM sandbox_secrets WHERE id = $1;

-- name: CreateAgentSession :one
INSERT INTO agent_sessions (
    id, sandbox_id, created_by, agent_type, config
)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: GetAgentSession :one
SELECT * FROM agent_sessions WHERE id = $1 LIMIT 1;

-- name: ListAgentSessionsBySandbox :many
SELECT * FROM agent_sessions
WHERE sandbox_id = $1
ORDER BY started_at DESC;

-- name: ListActiveAgentSessions :many
SELECT * FROM agent_sessions
WHERE sandbox_id = $1 AND status = 'active'
ORDER BY started_at DESC;

-- name: UpdateAgentSessionStatus :one
UPDATE agent_sessions
SET status = $1,
    ended_at = CASE WHEN $2 = 'active' THEN NULL ELSE CURRENT_TIMESTAMP END
WHERE id = $3
RETURNING *;

-- name: DeleteAgentSession :exec
DELETE FROM agent_sessions WHERE id = $1;

-- name: CreateToolExecution :one
INSERT INTO tool_executions (
    id, session_id, tool_name, input
)
VALUES ($1, $2, $3, $4)
RETURNING *;

-- name: GetToolExecution :one
SELECT * FROM tool_executions WHERE id = $1 LIMIT 1;

-- name: ListToolExecutionsBySession :many
SELECT * FROM tool_executions
WHERE session_id = $1
ORDER BY created_at ASC;

-- name: UpdateToolExecutionStatus :one
UPDATE tool_executions
SET status = $1, started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
WHERE id = $2
RETURNING *;

-- name: CompleteToolExecution :one
UPDATE tool_executions
SET status = 'completed',
    output = $1,
    completed_at = CURRENT_TIMESTAMP
WHERE id = $2
RETURNING *;

-- name: FailToolExecution :one
UPDATE tool_executions
SET status = 'failed',
    error_message = $1,
    completed_at = CURRENT_TIMESTAMP
WHERE id = $2
RETURNING *;

-- name: CreateSandboxArtifact :one
INSERT INTO sandbox_artifacts (
    id, sandbox_id, session_id, type,
    storage_key, mime_type, size_bytes, metadata
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: GetSandboxArtifact :one
SELECT * FROM sandbox_artifacts WHERE id = $1 LIMIT 1;

-- name: ListSandboxArtifacts :many
SELECT * FROM sandbox_artifacts
WHERE sandbox_id = $1
ORDER BY created_at DESC;

-- name: ListSessionArtifacts :many
SELECT * FROM sandbox_artifacts
WHERE session_id = $1
ORDER BY created_at DESC;

-- name: DeleteSandboxArtifact :exec
DELETE FROM sandbox_artifacts WHERE id = $1;

-- name: CreateAPIKey :one
INSERT INTO api_keys (
    id, workspace_id, created_by, name,
    key_hash, key_prefix, scopes, expires_at, sandbox_id, read_only, label_selector
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
RETURNING *;

-- name: GetAPIKeyByHash :one
SELECT * FROM api_keys
WHERE key_hash = $1 AND revoked_at IS NULL
AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
LIMIT 1;

-- name: ListAPIKeysByWorkspace :many
SELECT id, workspace_id, created_by, name, key_prefix,
       scopes, expires_at, last_used_at, revoked_at, created_at, sandbox_id, read_only
FROM api_keys
WHERE workspace_id = $1
ORDER BY created_at DESC;

-- name: UpdateAPIKeyLastUsed :exec
UPDATE api_keys
SET last_used_at = CURRENT_TIMESTAMP
WHERE id = $1;

-- name: RevokeAPIKey :one
UPDATE api_keys
SET revoked_at = CURRENT_TIMESTAMP
WHERE id = $1 AND revoked_at IS NULL
RETURNING *;

-- name: DeleteAPIKey :exec
DELETE FROM api_keys WHERE id = $1;

-- name: CreateAuditLog :one
INSERT INTO audit_logs (
    id, workspace_id, actor_id, sandbox_id,
    action, resource_type, resource_id, metadata
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: ListAuditLogsByWorkspace :many
SELECT * FROM audit_logs
WHERE workspace_id = $1
ORDER BY created_at DESC
LIMIT $2 OFFSET $3;

-- name: ListAuditLogsBySandbox :many
SELECT * FROM audit_logs
WHERE sandbox_id = $1
ORDER BY created_at DESC
LIMIT $2 OFFSET $3;

-- name: DeleteAuditLogsBefore :exec
DELETE FROM audit_logs WHERE created_at < $1;
-- name: ListToolExecutionsBySandbox :many
SELECT te.* FROM tool_executions te
JOIN agent_sessions s ON s.id = te.session_id
WHERE s.sandbox_id = $1
ORDER BY te.created_at DESC
LIMIT $2;

-- name: ListAllSandboxes :many
SELECT * FROM sandboxes
WHERE deleted_at IS NULL
ORDER BY created_at DESC;

-- name: ClearSandboxRuntime :one
UPDATE sandboxes
SET runtime_id = NULL, runtime_host = NULL, access_url = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
RETURNING *;

-- name: CreateServer :one
INSERT INTO servers (id, name, docker_url, bind_address, platform, capabilities, created_by, workspace_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: UpdateServerCapabilities :exec
UPDATE servers SET capabilities = $1 WHERE id = $2;

-- name: GetServer :one
SELECT * FROM servers WHERE id = $1 LIMIT 1;

-- name: ListServersByWorkspace :many
SELECT * FROM servers WHERE workspace_id = $1 ORDER BY created_at;

-- name: ListAllServers :many
SELECT * FROM servers;

-- name: DeleteServer :exec
DELETE FROM servers WHERE id = $1;

-- name: SetSandboxPlacement :one
UPDATE sandboxes
SET server_id = $1, kind = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3
RETURNING *;

-- name: CreateProfile :one
INSERT INTO profiles (id, user_id, name, app, size_bytes, encrypted, platform, workspace_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: RenameProfile :one
UPDATE profiles SET name = $1 WHERE id = $2
RETURNING *;

-- name: ListAllProfiles :many
SELECT * FROM profiles;

-- name: SetProfileEncrypted :exec
UPDATE profiles SET encrypted = $1, size_bytes = $2 WHERE id = $3;

-- name: GetProfile :one
SELECT * FROM profiles WHERE id = $1 LIMIT 1;

-- name: ListProfilesByWorkspace :many
SELECT * FROM profiles WHERE workspace_id = $1 ORDER BY created_at DESC;

-- name: FindProfile :one
SELECT * FROM profiles WHERE workspace_id = $1 AND name = $2 AND app = $3 AND platform = $4 LIMIT 1;

-- name: CreateProfileVersion :one
INSERT INTO profile_versions (id, profile_id, version, size_bytes, encrypted, sandbox_id, created_by)
VALUES ($1, $2, (SELECT COALESCE(MAX(pv.version), 0) + 1 FROM profile_versions pv WHERE pv.profile_id = $3), $4, $5, $6, $7)
RETURNING *;

-- name: ListProfileVersions :many
SELECT * FROM profile_versions WHERE profile_id = $1 ORDER BY version DESC;

-- name: GetProfileVersion :one
SELECT * FROM profile_versions WHERE profile_id = $1 AND version = $2 LIMIT 1;

-- name: GetLatestProfileVersion :one
SELECT * FROM profile_versions WHERE profile_id = $1 ORDER BY version DESC LIMIT 1;

-- name: ListAllProfileVersions :many
SELECT v.*, p.user_id FROM profile_versions v JOIN profiles p ON p.id = v.profile_id;

-- name: SetProfileVersionEncrypted :exec
UPDATE profile_versions SET encrypted = $1, size_bytes = $2 WHERE id = $3;

-- name: DeleteProfileVersion :exec
DELETE FROM profile_versions WHERE id = $1;

-- name: SetProfileLatest :exec
UPDATE profiles SET size_bytes = $1, encrypted = $2 WHERE id = $3;

-- name: DeleteProfile :exec
DELETE FROM profiles WHERE id = $1;

-- name: DetachServer :exec
UPDATE sandboxes SET server_id = NULL WHERE server_id = $1;

-- name: CreateDomain :one
INSERT INTO domains (id, hostname, created_by)
VALUES ($1, $2, $3)
RETURNING *;

-- name: GetDomainByHostname :one
SELECT * FROM domains WHERE hostname = $1 LIMIT 1;

-- name: ListDomains :many
SELECT * FROM domains ORDER BY created_at;

-- name: DeleteDomain :exec
DELETE FROM domains WHERE id = $1;

-- name: CreateAgentMessage :one
INSERT INTO agent_messages (id, sandbox_id, run_id, kind, content, source, screenshot, a11y)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: GetAgentMessage :one
SELECT * FROM agent_messages WHERE id = $1 LIMIT 1;

-- name: ListAgentRunMessages :many
SELECT * FROM agent_messages
WHERE run_id = $1
ORDER BY seq ASC
LIMIT 1000 OFFSET $2;

-- name: ListAgentMessages :many
SELECT * FROM agent_messages
WHERE sandbox_id = $1
ORDER BY seq ASC;

-- name: DeleteAgentMessages :exec
DELETE FROM agent_messages WHERE sandbox_id = $1;

-- name: CreateAgentChannel :one
INSERT INTO agent_channels (id, sandbox_id, platform, external_id, created_by, allowed_users)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING *;

-- name: SetAgentChannelAllowedUsers :one
UPDATE agent_channels SET allowed_users = $1 WHERE id = $2
RETURNING *;

-- name: ListAgentChannelsBySandbox :many
SELECT * FROM agent_channels
WHERE sandbox_id = $1
ORDER BY created_at ASC;

-- name: GetAgentChannel :one
SELECT * FROM agent_channels
WHERE platform = $1 AND external_id = $2
LIMIT 1;

-- name: GetAgentChannelByID :one
SELECT * FROM agent_channels WHERE id = $1 LIMIT 1;

-- name: DeleteAgentChannel :exec
DELETE FROM agent_channels WHERE id = $1;

-- name: CreateVaultSecret :one
INSERT INTO vault_secrets (id, user_id, name, description, ciphertext, workspace_id)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING *;

-- name: GetVaultSecret :one
SELECT * FROM vault_secrets WHERE id = $1 LIMIT 1;

-- name: GetVaultSecretByName :one
SELECT * FROM vault_secrets WHERE workspace_id = $1 AND name = $2 LIMIT 1;

-- name: ListVaultSecretsByWorkspace :many
SELECT id, user_id, name, description, created_at, updated_at, last_used_at, expires_at, rotate_every_days, rotated_at
FROM vault_secrets
WHERE workspace_id = $1
ORDER BY name ASC;

-- name: ListAllVaultSecrets :many
SELECT * FROM vault_secrets;

-- name: UpdateVaultSecretValue :exec
UPDATE vault_secrets
SET ciphertext = $1, updated_at = CURRENT_TIMESTAMP, rotated_at = CURRENT_TIMESTAMP
WHERE id = $2;

-- name: SetVaultSecretSchedule :exec
UPDATE vault_secrets
SET expires_at = $1, rotate_every_days = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3;

-- name: MarkSandboxVaultSecretsUsed :exec
UPDATE sandbox_vault_secrets SET last_used_at = CURRENT_TIMESTAMP WHERE sandbox_id = $1;

-- name: ListVaultSecretSandboxes :many
SELECT g.sandbox_id, g.secret_id
FROM sandbox_vault_secrets g
JOIN sandboxes s ON s.id = g.sandbox_id
WHERE g.secret_id = $1 AND s.status != 'deleted';

-- name: UpdateVaultSecretDescription :exec
UPDATE vault_secrets
SET description = $1, updated_at = CURRENT_TIMESTAMP
WHERE id = $2;

-- name: RewrapVaultSecret :exec
UPDATE vault_secrets SET ciphertext = $1 WHERE id = $2;

-- name: MarkVaultSecretUsed :exec
UPDATE vault_secrets SET last_used_at = CURRENT_TIMESTAMP WHERE id = $1;

-- name: DeleteVaultSecret :exec
DELETE FROM vault_secrets WHERE id = $1;

-- name: AttachVaultSecret :exec
INSERT INTO sandbox_vault_secrets (sandbox_id, secret_id)
VALUES ($1, $2)
ON CONFLICT DO NOTHING;

-- name: DetachVaultSecret :exec
DELETE FROM sandbox_vault_secrets WHERE sandbox_id = $1 AND secret_id = $2;

-- name: DetachVaultSecretEverywhere :exec
DELETE FROM sandbox_vault_secrets WHERE secret_id = $1;

-- name: ListSandboxVaultSecrets :many
SELECT v.id, v.name, v.ciphertext
FROM sandbox_vault_secrets g
JOIN vault_secrets v ON v.id = g.secret_id
WHERE g.sandbox_id = $1
ORDER BY v.name ASC;

-- name: ListVaultGrantsByWorkspace :many
SELECT g.secret_id, g.sandbox_id, s.name AS sandbox_name, s.status AS sandbox_status, g.last_used_at
FROM sandbox_vault_secrets g
JOIN sandboxes s ON s.id = g.sandbox_id
WHERE s.workspace_id = $1 AND s.status != 'deleted'
ORDER BY s.name ASC;

-- name: ListAllSandboxSecrets :many
SELECT id, sandbox_id, secret_ref FROM sandbox_secrets;

-- name: RewrapSandboxSecret :exec
UPDATE sandbox_secrets SET secret_ref = $1 WHERE id = $2;

-- name: ListVaultAuditLogs :many
SELECT * FROM audit_logs
WHERE workspace_id = $1 AND resource_type IN ('secret', 'profile')
ORDER BY created_at DESC
LIMIT $2;

-- name: CreateJob :one
INSERT INTO jobs (id, sandbox_id, kind, args, max_attempts, deadline)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING *;

-- name: GetJob :one
SELECT * FROM jobs WHERE id = $1;

-- name: GetActiveJob :one
SELECT * FROM jobs
WHERE sandbox_id = $1 AND state IN ('queued', 'running')
ORDER BY created_at DESC
LIMIT 1;

-- name: GetLatestJob :one
SELECT * FROM jobs
WHERE sandbox_id = $1
ORDER BY created_at DESC, seq DESC
LIMIT 1;

-- name: ListDueJobs :many
SELECT * FROM jobs
WHERE state = 'queued' AND run_after <= CURRENT_TIMESTAMP
ORDER BY created_at ASC, seq ASC;

-- name: ListJobsByState :many
SELECT * FROM jobs WHERE state = $1 ORDER BY created_at ASC, seq ASC;

-- name: ClaimJob :one
UPDATE jobs
SET state = 'running', attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
WHERE id = $1 AND state = 'queued'
RETURNING *;

-- name: RetryJob :exec
UPDATE jobs
SET state = 'queued', last_error = $1, run_after = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3 AND state = 'running';

-- name: RequeueJob :exec
UPDATE jobs
SET state = 'queued', attempts = GREATEST(attempts - 1, 0), last_error = $1, updated_at = CURRENT_TIMESTAMP
WHERE id = $2 AND state = 'running';

-- name: FinishJob :exec
UPDATE jobs
SET state = $1, last_error = $2, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
WHERE id = $3 AND state IN ('queued', 'running');

-- name: CancelSandboxJobs :exec
UPDATE jobs
SET state = 'cancelled', finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
WHERE sandbox_id = $1 AND state = 'queued';

-- name: SetSandboxReachable :exec
UPDATE sandboxes SET unreachable_since = NULL WHERE id = $1 AND unreachable_since IS NOT NULL;

-- name: SetSandboxUnreachable :exec
UPDATE sandboxes SET unreachable_since = COALESCE(unreachable_since, CURRENT_TIMESTAMP) WHERE id = $1;

-- name: SetPoolSize :exec
INSERT INTO pool_settings (id, kind, server_id, size)
VALUES ($1, $2, $3, $4)
ON CONFLICT (id) DO UPDATE SET size = excluded.size, updated_at = CURRENT_TIMESTAMP;

-- name: ListPoolSettings :many
SELECT * FROM pool_settings ORDER BY kind, server_id;

-- name: CreatePoolSandbox :one
INSERT INTO pool_sandboxes (id, kind, server_id, image, config)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: ListPoolSandboxes :many
SELECT * FROM pool_sandboxes ORDER BY created_at, seq;

-- name: SetPoolSandboxRuntime :exec
UPDATE pool_sandboxes
SET runtime_id = $1, runtime_host = $2, access_url = $3, updated_at = CURRENT_TIMESTAMP
WHERE id = $4;

-- name: SetPoolSandboxIdle :exec
UPDATE pool_sandboxes SET status = 'idle', updated_at = CURRENT_TIMESTAMP WHERE id = $1 AND status = 'booting';

-- name: SetPoolSandboxFailed :exec
UPDATE pool_sandboxes SET status = 'failed', error_message = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2;

-- name: ClaimPoolSandbox :one
DELETE FROM pool_sandboxes
WHERE id = (
    SELECT p.id FROM pool_sandboxes p
    WHERE p.status = 'idle' AND p.kind = $1 AND COALESCE(p.server_id, '') = $2 AND p.image = $3
    ORDER BY p.created_at, p.seq
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING *;

-- name: DeletePoolSandbox :one
DELETE FROM pool_sandboxes WHERE id = $1 RETURNING *;

-- name: GetSecretKey :one
SELECT * FROM secret_keys WHERE id = $1 LIMIT 1;

-- name: GetSecretKeyByScope :one
SELECT * FROM secret_keys WHERE scope = $1 LIMIT 1;

-- name: CreateSecretKey :exec
INSERT INTO secret_keys (id, scope, wrapped) VALUES ($1, $2, $3)
ON CONFLICT (scope) DO NOTHING;

-- name: ListSecretKeys :many
SELECT * FROM secret_keys;

-- name: RewrapSecretKey :exec
UPDATE secret_keys SET wrapped = $1, rotated_at = CURRENT_TIMESTAMP WHERE id = $2;

-- name: DeferJob :exec
UPDATE jobs
SET state = 'queued', attempts = GREATEST(attempts - 1, 0), last_error = $1, run_after = $2, deadline = $3,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $4 AND state = 'running';

-- name: SetSandboxBoot :exec
UPDATE sandboxes
SET base_version = $1, boot_seconds = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3;

-- name: SetSandboxRecovered :exec
UPDATE sandboxes
SET recovered_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
WHERE id = $1;

-- name: GetWorkspaceAgentSettings :one
SELECT * FROM workspace_agent_settings WHERE workspace_id = $1 LIMIT 1;

-- name: UpsertWorkspaceAgentSettings :one
INSERT INTO workspace_agent_settings (
    workspace_id, provider, model, api_key_secret_id, api_base, max_steps, max_seconds, max_tokens, updated_by
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (workspace_id)
DO UPDATE SET
    provider = excluded.provider,
    model = excluded.model,
    api_key_secret_id = excluded.api_key_secret_id,
    api_base = excluded.api_base,
    max_steps = excluded.max_steps,
    max_seconds = excluded.max_seconds,
    max_tokens = excluded.max_tokens,
    updated_by = excluded.updated_by,
    updated_at = CURRENT_TIMESTAMP
RETURNING *;

-- name: DeleteWorkspaceAgentSettings :exec
DELETE FROM workspace_agent_settings WHERE workspace_id = $1;

-- name: ListAgentKeySecrets :many
SELECT api_key_secret_id FROM workspace_agent_settings WHERE api_key_secret_id IS NOT NULL;

-- name: CreateAgentRun :one
INSERT INTO agent_runs (
    id, sandbox_id, user_id, source, model, max_steps, max_seconds, max_tokens, max_attempts, provider, api_base
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
RETURNING *;

-- name: GetAgentRun :one
SELECT * FROM agent_runs WHERE id = $1 LIMIT 1;

-- name: GetActiveAgentRun :one
SELECT * FROM agent_runs WHERE sandbox_id = $1 AND state IN ('queued', 'running') LIMIT 1;

-- name: GetLatestAgentRun :one
SELECT * FROM agent_runs WHERE sandbox_id = $1 ORDER BY created_at DESC, seq DESC LIMIT 1;

-- name: ListQueuedAgentRuns :many
SELECT * FROM agent_runs WHERE state = 'queued' ORDER BY created_at ASC, seq ASC;

-- name: ListStaleAgentRuns :many
SELECT * FROM agent_runs WHERE state = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < $1);

-- name: ClaimAgentRun :one
UPDATE agent_runs
SET state = 'running', worker = $1, attempts = attempts + 1, heartbeat_at = CURRENT_TIMESTAMP,
    started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
WHERE id = $2 AND state = 'queued'
RETURNING *;

-- name: HeartbeatAgentRun :one
UPDATE agent_runs SET heartbeat_at = CURRENT_TIMESTAMP
WHERE id = $1 AND state = 'running' AND worker = $2
RETURNING cancel_requested, paused_at;

-- name: RecordAgentRunUsage :exec
UPDATE agent_runs SET steps = $1, tokens = $2, cost = $3 WHERE id = $4;

-- name: RequestAgentRunCancel :exec
UPDATE agent_runs SET cancel_requested = true WHERE id = $1 AND state IN ('queued', 'running');

-- name: RequeueAgentRun :exec
UPDATE agent_runs
SET state = 'queued', worker = NULL, heartbeat_at = NULL, error = $1, attempts = GREATEST(attempts - $2, 0)
WHERE id = $3 AND state = 'running';

-- name: FinishAgentRun :exec
UPDATE agent_runs
SET state = $1, error = $2, finished_at = CURRENT_TIMESTAMP, worker = NULL, paused_at = NULL, paused_by = NULL
WHERE id = $3 AND state IN ('queued', 'running');

-- name: ListAgentRunIds :many
SELECT id FROM agent_runs;

-- name: RecordChatEvent :one
INSERT INTO chat_events (platform, event_id) VALUES ($1, $2)
ON CONFLICT (platform, event_id) DO NOTHING
RETURNING event_id;

-- name: PurgeChatEvents :exec
DELETE FROM chat_events WHERE created_at < $1;

-- name: AcquireLease :one
INSERT INTO leases (name, holder, expires_at) VALUES ($1, $2, $3)
ON CONFLICT (name) DO UPDATE SET holder = excluded.holder, expires_at = excluded.expires_at
WHERE leases.holder = excluded.holder OR leases.expires_at < CURRENT_TIMESTAMP
RETURNING holder;

-- name: ReleaseLease :exec
DELETE FROM leases WHERE name = $1 AND holder = $2;

-- name: CountSandboxesByState :many
SELECT status, kind, COUNT(*) AS count FROM sandboxes WHERE status != 'deleted' GROUP BY status, kind;

-- name: ListAgentRunsBySandbox :many
SELECT * FROM agent_runs WHERE sandbox_id = $1 ORDER BY created_at DESC, seq DESC;

-- name: GetNodeAuthority :one
SELECT * FROM node_authority WHERE id = 1;

-- name: CreateNodeAuthority :exec
INSERT INTO node_authority (id, certificate, key_ciphertext) VALUES (1, $1, $2)
ON CONFLICT (id) DO NOTHING;

-- name: CreateNodeToken :one
INSERT INTO node_tokens (id, secret_hash, created_by, server_id, name, expires_at, workspace_id)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING *;

-- name: ClaimNodeToken :one
UPDATE node_tokens SET used_at = CURRENT_TIMESTAMP
WHERE secret_hash = $1 AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP
RETURNING *;

-- name: PurgeNodeTokens :exec
DELETE FROM node_tokens WHERE expires_at < $1;

-- name: UpsertNode :one
INSERT INTO nodes (id, server_id, serial, cert_expires_at, os, arch, hostname)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (server_id) DO UPDATE SET
    id = excluded.id, serial = excluded.serial, cert_expires_at = excluded.cert_expires_at, os = excluded.os,
    arch = excluded.arch, hostname = excluded.hostname, version = '', seen_at = NULL
RETURNING *;

-- name: GetNode :one
SELECT * FROM nodes WHERE id = $1 LIMIT 1;

-- name: GetNodeByServer :one
SELECT * FROM nodes WHERE server_id = $1 LIMIT 1;

-- name: ListNodes :many
SELECT * FROM nodes;

-- name: SetNodeCertificate :exec
UPDATE nodes SET serial = $1, cert_expires_at = $2 WHERE id = $3;

-- name: SetNodeHello :exec
UPDATE nodes SET version = $1, os = $2, arch = $3, hostname = $4, drivers = $5, targets = $6, seen_at = CURRENT_TIMESTAMP
WHERE id = $7;

-- name: SetNodeStatus :exec
UPDATE nodes
SET cpus = $1, memory_total = $2, memory_available = $3, disk_total = $4, disk_free = $5, load = $6, sandboxes = $7,
    checks = $8, seen_at = CURRENT_TIMESTAMP
WHERE id = $9;

-- name: CreateSnapshot :one
INSERT INTO snapshots (id, sandbox_id, server_id, name, created_by)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: SetSnapshotReady :exec
UPDATE snapshots SET state = 'ready', size_bytes = $1, error = NULL WHERE id = $2;

-- name: SetSnapshotFailed :exec
UPDATE snapshots SET state = 'failed', error = $1 WHERE id = $2;

-- name: GetSnapshot :one
SELECT * FROM snapshots WHERE id = $1 LIMIT 1;

-- name: ListSnapshotsBySandbox :many
SELECT * FROM snapshots WHERE sandbox_id = $1 ORDER BY created_at DESC, seq DESC;

-- name: DeleteSnapshot :exec
DELETE FROM snapshots WHERE id = $1;

-- name: CreateTicket :exec
INSERT INTO tickets (ticket_hash, user_id, target, expires_at) VALUES ($1, $2, $3, $4);

-- name: RedeemTicket :one
-- one statement, so a ticket is spent exactly once even when two processes redeem it together; a ticket used for
-- the wrong target is spent too
DELETE FROM tickets WHERE ticket_hash = $1
RETURNING *;

-- name: PurgeTickets :exec
DELETE FROM tickets WHERE expires_at <= CURRENT_TIMESTAMP;

-- name: GetPoolSandboxByRuntimeID :one
SELECT * FROM pool_sandboxes WHERE runtime_id = $1 LIMIT 1;

-- name: ListAuditLogs :many
-- newest first; a page continues after (before_at, before_id), the last row of the page before
SELECT a.*, u.email AS actor_email
FROM audit_logs a
LEFT JOIN users u ON u.id = a.actor_id
WHERE a.workspace_id = sqlc.arg(workspace_id)
  AND (sqlc.narg(actor_id)::text IS NULL OR a.actor_id = sqlc.narg(actor_id))
  AND (sqlc.narg(action)::text IS NULL OR a.action = sqlc.narg(action))
  AND (sqlc.narg(resource_type)::text IS NULL OR a.resource_type = sqlc.narg(resource_type))
  AND (sqlc.narg(sandbox_id)::text IS NULL OR a.sandbox_id = sqlc.narg(sandbox_id))
  AND (sqlc.narg(since)::timestamptz IS NULL OR a.created_at >= sqlc.narg(since))
  AND (sqlc.narg(until)::timestamptz IS NULL OR a.created_at < sqlc.narg(until))
  AND (sqlc.narg(before_at)::timestamptz IS NULL
       OR (a.created_at, a.id) < (sqlc.narg(before_at)::timestamptz, sqlc.narg(before_id)::text))
ORDER BY a.created_at DESC, a.id DESC
LIMIT sqlc.arg(row_limit);

-- name: SetSandboxSize :exec
UPDATE sandboxes SET cpus = $1, memory_mb = $2, disk_gb = $3 WHERE id = $4;

-- name: LockWorkspace :one
-- held until the transaction ends, so quota checks in one workspace take turns
SELECT id FROM workspaces WHERE id = $1 FOR UPDATE;

-- name: EnsureWorkspaceQuota :exec
INSERT INTO workspace_quotas (workspace_id, max_running_sandboxes, max_cpus, max_memory_mb, max_storage_gb)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (workspace_id) DO NOTHING;

-- name: GetWorkspaceQuota :one
SELECT * FROM workspace_quotas WHERE workspace_id = $1 LIMIT 1;

-- name: SetWorkspaceQuota :one
INSERT INTO workspace_quotas (workspace_id, max_running_sandboxes, max_cpus, max_memory_mb, max_storage_gb)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (workspace_id) DO UPDATE SET
    max_running_sandboxes = excluded.max_running_sandboxes,
    max_cpus = excluded.max_cpus,
    max_memory_mb = excluded.max_memory_mb,
    max_storage_gb = excluded.max_storage_gb,
    updated_at = CURRENT_TIMESTAMP
RETURNING *;

-- name: WorkspaceUsage :one
-- running counts what holds CPU and memory (booting too); storage counts every sandbox's disk, a sandbox without a
-- disk size as unsized_gb, plus profile versions and snapshots
SELECT
    COUNT(*) FILTER (WHERE s.status IN ('pending', 'provisioning', 'running'))::bigint AS running,
    COALESCE(SUM(s.cpus) FILTER (WHERE s.status IN ('pending', 'provisioning', 'running')), 0)::float8 AS cpus,
    COALESCE(SUM(s.memory_mb) FILTER (WHERE s.status IN ('pending', 'provisioning', 'running')), 0)::bigint AS memory_mb,
    COALESCE(SUM(COALESCE(s.disk_gb, sqlc.arg(unsized_gb)::bigint)), 0)::bigint AS disk_gb,
    (SELECT COALESCE(SUM(pv.size_bytes), 0) FROM profile_versions pv JOIN profiles p ON p.id = pv.profile_id
     WHERE p.workspace_id = sqlc.arg(workspace_id))::bigint AS profile_bytes,
    (SELECT COALESCE(SUM(sn.size_bytes), 0) FROM snapshots sn JOIN sandboxes x ON x.id = sn.sandbox_id
     WHERE x.workspace_id = sqlc.arg(workspace_id))::bigint AS snapshot_bytes
FROM sandboxes s
WHERE s.workspace_id = sqlc.arg(workspace_id) AND s.status != 'deleted';

-- name: TouchSandbox :exec
-- at most twice a minute per sandbox: every tool call and open viewer lands here
UPDATE sandboxes SET last_activity_at = CURRENT_TIMESTAMP
WHERE id = $1 AND (last_activity_at IS NULL OR last_activity_at < CURRENT_TIMESTAMP - interval '30 seconds');

-- name: SetSandboxLifecycle :exec
UPDATE sandboxes SET idle_timeout_minutes = $1, max_lifetime_minutes = $2 WHERE id = $3;

-- name: SetWorkspaceLifecycle :one
UPDATE workspaces
SET default_idle_timeout_minutes = $1, default_max_lifetime_minutes = $2, updated_at = CURRENT_TIMESTAMP
WHERE id = $3
RETURNING *;

-- name: ListExpiredSandboxes :many
-- running sandboxes past their maximum lifetime, or idle past their timeout, with nothing queued for them; the
-- last start counts as activity, so a restarted sandbox gets a full timeout
SELECT s.id, s.workspace_id,
    (CASE
        WHEN COALESCE(s.max_lifetime_minutes, w.default_max_lifetime_minutes) IS NOT NULL
            AND s.started_at < CURRENT_TIMESTAMP
                - make_interval(mins => COALESCE(s.max_lifetime_minutes, w.default_max_lifetime_minutes)::int)
        THEN 'lifetime'
        ELSE 'idle'
    END)::text AS reason
FROM sandboxes s
JOIN workspaces w ON w.id = s.workspace_id
WHERE s.status = 'running'
AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.sandbox_id = s.id AND j.state IN ('queued', 'running'))
AND (
    (COALESCE(s.max_lifetime_minutes, w.default_max_lifetime_minutes) IS NOT NULL
        AND s.started_at < CURRENT_TIMESTAMP
            - make_interval(mins => COALESCE(s.max_lifetime_minutes, w.default_max_lifetime_minutes)::int))
    OR (COALESCE(s.idle_timeout_minutes, w.default_idle_timeout_minutes) IS NOT NULL
        AND GREATEST(s.last_activity_at, s.started_at) < CURRENT_TIMESTAMP
            - make_interval(mins => COALESCE(s.idle_timeout_minutes, w.default_idle_timeout_minutes)::int))
);

-- Viewer links (server/viewer_api.py)

-- name: CreateViewerLink :one
INSERT INTO viewer_links (id, workspace_id, sandbox_id, token_hash, mode, end_user, created_by, expires_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: GetLiveViewerLink :one
-- a link that is neither revoked nor expired
SELECT * FROM viewer_links
WHERE token_hash = $1 AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
LIMIT 1;

-- name: ViewerLinkLive :one
SELECT EXISTS (
    SELECT 1 FROM viewer_links WHERE id = $1 AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
)::bool AS live;

-- name: GetViewerLink :one
SELECT * FROM viewer_links WHERE id = $1 LIMIT 1;

-- name: ListViewerLinks :many
SELECT * FROM viewer_links WHERE sandbox_id = $1 ORDER BY created_at DESC LIMIT 200;

-- name: RevokeViewerLink :exec
UPDATE viewer_links SET revoked_at = CURRENT_TIMESTAMP WHERE id = $1 AND revoked_at IS NULL;

-- Takeover (server/agent_api.py)

-- name: PauseAgentRun :one
UPDATE agent_runs SET paused_at = CURRENT_TIMESTAMP, paused_by = $1
WHERE id = $2 AND state = 'running' AND paused_at IS NULL
RETURNING *;

-- name: ResumeAgentRun :one
UPDATE agent_runs SET paused_at = NULL, paused_by = NULL
WHERE id = $1 AND paused_at IS NOT NULL
RETURNING *;

-- Labels (server/labels.py)

-- name: SetSandboxLabel :exec
INSERT INTO sandbox_labels (sandbox_id, key, value) VALUES ($1, $2, $3)
ON CONFLICT (sandbox_id, key) DO UPDATE SET value = EXCLUDED.value;

-- name: DeleteSandboxLabels :exec
DELETE FROM sandbox_labels WHERE sandbox_id = $1;

-- name: ListSandboxLabels :many
SELECT * FROM sandbox_labels WHERE sandbox_id = $1 ORDER BY key;

-- name: ListWorkspaceSandboxLabels :many
SELECT l.* FROM sandbox_labels l
JOIN sandboxes s ON s.id = l.sandbox_id
WHERE s.workspace_id = $1 AND s.deleted_at IS NULL;

-- name: SetUsageLabel :exec
INSERT INTO usage_labels (sandbox_id, key, value) VALUES ($1, $2, $3)
ON CONFLICT (sandbox_id, key) DO UPDATE SET value = EXCLUDED.value;

-- name: DeleteUsageLabels :exec
DELETE FROM usage_labels WHERE sandbox_id = $1;

-- Label quotas (server/quotas.py)

-- name: UpsertLabelQuota :one
INSERT INTO label_quotas (
    workspace_id, key, value, max_running_sandboxes, max_cpus, max_memory_mb, max_storage_gb
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (workspace_id, key, value) DO UPDATE SET
    max_running_sandboxes = EXCLUDED.max_running_sandboxes,
    max_cpus = EXCLUDED.max_cpus,
    max_memory_mb = EXCLUDED.max_memory_mb,
    max_storage_gb = EXCLUDED.max_storage_gb,
    updated_at = CURRENT_TIMESTAMP
RETURNING *;

-- name: ListLabelQuotas :many
SELECT * FROM label_quotas WHERE workspace_id = $1 ORDER BY key, value;

-- name: DeleteLabelQuota :exec
DELETE FROM label_quotas WHERE workspace_id = $1 AND key = $2 AND value = $3;

-- name: GetLabelQuota :one
-- the value's own limits, else the key's default ('*')
SELECT * FROM label_quotas
WHERE workspace_id = $1 AND key = $2 AND value IN ($3, '*')
ORDER BY (value = '*') ASC
LIMIT 1;

-- name: LabelUsage :one
-- WorkspaceUsage for the sandboxes carrying one label value; profiles belong to the workspace, not a label
SELECT
    COUNT(*) FILTER (WHERE s.status IN ('pending', 'provisioning', 'running'))::bigint AS running,
    COALESCE(SUM(s.cpus) FILTER (WHERE s.status IN ('pending', 'provisioning', 'running')), 0)::float8 AS cpus,
    COALESCE(SUM(s.memory_mb) FILTER (WHERE s.status IN ('pending', 'provisioning', 'running')), 0)::bigint AS memory_mb,
    COALESCE(SUM(COALESCE(s.disk_gb, sqlc.arg(unsized_gb)::bigint)), 0)::bigint AS disk_gb,
    (SELECT COALESCE(SUM(sn.size_bytes), 0) FROM snapshots sn
     JOIN sandboxes x ON x.id = sn.sandbox_id
     JOIN sandbox_labels xl ON xl.sandbox_id = x.id AND xl.key = sqlc.arg(key) AND xl.value = sqlc.arg(value)
     WHERE x.workspace_id = sqlc.arg(workspace_id))::bigint AS snapshot_bytes
FROM sandboxes s
JOIN sandbox_labels l ON l.sandbox_id = s.id AND l.key = sqlc.arg(key) AND l.value = sqlc.arg(value)
WHERE s.workspace_id = sqlc.arg(workspace_id) AND s.status NOT IN ('deleting', 'deleted');

-- Events (server/events.py)

-- name: ListEvents :many
-- after `after_seq`, oldest first
SELECT * FROM events
WHERE workspace_id = sqlc.arg(workspace_id)
  AND seq > sqlc.arg(after_seq)
  AND (sqlc.narg(sandbox_id)::text IS NULL OR sandbox_id = sqlc.narg(sandbox_id))
  AND (sqlc.narg(type)::text IS NULL OR type = sqlc.narg(type))
ORDER BY seq ASC
LIMIT sqlc.arg(row_limit);

-- name: ListLateEvents :many
-- written since `since` with a seq at most `upto_seq`: rows that committed after ones with a higher seq
SELECT * FROM events
WHERE workspace_id = sqlc.arg(workspace_id)
  AND seq <= sqlc.arg(upto_seq)
  AND created_at >= sqlc.arg(since)
  AND (sqlc.narg(sandbox_id)::text IS NULL OR sandbox_id = sqlc.narg(sandbox_id))
  AND (sqlc.narg(type)::text IS NULL OR type = sqlc.narg(type))
ORDER BY seq ASC
LIMIT sqlc.arg(row_limit);

-- name: LatestEventSeq :one
SELECT COALESCE(MAX(seq), 0)::bigint AS seq FROM events WHERE workspace_id = $1;

-- name: PurgeEvents :exec
DELETE FROM events WHERE created_at < $1;

-- name: EmitEvent :exec
SELECT zoo_event(sqlc.arg(workspace_id)::text, sqlc.narg(sandbox_id)::text, NULL::text, sqlc.arg(type)::text,
    sqlc.arg(data)::jsonb);

-- name: GetEvent :one
SELECT * FROM events WHERE id = $1 LIMIT 1;

-- name: LatestWorkspaceEvent :one
SELECT * FROM events WHERE workspace_id = $1 AND type = $2 ORDER BY seq DESC LIMIT 1;

-- Webhooks (server/webhooks.py)

-- name: CreateWebhook :one
INSERT INTO webhooks (id, workspace_id, url, secret_ciphertext, types, label_selector, enabled, created_by)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: GetWebhook :one
SELECT * FROM webhooks WHERE id = $1 LIMIT 1;

-- name: ListWebhooks :many
SELECT * FROM webhooks WHERE workspace_id = $1 ORDER BY created_at;

-- name: UpdateWebhook :one
UPDATE webhooks SET url = $1, types = $2, label_selector = $3, enabled = $4
WHERE id = $5
RETURNING *;

-- name: DeleteWebhook :exec
DELETE FROM webhooks WHERE id = $1;

-- name: CreateWebhookDelivery :exec
INSERT INTO webhook_deliveries (webhook_id, event_id) VALUES ($1, $2);

-- name: ClaimWebhookDeliveries :many
-- due deliveries, leased for lease_seconds so a worker that dies mid-send leaves them to another
UPDATE webhook_deliveries
SET next_attempt_at = CURRENT_TIMESTAMP + make_interval(secs => sqlc.arg(lease_seconds)::int)
WHERE id IN (
    SELECT d.id FROM webhook_deliveries d
    WHERE d.status = 'pending' AND d.next_attempt_at <= CURRENT_TIMESTAMP
    ORDER BY d.seq
    LIMIT sqlc.arg(row_limit)
    FOR UPDATE SKIP LOCKED
)
RETURNING *;

-- name: FinishWebhookDelivery :exec
UPDATE webhook_deliveries
SET status = $1, attempts = $2, next_attempt_at = $3, last_error = $4, response_status = $5,
    delivered_at = CASE WHEN $1 = 'delivered' THEN CURRENT_TIMESTAMP ELSE NULL END
WHERE id = $6;

-- name: ListWebhookDeliveries :many
SELECT d.*, e.type AS event_type
FROM webhook_deliveries d
JOIN events e ON e.id = d.event_id
WHERE d.webhook_id = $1
ORDER BY d.created_at DESC
LIMIT $2;

-- name: PurgeWebhookDeliveries :exec
DELETE FROM webhook_deliveries WHERE status <> 'pending' AND created_at < $1;

-- Usage (server/metering.py)

-- name: AddUsage :exec
INSERT INTO usage (workspace_id, sandbox_id, metric, period_start, quantity)
VALUES ($1, $2, $3, date_trunc('hour', sqlc.arg(at)::timestamptz), $4)
ON CONFLICT (workspace_id, sandbox_id, metric, period_start)
DO UPDATE SET quantity = usage.quantity + EXCLUDED.quantity;

-- name: ClaimStorageMetering :many
-- workspaces whose storage hasn't been metered this hour, marked as metered
UPDATE workspaces SET storage_metered_at = date_trunc('hour', CURRENT_TIMESTAMP)
WHERE storage_metered_at IS NULL OR storage_metered_at < date_trunc('hour', CURRENT_TIMESTAMP)
RETURNING id;

-- name: SandboxStorage :many
-- each live sandbox's disk (unsized_gb without a size) plus its snapshots, in GB
SELECT s.id,
    (COALESCE(s.disk_gb, sqlc.arg(unsized_gb)::bigint)
     + COALESCE((SELECT SUM(sn.size_bytes) FROM snapshots sn WHERE sn.sandbox_id = s.id), 0) / 1073741824.0
    )::float8 AS gb
FROM sandboxes s
WHERE s.workspace_id = sqlc.arg(workspace_id) AND s.status NOT IN ('deleting', 'deleted');

-- name: ProfileStorage :one
SELECT (COALESCE(SUM(pv.size_bytes), 0) / 1073741824.0)::float8 AS gb
FROM profile_versions pv JOIN profiles p ON p.id = pv.profile_id
WHERE p.workspace_id = $1;

-- name: UsageReport :many
-- per sandbox, metric and day
SELECT u.sandbox_id, u.metric, date_trunc('day', u.period_start)::timestamptz AS day,
    SUM(u.quantity)::float8 AS quantity
FROM usage u
WHERE u.workspace_id = sqlc.arg(workspace_id)
  AND u.period_start >= sqlc.arg(since)
  AND u.period_start < sqlc.arg(until)
  AND (sqlc.narg(metric)::text IS NULL OR u.metric = sqlc.narg(metric))
GROUP BY u.sandbox_id, u.metric, day
ORDER BY day, u.sandbox_id, u.metric;

-- name: ListUsageLabels :many
SELECT ul.* FROM usage_labels ul
WHERE ul.sandbox_id IN (SELECT DISTINCT u.sandbox_id FROM usage u WHERE u.workspace_id = $1);

-- Trajectory exports (server/exports.py)

-- name: SetWorkspaceCaptureA11y :one
UPDATE workspaces SET capture_a11y = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2 RETURNING *;

-- name: ListExportableRuns :many
-- finished runs in the workspace, oldest first
SELECT r.* FROM agent_runs r
JOIN sandboxes s ON s.id = r.sandbox_id
WHERE s.workspace_id = sqlc.arg(workspace_id)
  AND r.state IN ('succeeded', 'failed', 'cancelled')
  AND (sqlc.narg(sandbox_id)::text IS NULL OR r.sandbox_id = sqlc.narg(sandbox_id))
  AND (sqlc.narg(since)::timestamptz IS NULL OR r.created_at >= sqlc.narg(since))
  AND (sqlc.narg(until)::timestamptz IS NULL OR r.created_at < sqlc.narg(until))
ORDER BY r.created_at ASC
LIMIT sqlc.arg(row_limit);

-- name: CreateExport :one
INSERT INTO exports (id, workspace_id, created_by, run_ids) VALUES ($1, $2, $3, $4) RETURNING *;

-- name: GetExport :one
SELECT * FROM exports WHERE id = $1 LIMIT 1;

-- name: ListExports :many
SELECT * FROM exports WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT 100;

-- name: ClaimExport :one
-- the oldest queued export, or one whose worker went quiet for stale_seconds
UPDATE exports SET state = 'running', worker = sqlc.arg(worker), claimed_at = CURRENT_TIMESTAMP
WHERE id = (
    SELECT e.id FROM exports e
    WHERE e.state = 'queued'
       OR (e.state = 'running'
           AND e.claimed_at < CURRENT_TIMESTAMP - make_interval(secs => sqlc.arg(stale_seconds)::int))
    ORDER BY e.seq
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING *;

-- name: FinishExport :exec
UPDATE exports SET state = $1, error = $2, steps = $3, size_bytes = $4, finished_at = CURRENT_TIMESTAMP, worker = NULL
WHERE id = $5;

-- name: PurgeExports :many
DELETE FROM exports WHERE created_at < $1 RETURNING id;

-- Benchmarks (server/bench.py)

-- name: CreateBenchmark :one
INSERT INTO benchmarks (id, workspace_id, created_by, name, suite, models, parallel)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING *;

-- name: GetBenchmark :one
SELECT * FROM benchmarks WHERE id = $1 LIMIT 1;

-- name: ListBenchmarks :many
SELECT * FROM benchmarks WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT 100;

-- name: ClaimBenchmark :one
UPDATE benchmarks
SET state = 'running', worker = $1, heartbeat_at = CURRENT_TIMESTAMP, started_at = CURRENT_TIMESTAMP
WHERE id = (
    SELECT b.id FROM benchmarks b WHERE b.state = 'queued' ORDER BY b.seq LIMIT 1 FOR UPDATE SKIP LOCKED
)
RETURNING *;

-- name: HeartbeatBenchmark :one
UPDATE benchmarks SET heartbeat_at = CURRENT_TIMESTAMP
WHERE id = $1 AND state = 'running' AND worker = $2
RETURNING cancel_requested;

-- name: RequestBenchmarkCancel :exec
UPDATE benchmarks SET cancel_requested = true WHERE id = $1 AND state IN ('queued', 'running');

-- name: FinishBenchmark :exec
UPDATE benchmarks SET state = $1, error = $2, finished_at = CURRENT_TIMESTAMP, worker = NULL
WHERE id = $3 AND state IN ('queued', 'running');

-- name: FailStaleBenchmarks :many
-- a benchmark whose worker stopped heartbeating isn't resumed: its sandboxes and runs were that worker's
UPDATE benchmarks
SET state = 'failed', error = 'its worker stopped', finished_at = CURRENT_TIMESTAMP, worker = NULL
WHERE state = 'running' AND heartbeat_at < $1
RETURNING id;

-- name: UpsertBenchmarkResult :exec
INSERT INTO benchmark_results (
    benchmark_id, task_id, model, run_id, sandbox_id, passed, score, steps, tokens, cost, seconds, error
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (benchmark_id, task_id, model) DO UPDATE SET
    run_id = EXCLUDED.run_id, sandbox_id = EXCLUDED.sandbox_id, passed = EXCLUDED.passed, score = EXCLUDED.score,
    steps = EXCLUDED.steps, tokens = EXCLUDED.tokens, cost = EXCLUDED.cost, seconds = EXCLUDED.seconds,
    error = EXCLUDED.error, finished_at = CURRENT_TIMESTAMP;

-- name: ListBenchmarkResults :many
SELECT * FROM benchmark_results WHERE benchmark_id = $1 ORDER BY task_id, model;

-- name: RenameSandbox :one
UPDATE sandboxes SET name = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2 RETURNING *;
