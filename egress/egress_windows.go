package main

import (
	"net"
	"net/netip"
)

// On Windows the API puts Hyper-V port ACLs on each filtered VM's network adapter, which let it reach only this
// proxy and the API; there are no host rules for the daemon to keep.
type aclFirewall struct{}

func newFirewall() egressFirewall { return aclFirewall{} }

func (aclFirewall) apply([]*egressPolicy, learned) error { return nil }

func (aclFirewall) learn(*egressPolicy, []netip.Addr, bool) error { return nil }

func originalDst(c net.Conn) (netip.AddrPort, bool) { return netip.AddrPort{}, false }
