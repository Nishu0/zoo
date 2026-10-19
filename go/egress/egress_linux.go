package main

import (
	"fmt"
	"net"
	"net/netip"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

// nftables, from the egress container in the host's network namespace (server/docker.py).
type nftFirewall struct{}

func newFirewall() egressFirewall { return nftFirewall{} }

func (nftFirewall) apply(policies []*egressPolicy, l learned) error {
	return nft(renderNft(policies, l, time.Now()))
}

func (nftFirewall) learn(p *egressPolicy, addrs []netip.Addr, allow bool) error {
	set := "d_" + p.tag()
	if allow {
		set = "a_" + p.tag()
	}
	elements := make([]string, len(addrs))
	for i, a := range addrs {
		elements[i] = fmt.Sprintf("%s timeout %ds", a, int(egressLearnTTL.Seconds()))
	}
	return nft(fmt.Sprintf("add element inet zoo %s { %s }\n", set, strings.Join(elements, ", ")))
}

func nft(script string) error {
	cmd := exec.Command("nft", "-f", "-")
	cmd.Stdin = strings.NewReader(script)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("nft: %v: %s", err, out)
	}
	return nil
}

// originalDst is where a connection the firewall redirected was going (SO_ORIGINAL_DST).
func originalDst(c net.Conn) (netip.AddrPort, bool) {
	tc, ok := c.(*net.TCPConn)
	if !ok {
		return netip.AddrPort{}, false
	}
	raw, err := tc.SyscallConn()
	if err != nil {
		return netip.AddrPort{}, false
	}
	var dst netip.AddrPort
	var found bool
	raw.Control(func(fd uintptr) {
		m, err := syscall.GetsockoptIPv6Mreq(int(fd), syscall.IPPROTO_IP, 80)
		if err != nil {
			return
		}
		// a sockaddr_in: family, port, address
		a := m.Multiaddr
		dst = netip.AddrPortFrom(netip.AddrFrom4([4]byte{a[4], a[5], a[6], a[7]}), uint16(a[2])<<8|uint16(a[3]))
		found = true
	})
	return dst, found
}
