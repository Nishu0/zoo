package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"slices"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

const lookupNodeSQL = `
SELECT n.id, n.server_id, n.serial, n.os, n.arch, n.ssh_host_key, n.cert_expires_at
FROM nodes n JOIN servers s ON s.id = n.server_id
WHERE n.id = $1 AND n.serial <> '' AND n.ssh_host_key <> ''
    AND n.cert_expires_at > CURRENT_TIMESTAMP`

const lockNodeSQL = `
SELECT s.workspace_id, s.platform, s.capabilities
FROM servers s JOIN nodes n ON n.server_id = s.id
WHERE n.id = $1 AND n.server_id = $2 AND n.serial = $3
    AND n.cert_expires_at = $4 AND n.cert_expires_at > CURRENT_TIMESTAMP
FOR UPDATE OF s, n`

const writeNodeHelloSQL = `
UPDATE nodes
SET version = $1, os = $2, arch = $3, hostname = $4, drivers = $5, targets = $6,
    seen_at = CURRENT_TIMESTAMP
WHERE id = $7 AND server_id = $8 AND serial = $9
    AND cert_expires_at = $10 AND cert_expires_at > CURRENT_TIMESTAMP`

const writeNodeCapabilitiesSQL = `
UPDATE servers SET capabilities = $1 WHERE id = $2 AND capabilities IS DISTINCT FROM $1`

const writeNodeStatusSQL = `
UPDATE nodes
SET cpus = $1, memory_total = $2, memory_available = $3, disk_total = $4, disk_free = $5,
    load = $6, sandboxes = $7, checks = $8, seen_at = CURRENT_TIMESTAMP
WHERE id = $9 AND server_id = $10 AND serial = $11
    AND cert_expires_at = $12 AND cert_expires_at > CURRENT_TIMESTAMP`

const nodeOnlineSQL = `
SELECT zoo_event($1::text, NULL::text, 'server.state', $2::jsonb)`

const swapNodeCertificateSQL = `
UPDATE nodes SET serial = $1, cert_expires_at = $2
WHERE id = $3 AND serial = $4 AND serial <> '' AND cert_expires_at > CURRENT_TIMESTAMP`

type postgresNodeDatabase interface {
	QueryRow(context.Context, string, ...any) pgx.Row
	Begin(context.Context) (pgx.Tx, error)
}

type postgresNodeStore struct {
	database postgresNodeDatabase
}

var _ nodeStore = (*postgresNodeStore)(nil)

func newPostgresNodeStore(pool *pgxpool.Pool) *postgresNodeStore {
	return &postgresNodeStore{database: pool}
}

func (store *postgresNodeStore) Lookup(ctx context.Context, nodeID string) (nodeRecord, error) {
	var record nodeRecord
	err := store.database.QueryRow(ctx, lookupNodeSQL, nodeID).Scan(
		&record.NodeID, &record.ServerID, &record.Serial, &record.OS, &record.Arch, &record.SSHHostKey, &record.ExpiresAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nodeRecord{}, errNodeNotFound
	}
	return record, err
}

func (store *postgresNodeStore) transaction(ctx context.Context, write func(pgx.Tx) error) error {
	transaction, err := store.database.Begin(ctx)
	if err != nil {
		return err
	}
	defer transaction.Rollback(ctx)
	if err := write(transaction); err != nil {
		return err
	}
	return transaction.Commit(ctx)
}

type nodeServerData struct {
	workspaceID  string
	platform     string
	capabilities string
}

func (store *postgresNodeStore) withNode(ctx context.Context, record nodeRecord, write func(pgx.Tx, nodeServerData) error) error {
	if record.NodeID == "" || record.ServerID == "" || record.Serial == "" {
		return errNodeNotFound
	}
	return store.transaction(ctx, func(transaction pgx.Tx) error {
		var server nodeServerData
		err := transaction.QueryRow(ctx, lockNodeSQL, record.NodeID, record.ServerID, record.Serial, record.ExpiresAt).Scan(
			&server.workspaceID, &server.platform, &server.capabilities,
		)
		if errors.Is(err, pgx.ErrNoRows) {
			return errNodeNotFound
		}
		if err != nil {
			return err
		}
		return write(transaction, server)
	})
}

func nodeWriteResult(tag pgconn.CommandTag, err error) error {
	if err != nil {
		return err
	}
	switch tag.RowsAffected() {
	case 0:
		return errNodeNotFound
	case 1:
		return nil
	default:
		return fmt.Errorf("node write affected %d rows instead of one", tag.RowsAffected())
	}
}

func nodeJSONSlice[Value any](values []Value) (string, error) {
	if values == nil {
		values = make([]Value, 0)
	}
	raw, err := json.Marshal(values)
	return string(raw), err
}

func (store *postgresNodeStore) WriteHello(ctx context.Context, record nodeRecord, hello nodeHelloData) error {
	if hello.OS != record.OS || hello.Arch != record.Arch {
		return errNodeNotFound
	}
	drivers, err := nodeJSONSlice(hello.Drivers)
	if err != nil {
		return err
	}
	targets, err := nodeJSONSlice(hello.Targets)
	if err != nil {
		return err
	}
	return store.withNode(ctx, record, func(transaction pgx.Tx, server nodeServerData) error {
		var driver string
		switch server.platform {
		case "macos":
			driver = "zoovm"
		case "windows":
			driver = "hyperv"
		default:
			return fmt.Errorf("unsupported node host platform %q", server.platform)
		}
		capabilities := ""
		if slices.Contains(hello.Targets, "ssh") {
			for _, reported := range hello.Drivers {
				if reported.Name == driver && reported.Available {
					capabilities = server.platform
					break
				}
			}
		}
		if err := nodeWriteResult(transaction.Exec(ctx, writeNodeHelloSQL,
			hello.Version, hello.OS, hello.Arch, hello.Hostname, drivers, targets,
			record.NodeID, record.ServerID, record.Serial, record.ExpiresAt,
		)); err != nil {
			return err
		}
		if capabilities == server.capabilities {
			return nil
		}
		return nodeWriteResult(transaction.Exec(ctx, writeNodeCapabilitiesSQL, capabilities, record.ServerID))
	})
}

func (store *postgresNodeStore) WriteStatus(ctx context.Context, record nodeRecord, report nodeStatusData) error {
	if report.MemoryTotal > math.MaxInt64 || report.MemoryAvailable > math.MaxInt64 || report.DiskTotal > math.MaxInt64 || report.DiskFree > math.MaxInt64 {
		return errors.New("node capacity exceeds PostgreSQL BIGINT range")
	}
	sandboxes, err := nodeJSONSlice(report.Sandboxes)
	if err != nil {
		return err
	}
	checks, err := nodeJSONSlice(report.Checks)
	if err != nil {
		return err
	}
	return store.withNode(ctx, record, func(transaction pgx.Tx, server nodeServerData) error {
		return nodeWriteResult(transaction.Exec(ctx, writeNodeStatusSQL,
			int64(report.CPUs), int64(report.MemoryTotal), int64(report.MemoryAvailable), int64(report.DiskTotal), int64(report.DiskFree),
			report.Load, sandboxes, checks, record.NodeID, record.ServerID, record.Serial, record.ExpiresAt,
		))
	})
}

func (store *postgresNodeStore) SetOnline(ctx context.Context, record nodeRecord, online bool) error {
	payload, err := json.Marshal(struct {
		ServerID string `json:"server_id"`
		Online   bool   `json:"online"`
	}{record.ServerID, online})
	if err != nil {
		return err
	}
	return store.withNode(ctx, record, func(transaction pgx.Tx, server nodeServerData) error {
		return nodeWriteResult(transaction.Exec(ctx, nodeOnlineSQL, server.workspaceID, string(payload)))
	})
}

func (store *postgresNodeStore) CompareAndSwapCertificate(ctx context.Context, nodeID, previous, next string, expiry time.Time) (bool, error) {
	if nodeID == "" || previous == "" {
		return false, errNodeNotFound
	}
	if next == "" || !expiry.After(time.Now()) {
		return false, errors.New("invalid renewed node certificate")
	}
	err := store.transaction(ctx, func(transaction pgx.Tx) error {
		return nodeWriteResult(transaction.Exec(ctx, swapNodeCertificateSQL, next, expiry, nodeID, previous))
	})
	return err == nil, err
}
