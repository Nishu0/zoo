package main

import (
	"bytes"
	"fmt"
	"log"
	"net"
	"net/netip"
	"os/exec"
	"strings"
	"time"
)

// pf on the Mac host, in the `com.apple/zoo` anchor that /etc/pf.conf already evaluates. The daemon runs as the
// host's Zoo user, which needs passwordless sudo for /sbin/pfctl (macos/README.md).
type pfFirewall struct{ loaded bool }

const pfAnchor = "com.apple/zoo"

func newFirewall() egressFirewall { return &pfFirewall{} }

func (f *pfFirewall) apply(policies []*egressPolicy, l learned) error {
	if !f.loaded {
		// the main ruleset holds the com.apple anchors; load it once in case nothing has since boot
		if err := pfctl(nil, "-q", "-f", "/etc/pf.conf"); err != nil {
			log.Printf("egress: loading /etc/pf.conf: %v", err)
		}
		f.loaded = true
	}
	rules := renderPf(policies, l, time.Now(), vmLink)
	if err := pfctl([]byte(rules), "-q", "-a", pfAnchor, "-f", "-"); err != nil {
		return err
	}
	// enabling an enabled pf fails; either way it's on afterwards
	pfctl(nil, "-q", "-e")
	return nil
}

func (f *pfFirewall) learn(p *egressPolicy, addrs []netip.Addr, allow bool) error {
	table := "zoo_d_" + p.tag()
	if allow {
		table = "zoo_a_" + p.tag()
	}
	args := []string{"-q", "-a", pfAnchor, "-t", table, "-T", "add"}
	for _, a := range addrs {
		args = append(args, a.String())
	}
	return pfctl(nil, args...)
}

func pfctl(stdin []byte, args ...string) error {
	cmd := exec.Command("sudo", append([]string{"-n", "/sbin/pfctl"}, args...)...)
	if stdin != nil {
		cmd.Stdin = bytes.NewReader(stdin)
	}
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("pfctl %s: %v: %s", strings.Join(args, " "), err, out)
	}
	return nil
}

// vmLink finds the host interface on the VM's network: Virtualization.framework's NAT bridge.
func vmLink(vm netip.Addr) (pfLink, bool) {
	ifaces, _ := net.Interfaces()
	for _, iface := range ifaces {
		addrs, _ := iface.Addrs()
		for _, a := range addrs {
			n, ok := a.(*net.IPNet)
			if !ok {
				continue
			}
			prefix, err := netip.ParsePrefix(n.String())
			if err != nil || !prefix.Addr().Is4() || !prefix.Contains(vm) || prefix.Addr() == vm {
				continue
			}
			return pfLink{name: iface.Name, gw: prefix.Addr()}, true
		}
	}
	return pfLink{}, false
}

// originalDst isn't available from pf without its ioctl; TLS without a server name is refused instead.
func originalDst(c net.Conn) (netip.AddrPort, bool) { return netip.AddrPort{}, false }
