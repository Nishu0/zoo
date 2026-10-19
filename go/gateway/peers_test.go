package main

import (
	"context"
	"errors"
	"fmt"
	"net/netip"
	"os"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type peerDNSResult struct {
	addresses []netip.Addr
	err       error
}

type fakePeerResolver struct {
	mu      sync.Mutex
	results map[string]peerDNSResult
	calls   int
	block   bool
	entered chan struct{}
}

func peerAddresses(values ...string) []netip.Addr {
	addresses := make([]netip.Addr, len(values))
	for index, value := range values {
		addresses[index] = netip.MustParseAddr(value)
	}
	return addresses
}

func (resolver *fakePeerResolver) set(hostname string, addresses []netip.Addr, err error) {
	resolver.mu.Lock()
	defer resolver.mu.Unlock()
	if resolver.results == nil {
		resolver.results = make(map[string]peerDNSResult)
	}
	resolver.results[hostname] = peerDNSResult{addresses: slices.Clone(addresses), err: err}
}

func (resolver *fakePeerResolver) LookupNetIP(ctx context.Context, network, hostname string) ([]netip.Addr, error) {
	if network != "ip" {
		return nil, fmt.Errorf("unexpected lookup network %q", network)
	}
	resolver.mu.Lock()
	resolver.calls++
	result, exists := resolver.results[hostname]
	blocked, entered := resolver.block, resolver.entered
	resolver.mu.Unlock()
	if blocked {
		if entered != nil {
			select {
			case entered <- struct{}{}:
			default:
			}
		}
		<-ctx.Done()
		return nil, ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if !exists {
		return nil, fmt.Errorf("unexpected hostname %q", hostname)
	}
	return slices.Clone(result.addresses), result.err
}

func testPeerSet(test *testing.T, values ...string) (*peerSet, *fakePeerResolver) {
	test.Helper()
	resolver := &fakePeerResolver{}
	resolver.set("gateway.test", peerAddresses(values...), nil)
	peers, err := newPeerSet(context.Background(), "gateway.test", values[0], resolver)
	if err != nil {
		test.Fatal(err)
	}
	test.Cleanup(peers.Close)
	return peers, resolver
}

func TestNormalizePeerIPs(test *testing.T) {
	addresses, err := normalizePeerIPs(peerAddresses("2001:0db8:0:0:0:0:0:2", "::ffff:10.0.0.2", "10.0.0.1", "10.0.0.2", "2001:db8::2"))
	if err != nil {
		test.Fatal(err)
	}
	expected := peerAddresses("10.0.0.1", "10.0.0.2", "2001:db8::2")
	if !slices.Equal(addresses, expected) {
		test.Fatalf("normalized addresses = %v, want %v", addresses, expected)
	}
	for _, invalid := range []string{"", "0.0.0.0", "::", "224.0.0.1", "ff02::1", "fe80::1%eth0"} {
		test.Run(invalid, func(test *testing.T) {
			var addresses []netip.Addr
			if invalid != "" {
				addresses = peerAddresses(invalid)
			}
			if _, err := normalizePeerIPs(addresses); err == nil {
				test.Fatal("accepted invalid membership")
			}
		})
	}
	if _, err := normalizePeerIPs([]netip.Addr{{}}); err == nil {
		test.Fatal("accepted invalid address")
	}
}

func TestRendezvousCanonicalVectors(test *testing.T) {
	members := peerAddresses("10.0.0.1", "10.0.0.2", "2001:db8::1")
	vectors := map[string]string{
		"sandbox-1":                            "10.0.0.1",
		"server-1":                             "10.0.0.2",
		"sandbox-α":                            "2001:db8::1",
		"00000000-0000-0000-0000-000000000000": "10.0.0.2",
	}
	for resourceID, expected := range vectors {
		test.Run(resourceID, func(test *testing.T) {
			if owner := rendezvousOwner(members, resourceID).String(); owner != expected {
				test.Fatalf("owner = %s, want %s", owner, expected)
			}
		})
	}
	if owner := rendezvousOwner(peerAddresses("::ffff:10.0.0.1", "2001:0db8::1", "10.0.0.2"), "sandbox-1").String(); owner != vectors["sandbox-1"] {
		test.Fatalf("noncanonical owner = %s", owner)
	}
}

func TestRendezvousStableMembershipChanges(test *testing.T) {
	initial := peerAddresses("10.0.0.1", "10.0.0.2", "2001:db8::1")
	reordered := peerAddresses("2001:0db8::1", "::ffff:10.0.0.2", "10.0.0.1", "10.0.0.2")
	added := append(slices.Clone(initial), netip.MustParseAddr("2001:db8::2"))
	removed := peerAddresses("10.0.0.1", "2001:db8::1")
	addedChanges, removedChanges := 0, 0
	for index := range 1000 {
		resourceID := fmt.Sprintf("resource-%d", index)
		owner := rendezvousOwner(initial, resourceID)
		if reorderedOwner := rendezvousOwner(reordered, resourceID); owner != reorderedOwner {
			test.Fatalf("reordering moved %s", resourceID)
		}
		if addedOwner := rendezvousOwner(added, resourceID); owner != addedOwner {
			addedChanges++
			if addedOwner.String() != "2001:db8::2" {
				test.Fatalf("addition moved resource between existing peers: %s", resourceID)
			}
		}
		if removedOwner := rendezvousOwner(removed, resourceID); owner != removedOwner {
			removedChanges++
			if owner.String() != "10.0.0.2" {
				test.Fatalf("removal moved surviving peer's resource: %s", resourceID)
			}
		}
	}
	if addedChanges == 0 || removedChanges == 0 {
		test.Fatal("membership changes moved no resources")
	}
}

func TestPeerSetConfiguration(test *testing.T) {
	for _, configuration := range []struct {
		name, hostname, localIP string
		members                 []string
	}{
		{name: "missing DNS", localIP: "10.0.0.1", members: []string{"10.0.0.1"}},
		{name: "whitespace DNS", hostname: " gateway.test", localIP: "10.0.0.1", members: []string{"10.0.0.1"}},
		{name: "invalid local", hostname: "gateway.test", localIP: "not-an-ip", members: []string{"10.0.0.1"}},
		{name: "unspecified local", hostname: "gateway.test", localIP: "::", members: []string{"10.0.0.1"}},
		{name: "absent local", hostname: "gateway.test", localIP: "10.0.0.2", members: []string{"10.0.0.1"}},
		{name: "empty membership", hostname: "gateway.test", localIP: "10.0.0.1"},
	} {
		test.Run(configuration.name, func(test *testing.T) {
			resolver := &fakePeerResolver{}
			resolver.set(configuration.hostname, peerAddresses(configuration.members...), nil)
			if peers, err := newPeerSet(context.Background(), configuration.hostname, configuration.localIP, resolver); err == nil {
				peers.Close()
				test.Fatal("accepted invalid configuration")
			}
		})
	}
	for _, localIP := range []string{"127.0.0.1", "::1", "::ffff:10.0.0.1", "2001:0db8::1"} {
		test.Run(localIP, func(test *testing.T) {
			peers, _ := testPeerSet(test, localIP)
			expected := netip.MustParseAddr(localIP).Unmap().String()
			if peers.LocalIP() != expected {
				test.Fatalf("local = %s, want %s", peers.LocalIP(), expected)
			}
			if owner, err := peers.IsOwner("resource"); err != nil || !owner {
				test.Fatalf("local ownership = %v, %v", owner, err)
			}
		})
	}
}

func TestPeerSetInferLocalIP(test *testing.T) {
	localHostname, err := os.Hostname()
	if err != nil {
		test.Fatal(err)
	}
	for _, configuration := range []struct {
		name           string
		local, members []string
		lookupError    error
		expected       string
	}{
		{name: "unique intersection", local: []string{"192.0.2.10", "::ffff:10.0.0.1"}, members: []string{"10.0.0.1", "10.0.0.2"}, expected: "10.0.0.1"},
		{name: "IPv6", local: []string{"2001:0db8::1"}, members: []string{"2001:db8::1"}, expected: "2001:db8::1"},
		{name: "ambiguous", local: []string{"10.0.0.1", "10.0.0.2"}, members: []string{"10.0.0.1", "10.0.0.2"}},
		{name: "no match", local: []string{"192.0.2.10"}, members: []string{"10.0.0.1"}},
		{name: "loopback needs explicit identity", local: []string{"127.0.0.1"}, members: []string{"127.0.0.1"}},
		{name: "lookup failure", members: []string{"10.0.0.1"}, lookupError: errors.New("local DNS unavailable")},
	} {
		test.Run(configuration.name, func(test *testing.T) {
			resolver := &fakePeerResolver{}
			resolver.set("gateway.test", peerAddresses(configuration.members...), nil)
			resolver.set(localHostname, peerAddresses(configuration.local...), configuration.lookupError)
			peers, err := newPeerSet(context.Background(), "gateway.test", "", resolver)
			if configuration.expected == "" {
				if err == nil {
					peers.Close()
					test.Fatal("inferred ambiguous local identity")
				}
				if !strings.Contains(err.Error(), "ZOO_GATEWAY_IP") {
					test.Fatalf("missing configuration hint: %v", err)
				}
				return
			}
			if err != nil {
				test.Fatal(err)
			}
			test.Cleanup(peers.Close)
			if peers.LocalIP() != configuration.expected {
				test.Fatalf("local = %s, want %s", peers.LocalIP(), configuration.expected)
			}
		})
	}
}

func TestPeerSetDNSFailureFailsClosedWithoutHandoff(test *testing.T) {
	peers, resolver := testPeerSet(test, "10.0.0.1", "10.0.0.2")
	var callbacks atomic.Int64
	peers.OnOwnerChanges(func() { callbacks.Add(1) })
	lookupError := errors.New("DNS unavailable")
	resolver.set("gateway.test", nil, lookupError)
	if err := peers.Refresh(context.Background()); !errors.Is(err, lookupError) || !errors.Is(err, errPeerMembershipUnavailable) {
		test.Fatalf("refresh error = %v", err)
	}
	if err := peers.Ready(); !errors.Is(err, errPeerMembershipUnavailable) {
		test.Fatalf("ready error = %v", err)
	}
	if owner, err := peers.Owner("resource"); owner != "" || !errors.Is(err, errPeerMembershipUnavailable) {
		test.Fatalf("stale owner = %s, %v", owner, err)
	}
	if owned, err := peers.IsOwner("resource"); owned || !errors.Is(err, errPeerMembershipUnavailable) {
		test.Fatalf("stale ownership = %v, %v", owned, err)
	}
	if members, err := peers.Members(); members != nil || !errors.Is(err, errPeerMembershipUnavailable) {
		test.Fatalf("stale membership = %v, %v", members, err)
	}
	if callbacks.Load() != 0 {
		test.Fatal("DNS error triggered handoff")
	}
	resolver.set("gateway.test", peerAddresses("::ffff:10.0.0.2", "10.0.0.1", "10.0.0.2"), nil)
	if err := peers.Refresh(context.Background()); err != nil {
		test.Fatal(err)
	}
	if err := peers.Ready(); err != nil {
		test.Fatal(err)
	}
	if callbacks.Load() != 0 {
		test.Fatal("same-set recovery triggered handoff")
	}
	for _, addresses := range [][]netip.Addr{nil, peerAddresses("0.0.0.0"), peerAddresses("ff02::1")} {
		resolver.set("gateway.test", addresses, nil)
		if err := peers.Refresh(context.Background()); !errors.Is(err, errPeerMembershipUnavailable) {
			test.Fatalf("invalid DNS refresh = %v", err)
		}
		if callbacks.Load() != 0 {
			test.Fatal("invalid DNS triggered handoff")
		}
	}
	resolver.set("gateway.test", peerAddresses("10.0.0.1", "2001:db8::1"), nil)
	if err := peers.Refresh(context.Background()); err != nil {
		test.Fatal(err)
	}
	if callbacks.Load() != 1 {
		test.Fatal("confirmed change did not trigger handoff")
	}
}

func TestPeerSetOwnerChangesCloseOnlyMovedSessions(test *testing.T) {
	peers, resolver := testPeerSet(test, "10.0.0.1")
	sessions := make(map[string]bool)
	for index := range 1000 {
		sessions[fmt.Sprintf("resource-%d", index)] = true
	}
	callbacks := 0
	peers.OnOwnerChanges(func() {
		callbacks++
		if err := peers.Ready(); err != nil {
			test.Errorf("callback membership unavailable: %v", err)
		}
		for resourceID, connected := range sessions {
			owner, err := peers.IsOwner(resourceID)
			if err != nil {
				test.Error(err)
			}
			if connected && !owner {
				sessions[resourceID] = false
			}
		}
	})
	resolver.set("gateway.test", peerAddresses("10.0.0.1", "2001:db8::1"), nil)
	if err := peers.Refresh(context.Background()); err != nil {
		test.Fatal(err)
	}
	connected := 0
	for resourceID, open := range sessions {
		owner, err := peers.IsOwner(resourceID)
		if err != nil || owner != open {
			test.Fatalf("session %s state = %v, ownership = %v, %v", resourceID, open, owner, err)
		}
		if open {
			connected++
		}
	}
	if connected == 0 || connected == len(sessions) {
		test.Fatalf("addition kept %d of %d sessions", connected, len(sessions))
	}
	resolver.set("gateway.test", peerAddresses("2001:db8::1"), nil)
	if err := peers.Refresh(context.Background()); err != nil {
		test.Fatal(err)
	}
	for resourceID, open := range sessions {
		if open {
			test.Fatalf("removed local peer retained session %s", resourceID)
		}
	}
	if callbacks != 2 {
		test.Fatalf("callbacks = %d, want 2", callbacks)
	}
	if peers.LocalIP() != "10.0.0.1" {
		test.Fatal("local identity changed after removal")
	}
}

func TestPeerSetMembersAreIndependentCopy(test *testing.T) {
	peers, _ := testPeerSet(test, "10.0.0.2", "10.0.0.1", "2001:db8::1")
	members, err := peers.Members()
	if err != nil {
		test.Fatal(err)
	}
	if !slices.Equal(members, []string{"10.0.0.1", "10.0.0.2", "2001:db8::1"}) {
		test.Fatalf("members = %v", members)
	}
	members[0] = "modified"
	updated, err := peers.Members()
	if err != nil || updated[0] != "10.0.0.1" {
		test.Fatalf("mutated internal membership: %v, %v", updated, err)
	}
	if _, err := peers.Owner(""); err == nil {
		test.Fatal("accepted empty resource ID")
	}
}

func TestPeerSetMonitorRefreshesAndStops(test *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	resolver := &fakePeerResolver{}
	resolver.set("gateway.test", peerAddresses("10.0.0.1"), nil)
	peers, err := newPeerSet(ctx, "gateway.test", "10.0.0.1", resolver)
	if err != nil {
		test.Fatal(err)
	}
	test.Cleanup(peers.Close)
	updated := make(chan struct{}, 1)
	peers.OnOwnerChanges(func() { cancel(); updated <- struct{}{} })
	resolver.set("gateway.test", peerAddresses("10.0.0.1", "10.0.0.2"), nil)
	select {
	case <-updated:
	case <-time.After(peerRefreshInterval + 2*time.Second):
		test.Fatal("monitor did not refresh DNS")
	}
	select {
	case <-peers.done:
	case <-time.After(time.Second):
		test.Fatal("monitor did not stop on cancellation")
	}
	if err := peers.Ready(); !errors.Is(err, context.Canceled) {
		test.Fatalf("closed readiness = %v", err)
	}
	resolver.mu.Lock()
	calls := resolver.calls
	resolver.mu.Unlock()
	if calls != 2 {
		test.Fatalf("DNS calls = %d, want initial lookup and one refresh", calls)
	}
	if peerRefreshInterval != 5*time.Second {
		test.Fatalf("refresh interval = %v", peerRefreshInterval)
	}
}

func TestPeerSetCloseCancelsInFlightLookup(test *testing.T) {
	peers, resolver := testPeerSet(test, "10.0.0.1")
	resolver.mu.Lock()
	resolver.block, resolver.entered = true, make(chan struct{}, 1)
	entered := resolver.entered
	resolver.mu.Unlock()
	result := make(chan error, 1)
	go func() { result <- peers.Refresh(context.Background()) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		test.Fatal("lookup did not begin")
	}
	closed := make(chan struct{})
	go func() { peers.Close(); close(closed) }()
	select {
	case <-closed:
	case <-time.After(time.Second):
		test.Fatal("Close stranded monitor")
	}
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) || !errors.Is(err, errPeerMembershipUnavailable) {
			test.Fatalf("canceled lookup = %v", err)
		}
	case <-time.After(time.Second):
		test.Fatal("Close stranded DNS transport")
	}
	if _, err := peers.Owner("resource"); !errors.Is(err, context.Canceled) {
		test.Fatalf("closed ownership = %v", err)
	}
	if _, err := peers.Members(); !errors.Is(err, context.Canceled) {
		test.Fatalf("closed membership = %v", err)
	}
}

func TestPeerSetCallbackCanRefresh(test *testing.T) {
	peers, resolver := testPeerSet(test, "10.0.0.1")
	peers.OnOwnerChanges(func() {
		if err := peers.Refresh(context.Background()); err != nil {
			test.Error(err)
		}
	})
	resolver.set("gateway.test", peerAddresses("10.0.0.1", "10.0.0.2"), nil)
	result := make(chan error, 1)
	go func() { result <- peers.Refresh(context.Background()) }()
	select {
	case err := <-result:
		if err != nil {
			test.Fatal(err)
		}
	case <-time.After(time.Second):
		test.Fatal("callback ran under refresh lock")
	}
}

func TestPeerSetConcurrentReadersAndRefresh(test *testing.T) {
	peers, resolver := testPeerSet(test, "10.0.0.1")
	var readers sync.WaitGroup
	for range 8 {
		readers.Go(func() {
			for index := range 1000 {
				owner, err := peers.Owner(fmt.Sprintf("resource-%d", index))
				if err != nil || owner == "" {
					test.Errorf("concurrent owner = %s, %v", owner, err)
					return
				}
				if _, err := peers.Members(); err != nil {
					test.Error(err)
					return
				}
			}
		})
	}
	for index := range 50 {
		addresses := peerAddresses("10.0.0.1")
		if index%2 == 0 {
			addresses = append(addresses, netip.MustParseAddr("2001:db8::1"))
		}
		resolver.set("gateway.test", addresses, nil)
		if err := peers.Refresh(context.Background()); err != nil {
			test.Fatal(err)
		}
	}
	readers.Wait()
}
