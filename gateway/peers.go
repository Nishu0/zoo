package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"os"
	"slices"
	"strings"
	"sync"
	"time"
)

const peerRefreshInterval = 5 * time.Second

var errPeerMembershipUnavailable = errors.New("gateway peer membership is unavailable")

type peerResolver interface {
	LookupNetIP(context.Context, string, string) ([]netip.Addr, error)
}

type peerSet struct {
	mu           sync.RWMutex
	refreshMu    sync.Mutex
	hostname     string
	localIP      netip.Addr
	resolver     peerResolver
	members      []netip.Addr
	failure      error
	ownerChanges func()
	ctx          context.Context
	cancel       context.CancelFunc
	done         chan struct{}
}

func normalizePeerIPs(addresses []netip.Addr) ([]netip.Addr, error) {
	if len(addresses) == 0 {
		return nil, errors.New("peer DNS returned no addresses")
	}
	normalized := make([]netip.Addr, 0, len(addresses))
	for _, address := range addresses {
		address = address.Unmap()
		if !address.IsValid() || address.IsUnspecified() || address.IsMulticast() || address.Zone() != "" {
			return nil, fmt.Errorf("invalid gateway peer IP %q", address)
		}
		normalized = append(normalized, address)
	}
	slices.SortFunc(normalized, func(first, second netip.Addr) int { return strings.Compare(first.String(), second.String()) })
	return slices.Compact(normalized), nil
}

func rendezvousOwner(members []netip.Addr, resourceID string) netip.Addr {
	var owner netip.Addr
	var highest [sha256.Size]byte
	for _, member := range members {
		member = member.Unmap()
		digest := sha256.Sum256([]byte(member.String() + "\x00" + resourceID))
		if !owner.IsValid() || bytes.Compare(digest[:], highest[:]) > 0 {
			owner, highest = member, digest
		}
	}
	return owner
}

func newPeerSet(ctx context.Context, hostname, localIP string, resolver peerResolver) (*peerSet, error) {
	if hostname == "" || strings.TrimSpace(hostname) != hostname {
		return nil, errors.New("ZOO_GATEWAY_PEERS must be the gateway peer DNS name or explicit loopback address")
	}
	var local netip.Addr
	if localIP != "" {
		parsed, err := netip.ParseAddr(localIP)
		if err != nil {
			return nil, fmt.Errorf("ZOO_GATEWAY_IP must be an IP address: %w", err)
		}
		addresses, err := normalizePeerIPs([]netip.Addr{parsed})
		if err != nil {
			return nil, err
		}
		local = addresses[0]
	}
	if resolver == nil {
		resolver = net.DefaultResolver
	}
	lifecycle, cancel := context.WithCancel(ctx)
	peers := &peerSet{hostname: hostname, resolver: resolver, localIP: local, ctx: lifecycle, cancel: cancel, done: make(chan struct{}), failure: errPeerMembershipUnavailable}
	if err := peers.Refresh(lifecycle); err != nil {
		cancel()
		return nil, err
	}
	if !local.IsValid() {
		inferred, err := peers.inferLocalIP(lifecycle)
		if err != nil {
			cancel()
			return nil, err
		}
		peers.localIP = inferred
	} else if !slices.Contains(peers.members, local) {
		cancel()
		return nil, fmt.Errorf("ZOO_GATEWAY_IP %s is absent from peer DNS %s", local, hostname)
	}
	go peers.monitor()
	return peers, nil
}

func (peers *peerSet) inferLocalIP(ctx context.Context) (netip.Addr, error) {
	hostname, err := os.Hostname()
	if err != nil {
		return netip.Addr{}, fmt.Errorf("cannot infer gateway IP; set ZOO_GATEWAY_IP: %w", err)
	}
	lookup, cancel := context.WithTimeout(ctx, peerRefreshInterval)
	defer cancel()
	addresses, err := peers.resolver.LookupNetIP(lookup, "ip", hostname)
	if err != nil {
		return netip.Addr{}, fmt.Errorf("cannot resolve local hostname; set ZOO_GATEWAY_IP: %w", err)
	}
	addresses, err = normalizePeerIPs(addresses)
	if err != nil {
		return netip.Addr{}, fmt.Errorf("cannot infer gateway IP; set ZOO_GATEWAY_IP: %w", err)
	}
	candidates := make([]netip.Addr, 0, 1)
	for _, address := range addresses {
		if !address.IsLoopback() && slices.Contains(peers.members, address) {
			candidates = append(candidates, address)
		}
	}
	if len(candidates) != 1 {
		return netip.Addr{}, errors.New("local hostname must identify one peer IP; set ZOO_GATEWAY_IP explicitly, including for loopback")
	}
	return candidates[0], nil
}

func (peers *peerSet) monitor() {
	defer close(peers.done)
	ticker := time.NewTicker(peerRefreshInterval)
	defer ticker.Stop()
	for {
		select {
		case <-peers.ctx.Done():
			return
		case <-ticker.C:
			peers.Refresh(peers.ctx)
		}
	}
}

func (peers *peerSet) Refresh(ctx context.Context) error {
	peers.refreshMu.Lock()
	lookup, cancel := context.WithTimeout(ctx, peerRefreshInterval)
	stop := context.AfterFunc(peers.ctx, cancel)
	defer stop()
	defer cancel()
	addresses, err := peers.resolver.LookupNetIP(lookup, "ip", peers.hostname)
	var normalized []netip.Addr
	if err == nil {
		normalized, err = normalizePeerIPs(addresses)
	}
	if lookup.Err() != nil {
		err = lookup.Err()
	}
	if peers.ctx.Err() != nil {
		err = peers.ctx.Err()
	}
	peers.mu.Lock()
	if err != nil {
		peers.failure = fmt.Errorf("%w: lookup %q: %w", errPeerMembershipUnavailable, peers.hostname, err)
		failure := peers.failure
		peers.mu.Unlock()
		peers.refreshMu.Unlock()
		return failure
	}
	changed := len(peers.members) != 0 && !slices.Equal(peers.members, normalized)
	peers.members, peers.failure = normalized, nil
	callback := peers.ownerChanges
	peers.mu.Unlock()
	peers.refreshMu.Unlock()
	if changed && callback != nil {
		callback()
	}
	return nil
}

func (peers *peerSet) Ready() error {
	if err := peers.ctx.Err(); err != nil {
		return fmt.Errorf("%w: %w", errPeerMembershipUnavailable, err)
	}
	peers.mu.RLock()
	defer peers.mu.RUnlock()
	return peers.failure
}

func (peers *peerSet) Owner(resourceID string) (string, error) {
	if resourceID == "" {
		return "", errors.New("resource ID is required for gateway ownership")
	}
	if err := peers.ctx.Err(); err != nil {
		return "", fmt.Errorf("%w: %w", errPeerMembershipUnavailable, err)
	}
	peers.mu.RLock()
	defer peers.mu.RUnlock()
	if peers.failure != nil {
		return "", peers.failure
	}
	return rendezvousOwner(peers.members, resourceID).String(), nil
}

func (peers *peerSet) IsOwner(resourceID string) (bool, error) {
	owner, err := peers.Owner(resourceID)
	return err == nil && owner == peers.LocalIP(), err
}

func (peers *peerSet) LocalIP() string { return peers.localIP.String() }

func (peers *peerSet) Members() ([]string, error) {
	if err := peers.ctx.Err(); err != nil {
		return nil, fmt.Errorf("%w: %w", errPeerMembershipUnavailable, err)
	}
	peers.mu.RLock()
	defer peers.mu.RUnlock()
	if peers.failure != nil {
		return nil, peers.failure
	}
	members := make([]string, len(peers.members))
	for index, address := range peers.members {
		members[index] = address.String()
	}
	return members, nil
}

func (peers *peerSet) OnOwnerChanges(callback func()) {
	peers.mu.Lock()
	peers.ownerChanges = callback
	peers.mu.Unlock()
}

func (peers *peerSet) Close() {
	peers.cancel()
	<-peers.done
}
