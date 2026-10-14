package main

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

const screenSessionLimit = 1024

type screenEntry struct {
	ready     chan struct{}
	cancel    context.CancelFunc
	record    vncTargetRecord
	session   *VNCSession
	err       error
	diffLock  chan struct{}
	baselines map[string]uint64
	viewers   atomic.Int32
}

type screenService struct {
	service  *gatewayServer
	resolver *vncTargetResolver
	ctx      context.Context
	cancel   context.CancelFunc
	mu       sync.Mutex
	entries  map[string]*screenEntry
	closed   bool
	done     chan struct{}
	opening  sync.WaitGroup
}

func newScreenService(service *gatewayServer, pool *pgxpool.Pool) *screenService {
	ctx, cancel := context.WithCancel(context.Background())
	screens := &screenService{service: service, resolver: &vncTargetResolver{database: pool, service: service},
		ctx: ctx, cancel: cancel, entries: make(map[string]*screenEntry), done: make(chan struct{})}
	go screens.watch()
	return screens
}

func (screens *screenService) acquire(ctx context.Context, sandboxID string) (*screenEntry, error) {
	if len(sandboxID) > 256 {
		return nil, status.Error(codes.InvalidArgument, "sandbox ID is too long")
	}
	if err := screens.service.routes.requireOwner(ctx, sandboxID); err != nil {
		return nil, err
	}
	screens.mu.Lock()
	if screens.closed {
		screens.mu.Unlock()
		return nil, status.Error(codes.Unavailable, "gateway screen service is shutting down")
	}
	entry := screens.entries[sandboxID]
	if entry == nil {
		if len(screens.entries) >= screenSessionLimit {
			screens.mu.Unlock()
			return nil, status.Error(codes.ResourceExhausted, "gateway screen session capacity exceeded")
		}
		lifetime, cancel := context.WithCancel(screens.ctx)
		entry = &screenEntry{ready: make(chan struct{}), cancel: cancel, diffLock: make(chan struct{}, 1), baselines: make(map[string]uint64)}
		entry.diffLock <- struct{}{}
		screens.entries[sandboxID] = entry
		screens.opening.Add(1)
		go screens.open(lifetime, sandboxID, entry)
	}
	screens.mu.Unlock()
	select {
	case <-ctx.Done():
		return nil, status.FromContextError(ctx.Err()).Err()
	case <-entry.ready:
		if entry.err != nil {
			return nil, screenTransportError(entry.err)
		}
		if err := entry.session.Touch(); err != nil {
			screens.drop(sandboxID, entry)
			return nil, screenTransportError(err)
		}
		return entry, nil
	}
}

func (screens *screenService) open(ctx context.Context, sandboxID string, entry *screenEntry) {
	defer screens.opening.Done()
	deadline := time.AfterFunc(30*time.Second, entry.cancel)
	record, err := screens.resolver.lookup(ctx, sandboxID)
	var session *VNCSession
	if err == nil {
		transport, password, failure := screens.resolver.open(ctx, record)
		err = failure
		if err == nil {
			session, err = NewVNCSession(ctx, transport, password)
			if err != nil {
				transport.Close()
			}
		}
	}
	deadline.Stop()
	if err == nil {
		err = ctx.Err()
	}
	screens.mu.Lock()
	if screens.closed || screens.entries[sandboxID] != entry {
		err = status.Error(codes.Canceled, "screen session was closed while opening")
	}
	entry.record, entry.session, entry.err = record, session, err
	if err != nil && screens.entries[sandboxID] == entry {
		delete(screens.entries, sandboxID)
	}
	close(entry.ready)
	screens.mu.Unlock()
	if err != nil {
		entry.cancel()
		if session != nil {
			session.Close()
		}
		return
	}
	go func() {
		<-session.Done()
		screens.drop(sandboxID, entry)
	}()
}

func (screens *screenService) drop(sandboxID string, entry *screenEntry) {
	screens.mu.Lock()
	if screens.entries[sandboxID] == entry {
		delete(screens.entries, sandboxID)
	}
	screens.mu.Unlock()
	entry.cancel()
	select {
	case <-entry.ready:
		if entry.session != nil {
			entry.session.Close()
		}
	default:
	}
}

func (screens *screenService) closeSandbox(sandboxID string) {
	screens.mu.Lock()
	entry := screens.entries[sandboxID]
	screens.mu.Unlock()
	if entry != nil {
		screens.drop(sandboxID, entry)
	}
}

func (screens *screenService) closeMoved() {
	screens.mu.Lock()
	entries := make(map[string]*screenEntry, len(screens.entries))
	for sandboxID, entry := range screens.entries {
		entries[sandboxID] = entry
	}
	screens.mu.Unlock()
	for sandboxID, entry := range entries {
		if err := screens.service.routes.requireOwner(screens.ctx, sandboxID); err != nil {
			screens.drop(sandboxID, entry)
		}
	}
}

func (screens *screenService) close() {
	screens.mu.Lock()
	screens.closed = true
	entries := screens.entries
	screens.entries = make(map[string]*screenEntry)
	screens.mu.Unlock()
	screens.cancel()
	for sandboxID, entry := range entries {
		screens.drop(sandboxID, entry)
	}
	<-screens.done
	screens.opening.Wait()
}

func (screens *screenService) watch() {
	defer close(screens.done)
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-screens.ctx.Done():
			return
		case <-ticker.C:
		}
		screens.mu.Lock()
		entries := make(map[string]*screenEntry, len(screens.entries))
		for sandboxID, entry := range screens.entries {
			select {
			case <-entry.ready:
				entries[sandboxID] = entry
			default:
			}
		}
		screens.mu.Unlock()
		for sandboxID, entry := range entries {
			ctx, cancel := context.WithTimeout(screens.ctx, 3*time.Second)
			err := screens.service.routes.requireOwner(ctx, sandboxID)
			if err == nil {
				current, failure := screens.resolver.lookup(ctx, sandboxID)
				if failure != nil || !entry.record.sameTarget(current) {
					err = status.Error(codes.Unavailable, "sandbox screen placement changed")
				}
			}
			cancel()
			if err != nil {
				screens.drop(sandboxID, entry)
			}
		}
	}
}
