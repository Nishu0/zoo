package main

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type storeTestRow struct {
	values []any
	err    error
}

func (row storeTestRow) Scan(destinations ...any) error {
	if row.err != nil {
		return row.err
	}
	if len(destinations) != len(row.values) {
		return errors.New("unexpected scan destination count")
	}
	for index, destination := range destinations {
		reflect.ValueOf(destination).Elem().Set(reflect.ValueOf(row.values[index]))
	}
	return nil
}

type storeTestOperation struct {
	sql     string
	args    []any
	row     storeTestRow
	command string
	err     error
}

type storeTestTransaction struct {
	pgx.Tx
	test        *testing.T
	ctx         context.Context
	operations  []storeTestOperation
	commitError error
	commits     int
	rollbacks   int
}

func (transaction *storeTestTransaction) take(ctx context.Context, sql string, args []any) storeTestOperation {
	transaction.test.Helper()
	if ctx != transaction.ctx {
		transaction.test.Fatal("caller context not forwarded")
	}
	if len(transaction.operations) == 0 {
		transaction.test.Fatalf("unexpected SQL: %s", sql)
	}
	operation := transaction.operations[0]
	transaction.operations = transaction.operations[1:]
	if sql != operation.sql || !reflect.DeepEqual(args, operation.args) {
		transaction.test.Fatalf("SQL/arguments differ:\n%s\n%#v\nexpected:\n%s\n%#v", sql, args, operation.sql, operation.args)
	}
	return operation
}

func (transaction *storeTestTransaction) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	return transaction.take(ctx, sql, args).row
}

func (transaction *storeTestTransaction) Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error) {
	operation := transaction.take(ctx, sql, args)
	return pgconn.NewCommandTag(operation.command), operation.err
}

func (transaction *storeTestTransaction) Commit(ctx context.Context) error {
	if ctx != transaction.ctx {
		transaction.test.Fatal("commit lost caller context")
	}
	transaction.commits++
	if len(transaction.operations) != 0 {
		transaction.test.Fatal("transaction committed before all statements")
	}
	return transaction.commitError
}

func (transaction *storeTestTransaction) Rollback(ctx context.Context) error {
	if ctx != transaction.ctx {
		transaction.test.Fatal("rollback lost caller context")
	}
	transaction.rollbacks++
	if transaction.commits > 0 && transaction.commitError == nil {
		return pgx.ErrTxClosed
	}
	return nil
}

type storeTestDatabase struct {
	test        *testing.T
	ctx         context.Context
	lookup      storeTestRow
	lookupNode  string
	transaction *storeTestTransaction
	beginError  error
	begins      int
}

func (database *storeTestDatabase) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	if ctx != database.ctx || sql != lookupNodeSQL || !reflect.DeepEqual(args, []any{database.lookupNode}) {
		database.test.Fatal("lookup changed SQL, arguments or context")
	}
	return database.lookup
}

func (database *storeTestDatabase) Begin(ctx context.Context) (pgx.Tx, error) {
	if ctx != database.ctx {
		database.test.Fatal("begin lost caller context")
	}
	database.begins++
	return database.transaction, database.beginError
}

func postgresStoreFixture(test *testing.T) (*postgresNodeStore, *storeTestDatabase, nodeRecord) {
	test.Helper()
	ctx := test.Context()
	transaction := &storeTestTransaction{test: test, ctx: ctx}
	database := &storeTestDatabase{test: test, ctx: ctx, transaction: transaction, lookupNode: "node-1"}
	record := nodeRecord{NodeID: "node-1", ServerID: "server-1", Serial: "cafe", OS: "darwin", Arch: "arm64", SSHHostKey: "ssh-ed25519 pinned", ExpiresAt: time.Now().UTC().Add(time.Hour).Truncate(time.Second)}
	return &postgresNodeStore{database: database}, database, record
}

func lockStoreNode(record nodeRecord, platform, capabilities string) storeTestOperation {
	return storeTestOperation{sql: lockNodeSQL, args: []any{record.NodeID, record.ServerID, record.Serial, record.ExpiresAt}, row: storeTestRow{values: []any{"workspace-1", platform, capabilities}}}
}

func assertStoreOutcome(test *testing.T, database *storeTestDatabase, commits int) {
	test.Helper()
	if database.begins != 1 || database.transaction.commits != commits || database.transaction.rollbacks != 1 || len(database.transaction.operations) != 0 {
		test.Fatalf("transaction not drained: begins=%d commits=%d rollbacks=%d remaining=%d", database.begins, database.transaction.commits, database.transaction.rollbacks, len(database.transaction.operations))
	}
}

func TestPostgresNodeStoreLookup(test *testing.T) {
	for name, failure := range map[string]error{"registered": nil, "unknown": pgx.ErrNoRows, "canceled": context.Canceled, "database_error": errors.New("database unavailable")} {
		test.Run(name, func(test *testing.T) {
			store, database, expected := postgresStoreFixture(test)
			database.lookup = storeTestRow{values: []any{expected.NodeID, expected.ServerID, expected.Serial, expected.OS, expected.Arch, expected.SSHHostKey, expected.ExpiresAt}, err: failure}
			record, err := store.Lookup(database.ctx, expected.NodeID)
			if failure == nil {
				if err != nil || !reflect.DeepEqual(record, expected) {
					test.Fatalf("lookup: %+v %v", record, err)
				}
			} else if errors.Is(failure, pgx.ErrNoRows) {
				if !errors.Is(err, errNodeNotFound) {
					test.Fatalf("unknown node: %v", err)
				}
			} else if !errors.Is(err, failure) {
				test.Fatalf("lookup error changed: %v", err)
			}
		})
	}
}

func TestPostgresNodeStoreHelloCapabilitiesAndJSON(test *testing.T) {
	cases := []struct {
		name, platform, os, arch, driver, existing, expected string
		available, ssh                                       bool
	}{
		{"mac_available", "macos", "darwin", "arm64", "zoovm", "", "macos", true, true},
		{"windows_available", "windows", "windows", "amd64", "hyperv", "", "windows", true, true},
		{"unchanged", "macos", "darwin", "arm64", "zoovm", "macos", "macos", true, true},
		{"no_ssh", "macos", "darwin", "arm64", "zoovm", "macos", "", true, false},
		{"unavailable", "windows", "windows", "amd64", "hyperv", "windows", "", false, true},
		{"wrong_driver", "macos", "darwin", "arm64", "hyperv", "macos", "", true, true},
		{"empty_drivers", "macos", "darwin", "arm64", "", "macos", "", false, false},
	}
	for _, scenario := range cases {
		test.Run(scenario.name, func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			record.OS, record.Arch = scenario.os, scenario.arch
			hello := nodeHelloData{Version: "v1", OS: record.OS, Arch: record.Arch, Hostname: `host"name`, Drivers: []nodeDriverData{{Name: scenario.driver, Available: scenario.available, Detail: "ready"}}}
			if scenario.driver == "" {
				hello.Drivers = nil
			}
			if scenario.ssh {
				hello.Targets = []string{"ssh"}
			}
			drivers, _ := json.Marshal(hello.Drivers)
			if hello.Drivers == nil {
				drivers = []byte("[]")
			}
			targets := "[]"
			if scenario.ssh {
				targets = `["ssh"]`
			}
			database.transaction.operations = []storeTestOperation{
				lockStoreNode(record, scenario.platform, scenario.existing),
				{sql: writeNodeHelloSQL, args: []any{hello.Version, hello.OS, hello.Arch, hello.Hostname, string(drivers), targets, record.NodeID, record.ServerID, record.Serial, record.ExpiresAt}, command: "UPDATE 1"},
			}
			if scenario.expected != scenario.existing {
				database.transaction.operations = append(database.transaction.operations, storeTestOperation{sql: writeNodeCapabilitiesSQL, args: []any{scenario.expected, record.ServerID}, command: "UPDATE 1"})
			}
			if err := store.WriteHello(database.ctx, record, hello); err != nil {
				test.Fatal(err)
			}
			assertStoreOutcome(test, database, 1)
		})
	}
}

func TestPostgresNodeStoreStatusLeavesEventsToTriggers(test *testing.T) {
	for _, populated := range []bool{false, true} {
		test.Run(map[bool]string{false: "empty_arrays", true: "reported_values"}[populated], func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			report := nodeStatusData{CPUs: 8, MemoryTotal: math.MaxInt64, MemoryAvailable: 1024, DiskTotal: 4096, DiskFree: 2048, Load: 0.25}
			sandboxes, checks := "[]", "[]"
			if populated {
				report.Sandboxes, report.Checks = []string{"sandbox-1"}, []nodeCheckData{{Name: "ssh", OK: true, Detail: "ready"}}
				sandboxes, checks = `["sandbox-1"]`, `[{"name":"ssh","ok":true,"detail":"ready"}]`
			}
			database.transaction.operations = []storeTestOperation{
				lockStoreNode(record, "macos", "macos"),
				{sql: writeNodeStatusSQL, args: []any{int64(8), int64(math.MaxInt64), int64(1024), int64(4096), int64(2048), 0.25, sandboxes, checks, record.NodeID, record.ServerID, record.Serial, record.ExpiresAt}, command: "UPDATE 1"},
			}
			if err := store.WriteStatus(database.ctx, record, report); err != nil {
				test.Fatal(err)
			}
			assertStoreOutcome(test, database, 1)
		})
	}
}

func TestPostgresNodeStoreOnlineEvents(test *testing.T) {
	for _, online := range []bool{false, true} {
		test.Run(map[bool]string{false: "offline", true: "online"}[online], func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			payload, _ := json.Marshal(map[string]any{"server_id": record.ServerID, "online": online})
			var values map[string]json.RawMessage
			json.Unmarshal(payload, &values)
			expected := `{"server_id":"server-1","online":` + string(values["online"]) + `}`
			database.transaction.operations = []storeTestOperation{lockStoreNode(record, "macos", "macos"), {sql: nodeOnlineSQL, args: []any{"workspace-1", expected}, command: "SELECT 1"}}
			if err := store.SetOnline(database.ctx, record, online); err != nil {
				test.Fatal(err)
			}
			assertStoreOutcome(test, database, 1)
		})
	}
}

func TestPostgresNodeStoreRejectsRevokedWrites(test *testing.T) {
	methods := map[string]func(*postgresNodeStore, context.Context, nodeRecord) error{
		"hello": func(store *postgresNodeStore, ctx context.Context, record nodeRecord) error {
			return store.WriteHello(ctx, record, nodeHelloData{OS: record.OS, Arch: record.Arch})
		},
		"status": func(store *postgresNodeStore, ctx context.Context, record nodeRecord) error {
			return store.WriteStatus(ctx, record, nodeStatusData{})
		},
		"online": func(store *postgresNodeStore, ctx context.Context, record nodeRecord) error {
			return store.SetOnline(ctx, record, true)
		},
	}
	for name, write := range methods {
		test.Run(name, func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			locked := lockStoreNode(record, "macos", "")
			locked.row.err = pgx.ErrNoRows
			database.transaction.operations = []storeTestOperation{locked}
			if err := write(store, database.ctx, record); !errors.Is(err, errNodeNotFound) {
				test.Fatalf("stale certificate accepted: %v", err)
			}
			assertStoreOutcome(test, database, 0)
		})
	}
}

func TestPostgresNodeStoreHelloRollsBackBothWrites(test *testing.T) {
	for name, failure := range map[string]error{"deleted_server": nil, "capabilities_failure": errors.New("write failed")} {
		test.Run(name, func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			hello := nodeHelloData{Version: "v1", OS: record.OS, Arch: record.Arch, Drivers: []nodeDriverData{{Name: "zoovm", Available: true}}, Targets: []string{"ssh"}}
			drivers, _ := json.Marshal(hello.Drivers)
			database.transaction.operations = []storeTestOperation{
				lockStoreNode(record, "macos", ""),
				{sql: writeNodeHelloSQL, args: []any{hello.Version, hello.OS, hello.Arch, "", string(drivers), `["ssh"]`, record.NodeID, record.ServerID, record.Serial, record.ExpiresAt}, command: "UPDATE 1"},
				{sql: writeNodeCapabilitiesSQL, args: []any{"macos", record.ServerID}, command: "UPDATE 0", err: failure},
			}
			err := store.WriteHello(database.ctx, record, hello)
			if failure != nil && !errors.Is(err, failure) || failure == nil && !errors.Is(err, errNodeNotFound) {
				test.Fatalf("partial update error: %v", err)
			}
			assertStoreOutcome(test, database, 0)
		})
	}
}

func TestPostgresNodeStoreCertificateCAS(test *testing.T) {
	for name, command := range map[string]string{"renewed": "UPDATE 1", "revoked_or_replaced": "UPDATE 0", "unexpected_count": "UPDATE 2"} {
		test.Run(name, func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			expiry := record.ExpiresAt.Add(time.Hour)
			database.transaction.operations = []storeTestOperation{{sql: swapNodeCertificateSQL, args: []any{"new-serial", expiry, record.NodeID, record.Serial}, command: command}}
			updated, err := store.CompareAndSwapCertificate(database.ctx, record.NodeID, record.Serial, "new-serial", expiry)
			if command == "UPDATE 1" {
				if err != nil || !updated {
					test.Fatalf("renewal: %v %v", updated, err)
				}
				assertStoreOutcome(test, database, 1)
			} else {
				if err == nil || updated {
					test.Fatalf("failed CAS issued a certificate: %v %v", updated, err)
				}
				if command == "UPDATE 0" && !errors.Is(err, errNodeNotFound) {
					test.Fatalf("revoked CAS: %v", err)
				}
				assertStoreOutcome(test, database, 0)
			}
		})
	}
}

func TestPostgresNodeStoreZeroRowsRollBackStatusAndOnline(test *testing.T) {
	for _, online := range []bool{false, true} {
		test.Run(map[bool]string{false: "status", true: "online"}[online], func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			operation := storeTestOperation{sql: writeNodeStatusSQL, args: []any{int64(0), int64(0), int64(0), int64(0), int64(0), float64(0), "[]", "[]", record.NodeID, record.ServerID, record.Serial, record.ExpiresAt}, command: "UPDATE 0"}
			if online {
				operation = storeTestOperation{sql: nodeOnlineSQL, args: []any{"workspace-1", `{"server_id":"server-1","online":true}`}, command: "SELECT 0"}
			}
			database.transaction.operations = []storeTestOperation{lockStoreNode(record, "macos", ""), operation}
			var err error
			if online {
				err = store.SetOnline(database.ctx, record, true)
			} else {
				err = store.WriteStatus(database.ctx, record, nodeStatusData{})
			}
			if !errors.Is(err, errNodeNotFound) {
				test.Fatalf("zero rows accepted: %v", err)
			}
			assertStoreOutcome(test, database, 0)
		})
	}
}

func TestPostgresNodeStoreCertificateCASCommitFailure(test *testing.T) {
	store, database, record := postgresStoreFixture(test)
	database.transaction.commitError = context.Canceled
	database.transaction.operations = []storeTestOperation{{sql: swapNodeCertificateSQL, args: []any{"new-serial", record.ExpiresAt, record.NodeID, record.Serial}, command: "UPDATE 1"}}
	updated, err := store.CompareAndSwapCertificate(database.ctx, record.NodeID, record.Serial, "new-serial", record.ExpiresAt)
	if updated || !errors.Is(err, context.Canceled) {
		test.Fatalf("uncommitted certificate accepted: %v %v", updated, err)
	}
	assertStoreOutcome(test, database, 1)
}

func TestPostgresNodeStoreContextAndTransactionFailures(test *testing.T) {
	for _, stage := range []string{"begin", "lock", "write", "commit"} {
		test.Run(stage, func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			failure := context.DeadlineExceeded
			locked := lockStoreNode(record, "macos", "")
			written := storeTestOperation{sql: writeNodeStatusSQL, args: []any{int64(0), int64(0), int64(0), int64(0), int64(0), float64(0), "[]", "[]", record.NodeID, record.ServerID, record.Serial, record.ExpiresAt}, command: "UPDATE 1"}
			switch stage {
			case "begin":
				database.beginError = failure
			case "lock":
				locked.row.err = failure
				database.transaction.operations = []storeTestOperation{locked}
			case "write":
				written.err = failure
				database.transaction.operations = []storeTestOperation{locked, written}
			case "commit":
				database.transaction.commitError = failure
				database.transaction.operations = []storeTestOperation{locked, written}
			}
			if err := store.WriteStatus(database.ctx, record, nodeStatusData{}); !errors.Is(err, failure) {
				test.Fatalf("caller failure lost: %v", err)
			}
			if stage == "begin" {
				if database.transaction.commits != 0 || database.transaction.rollbacks != 0 {
					test.Fatal("used failed transaction")
				}
			} else {
				commits := 0
				if stage == "commit" {
					commits = 1
				}
				assertStoreOutcome(test, database, commits)
			}
		})
	}
}

func TestPostgresNodeStoreValidatesCapacityAndRegistration(test *testing.T) {
	for name, report := range map[string]nodeStatusData{
		"memory_total": {MemoryTotal: math.MaxUint64}, "memory_available": {MemoryAvailable: math.MaxUint64},
		"disk_total": {DiskTotal: math.MaxUint64}, "disk_free": {DiskFree: math.MaxUint64},
	} {
		test.Run(name, func(test *testing.T) {
			store, database, record := postgresStoreFixture(test)
			if err := store.WriteStatus(database.ctx, record, report); err == nil || database.begins != 0 {
				test.Fatalf("unsigned BIGINT overflow accepted: %v", err)
			}
		})
	}
	test.Run("mismatched_hello", func(test *testing.T) {
		store, database, record := postgresStoreFixture(test)
		if err := store.WriteHello(database.ctx, record, nodeHelloData{OS: "windows", Arch: "amd64"}); !errors.Is(err, errNodeNotFound) || database.begins != 0 {
			test.Fatalf("registration changed: %v", err)
		}
	})
}

func TestPostgresNodeStoreSQLGuardsAndTriggerContract(test *testing.T) {
	for name, sql := range map[string]string{"lookup": lookupNodeSQL, "lock": lockNodeSQL, "hello": writeNodeHelloSQL, "status": writeNodeStatusSQL, "renewal": swapNodeCertificateSQL} {
		test.Run(name, func(test *testing.T) {
			where := strings.Split(sql, "WHERE")[1]
			if !strings.Contains(where, "serial") || !strings.Contains(where, "cert_expires_at > CURRENT_TIMESTAMP") {
				test.Fatal("certificate guard missing")
			}
			if name != "lookup" && (!strings.Contains(where, "id = $") || !strings.Contains(where, "serial = $")) {
				test.Fatal("write authorization is not parameterized")
			}
		})
	}
	if !strings.Contains(lockNodeSQL, "FOR UPDATE OF s, n") {
		test.Fatal("certificate and server are not locked together")
	}
	if strings.Contains(writeNodeHelloSQL, "zoo_event") || strings.Contains(writeNodeStatusSQL, "zoo_event") || strings.Contains(writeNodeStatusSQL, "pg_notify") {
		test.Fatal("report writes duplicate trigger events")
	}
	if !strings.Contains(nodeOnlineSQL, "zoo_event") || !strings.Contains(nodeOnlineSQL, "'server.state'") || strings.Contains(nodeOnlineSQL, "UPDATE nodes") {
		test.Fatal("online event contract changed")
	}
}
