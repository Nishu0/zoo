-- +goose Up
-- when the user finished or skipped the dashboard's first-run steps (web /welcome); NULL shows them. Users from
-- before onboarding existed don't need it.
ALTER TABLE users ADD COLUMN onboarded_at TIMESTAMPTZ;
UPDATE users SET onboarded_at = now();

-- +goose Down
ALTER TABLE users DROP COLUMN onboarded_at;
