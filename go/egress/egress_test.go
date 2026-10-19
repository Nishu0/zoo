package main

import (
	"bufio"
	"crypto/tls"
	"net"
	"net/netip"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/dns/dnsmessage"
)

func testPolicy(t *testing.T, def string, rules ...egressRule) *egressPolicy {
	t.Helper()
	p := &egressPolicy{ID: "sb", Addrs: []string{"172.18.0.5"}, Default: def, DNS: true, Rules: rules,
		Always: []egressEndpoint{{IP: "172.18.0.2", Port: 8000}}}
	if err := p.compile(); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestAllowDial(t *testing.T) {
	notLocal := func(netip.Addr) bool { return false }
	ip := netip.MustParseAddr
	deny := testPolicy(t, "deny",
		egressRule{"domain", "github.com", "allow"},
		egressRule{"domain", "evil.github.com", "deny"},
		egressRule{"cidr", "10.1.0.0/16", "allow"},
		egressRule{"ip", "127.0.0.9", "allow"})
	cases := []struct {
		host string
		addr string
		port int
		want bool
	}{
		{"github.com", "140.82.112.3", 443, true},
		{"api.github.com", "140.82.112.3", 22, true},
		{"evil.github.com", "140.82.112.3", 443, false},
		{"notgithub.com", "1.2.3.4", 443, false},
		{"", "10.1.2.3", 5432, true},
		{"", "1.2.3.4", 443, false},
		{"github.com", "127.0.0.1", 443, false},      // an allowed name pointing at the host
		{"github.com", "169.254.169.254", 80, false}, // cloud metadata
		{"", "127.0.0.9", 80, true},                  // opened by an ip rule
		{"", "172.18.0.2", 8000, true},               // the API
		{"", "172.18.0.2", 8001, false},
	}
	for _, c := range cases {
		if got := deny.allowDial(c.host, ip(c.addr), c.port, notLocal); got != c.want {
			t.Errorf("deny policy: %s %s:%d = %v, want %v", c.host, c.addr, c.port, got, c.want)
		}
	}
	allow := testPolicy(t, "allow", egressRule{"domain", "*.example.com", "deny"}, egressRule{"cidr", "9.9.9.0/24", "deny"})
	if !allow.allowDial("anything.org", ip("1.2.3.4"), 443, notLocal) {
		t.Error("allow policy refused an unlisted host")
	}
	if allow.allowDial("www.example.com", ip("1.2.3.4"), 443, notLocal) || allow.allowDial("example.com", ip("1.2.3.4"), 443, notLocal) {
		t.Error("allow policy let a denied domain through")
	}
	if allow.allowDial("anything.org", ip("9.9.9.9"), 443, notLocal) {
		t.Error("allow policy let a denied cidr through")
	}
	if allow.allowDial("anything.org", ip("10.0.0.1"), 443, func(netip.Addr) bool { return true }) {
		t.Error("allow policy reached the host's own address")
	}
	if !allow.allowName("ok.org") || allow.allowName("a.example.com") || deny.allowName("ok.org") || !deny.allowName("github.com") {
		t.Error("allowName")
	}
}

func TestBadRuleShutsOff(t *testing.T) {
	p := &egressPolicy{ID: "sb", Addrs: []string{"172.18.0.5"}, Default: "allow", DNS: true,
		Rules: []egressRule{{"cidr", "not-a-cidr", "deny"}}}
	if err := p.compile(); err != nil || p.Default != "deny" || p.DNS || len(p.Rules) != 0 {
		t.Fatalf("got %+v, %v", p, err)
	}
}

func TestParseSNI(t *testing.T) {
	client, server := net.Pipe()
	go func() {
		tls.Client(client, &tls.Config{ServerName: "Example.COM"}).Handshake()
	}()
	br := bufio.NewReaderSize(server, 17<<10)
	server.SetReadDeadline(time.Now().Add(5 * time.Second))
	if got := clientHelloSNI(br); got != "example.com" {
		t.Fatalf("sni = %q", got)
	}
	client.Close()
	if parseSNI([]byte{1, 0, 0}) != "" || parseSNI(nil) != "" {
		t.Error("short hellos must give no name")
	}
}

func TestPeekHead(t *testing.T) {
	data := "CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\nrest"
	br := bufio.NewReaderSize(strings.NewReader(data), 64)
	head, err := peekHead(br)
	if err != nil || string(head) != data[:len(data)-4] {
		t.Fatalf("head = %q, %v", head, err)
	}
	if _, err := peekHead(bufio.NewReaderSize(strings.NewReader(strings.Repeat("x", 100)), 16)); err == nil {
		t.Error("an endless head must fail")
	}
}

func TestDNSAnswer(t *testing.T) {
	p := testPolicy(t, "deny", egressRule{"domain", "github.com", "allow"})
	e := &egress{bySrc: map[netip.Addr]*egressPolicy{netip.MustParseAddr("172.18.0.5"): p}, learned: learned{}}
	query := func(name string) []byte {
		b := dnsmessage.NewBuilder(nil, dnsmessage.Header{ID: 7, RecursionDesired: true})
		b.StartQuestions()
		b.Question(dnsmessage.Question{Name: dnsmessage.MustNewName(name), Type: dnsmessage.TypeA, Class: dnsmessage.ClassINET})
		out, _ := b.Finish()
		return out
	}
	rcode := func(resp []byte) dnsmessage.RCode {
		var parser dnsmessage.Parser
		h, err := parser.Start(resp)
		if err != nil || h.ID != 7 || !h.Response {
			t.Fatalf("bad reply %v", err)
		}
		return h.RCode
	}
	if got := rcode(e.answer(query("evil.com."), netip.MustParseAddr("172.18.0.5"), false)); got != dnsmessage.RCodeNameError {
		t.Errorf("denied name: %v", got)
	}
	if got := rcode(e.answer(query("github.com."), netip.MustParseAddr("172.18.0.9"), false)); got != dnsmessage.RCodeRefused {
		t.Errorf("unknown source: %v", got)
	}
	p.DNS = false
	if got := rcode(e.answer(query("github.com."), netip.MustParseAddr("172.18.0.5"), false)); got != dnsmessage.RCodeRefused {
		t.Errorf("dns off: %v", got)
	}
}

func TestRenderNft(t *testing.T) {
	p := testPolicy(t, "deny", egressRule{"domain", "github.com", "allow"}, egressRule{"cidr", "10.1.0.0/16", "allow"},
		egressRule{"ip", "9.9.9.9", "deny"})
	now := time.Now()
	l := learned{p.tag(): {netip.MustParseAddr("140.82.112.3"): {true, now.Add(time.Minute)}, netip.MustParseAddr("1.1.1.1"): {true, now.Add(-time.Minute)}}}
	out := renderNft([]*egressPolicy{p}, l, now)
	for _, want := range []string{
		"delete table inet zoo",
		"set sandboxes { type ipv4_addr; elements = { 172.18.0.5 }; }",
		"set a_" + p.tag() + " { type ipv4_addr; flags timeout; elements = { 140.82.112.3 timeout 60s }; }",
		"ip saddr 172.18.0.5 jump p_" + p.tag(),
		"ip daddr 172.18.0.2 tcp dport 8000 return",
		"ip daddr { 10.1.0.0/16 } return",
		"meta l4proto { tcp, udp } th dport 53 redirect to :15353",
		"tcp dport { 80, 443 } redirect to :15128",
		"ip daddr { 9.9.9.9/32 } reject",
		"ip saddr 172.18.0.5 jump i_" + p.tag(),
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in\n%s", want, out)
		}
	}
	if strings.Contains(out, "1.1.1.1") {
		t.Error("an expired learned address was kept")
	}
	fwd := out[strings.Index(out, "chain f_"+p.tag()):]
	if !strings.HasPrefix(fwd[strings.Index(fwd, "ip daddr @a_"):], "ip daddr @a_"+p.tag()+" accept\n    reject\n  }") {
		t.Error("deny-by-default chain must end in reject")
	}
	open := testPolicy(t, "allow", egressRule{"cidr", "9.9.9.0/24", "deny"})
	if out := renderNft([]*egressPolicy{open}, learned{}, now); strings.Contains(out, "redirect") || strings.Contains(out, "jump i_") {
		t.Errorf("an allow policy without domain rules needs no proxy:\n%s", out)
	}
}

func TestRenderPf(t *testing.T) {
	p := testPolicy(t, "deny", egressRule{"domain", "github.com", "allow"})
	p.Addrs, p.addrs = []string{"192.168.64.5"}, []netip.Addr{netip.MustParseAddr("192.168.64.5")}
	link := func(netip.Addr) (pfLink, bool) { return pfLink{"bridge100", netip.MustParseAddr("192.168.64.1")}, true }
	out := renderPf([]*egressPolicy{p}, learned{}, time.Now(), link)
	rdr := strings.Index(out, "rdr pass on bridge100 inet proto tcp from 192.168.64.5 to any port { 80 443 } -> 192.168.64.1 port 15128")
	block := strings.Index(out, "block return in quick on bridge100 inet from 192.168.64.5\n")
	if rdr < 0 || block < 0 || rdr > block || !strings.Contains(out, "block return in quick on bridge100 inet6") {
		t.Errorf("pf rules:\n%s", out)
	}
	if out := renderPf([]*egressPolicy{p}, learned{}, time.Now(), func(netip.Addr) (pfLink, bool) { return pfLink{}, false }); !strings.Contains(out, "block return in quick from 192.168.64.5") {
		t.Errorf("a VM on no known network must stay shut:\n%s", out)
	}
}
