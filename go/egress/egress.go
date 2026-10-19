package main

// The host side of sandbox network policy, run once per host as `zoo-egress -dir DIR`, outside every sandbox, so
// nothing an agent does inside one (as root or admin) can change it. The API writes one JSON policy per sandbox
// into DIR and the daemon picks up changes within a second. It serves:
//   - a filtering proxy: HTTP CONNECT and plain proxy requests, plus HTTP and TLS that the host firewall redirects
//     to it, decided by the Host header or the TLS server name, so domain rules hold when a site's IPs change;
//   - a DNS resolver that only answers names the policy allows;
//   - the host firewall rules (nftables on Linux, pf on macOS) that send each filtered sandbox's traffic to the two
//     and decide the rest. Windows VMs reach the network only through the proxy (Hyper-V port ACLs, set by the API).

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/netip"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/dns/dnsmessage"
)

const (
	egressProxyPort = 15128
	egressDNSPort   = 15353
	// how long an address a policy's DNS answer handed out stays open for ports the proxy doesn't see
	egressLearnTTL    = 30 * time.Minute
	egressDialTimeout = 10 * time.Second
	egressHeadTimeout = 30 * time.Second
)

var errDenied = errors.New("denied by the sandbox's network policy")

type egressEndpoint struct {
	IP   string `json:"ip"`
	Port int    `json:"port"`
}

type egressRule struct {
	Type   string `json:"type"` // domain, ip or cidr
	Value  string `json:"value"`
	Effect string `json:"effect"` // allow or deny
}

// egressPolicy is one sandbox's policy as the API writes it.
type egressPolicy struct {
	ID      string           `json:"id"`
	Addrs   []string         `json:"addrs"`   // the sandbox's addresses on this host
	Default string           `json:"default"` // allow or deny
	DNS     bool             `json:"dns"`
	Rules   []egressRule     `json:"rules"`
	Always  []egressEndpoint `json:"always"` // the API the sandbox's guest dials, open whatever the rules say

	addrs  []netip.Addr
	nets   []egressNet
	always []netip.AddrPort
}

type egressNet struct {
	prefix netip.Prefix
	allow  bool
}

type verdict int

const (
	unmatched verdict = iota
	allowed
	denied
)

func (p *egressPolicy) compile() error {
	if p.ID == "" {
		return errors.New("policy needs an id")
	}
	for _, s := range p.Addrs {
		a, err := netip.ParseAddr(s)
		if err != nil {
			return err
		}
		p.addrs = append(p.addrs, a.Unmap())
	}
	for _, e := range p.Always {
		if a, err := netip.ParseAddr(e.IP); err == nil && e.Port > 0 && e.Port <= 65535 {
			p.always = append(p.always, netip.AddrPortFrom(a.Unmap(), uint16(e.Port)))
		}
	}
	if err := p.compileRules(); err != nil {
		// a rule the daemon can't read must not open anything: shut the sandbox off instead
		log.Printf("egress: %s: %v; denying everything until the policy is fixed", p.ID, err)
		p.Default, p.DNS, p.Rules, p.nets = "deny", false, nil, nil
	}
	return nil
}

func (p *egressPolicy) compileRules() error {
	if p.Default != "allow" && p.Default != "deny" {
		return fmt.Errorf("default must be allow or deny, not %q", p.Default)
	}
	for _, r := range p.Rules {
		if r.Effect != "allow" && r.Effect != "deny" {
			return fmt.Errorf("bad effect %q", r.Effect)
		}
		switch r.Type {
		case "domain":
			continue
		case "ip", "cidr":
		default:
			return fmt.Errorf("bad rule type %q", r.Type)
		}
		prefix, err := netip.ParsePrefix(r.Value)
		if err != nil {
			a, aerr := netip.ParseAddr(r.Value)
			if aerr != nil {
				return fmt.Errorf("bad %s rule %q", r.Type, r.Value)
			}
			a = a.Unmap()
			prefix = netip.PrefixFrom(a, a.BitLen())
		}
		p.nets = append(p.nets, egressNet{prefix.Masked(), r.Effect == "allow"})
	}
	return nil
}

// tag names the policy's chains, sets and tables: stable, short and safe in any firewall syntax.
func (p *egressPolicy) tag() string {
	sum := sha256.Sum256([]byte(p.ID))
	return hex.EncodeToString(sum[:6])
}

// proxied is whether the sandbox's web traffic and DNS must go through the daemon: when names decide anything.
func (p *egressPolicy) proxied() bool {
	if p.Default == "deny" {
		return true
	}
	return slices.ContainsFunc(p.Rules, func(r egressRule) bool { return r.Type == "domain" })
}

func domainMatches(host, rule string) bool {
	rule = strings.TrimSuffix(strings.TrimPrefix(strings.ToLower(rule), "*."), ".")
	return rule != "" && (host == rule || strings.HasSuffix(host, "."+rule))
}

// hostVerdict: a deny rule wins over an allow rule, whatever their order.
func (p *egressPolicy) hostVerdict(host string) verdict {
	v := unmatched
	for _, r := range p.Rules {
		if r.Type == "domain" && domainMatches(host, r.Value) {
			if r.Effect != "allow" {
				return denied
			}
			v = allowed
		}
	}
	return v
}

func (p *egressPolicy) ipVerdict(a netip.Addr) verdict {
	v := unmatched
	for _, n := range p.nets {
		if n.prefix.Contains(a) {
			if !n.allow {
				return denied
			}
			v = allowed
		}
	}
	return v
}

// allowName decides a DNS question.
func (p *egressPolicy) allowName(host string) bool {
	switch p.hostVerdict(host) {
	case denied:
		return false
	case allowed:
		return true
	}
	return p.Default == "allow"
}

// allowDial decides one connection the proxy would make. Loopback, link-local (cloud metadata) and the host's own
// addresses stay closed unless an ip or cidr rule opens them: the proxy runs on the host, where they mean the host.
func (p *egressPolicy) allowDial(host string, a netip.Addr, port int, local func(netip.Addr) bool) bool {
	if slices.Contains(p.always, netip.AddrPortFrom(a, uint16(port))) {
		return true
	}
	iv, hv := p.ipVerdict(a), unmatched
	if host != "" {
		hv = p.hostVerdict(host)
	}
	switch {
	case iv == denied || hv == denied:
		return false
	case iv == allowed:
		return true
	case a.IsLoopback() || a.IsLinkLocalUnicast() || a.IsMulticast() || a.IsUnspecified() || local(a):
		return false
	case hv == allowed:
		return true
	}
	return p.Default == "allow"
}

func (p *egressPolicy) prefixes(allow, v4 bool) []string {
	var out []string
	for _, n := range p.nets {
		if n.allow == allow && n.prefix.Addr().Is4() == v4 {
			out = append(out, n.prefix.String())
		}
	}
	return out
}

type learnedAddr struct {
	allow bool
	until time.Time
}

// learned holds, per policy tag, the addresses its DNS answers handed out (allow) or its denied names resolve to
// (deny), so ports the proxy doesn't see follow domain rules too.
type learned map[string]map[netip.Addr]learnedAddr

func (l learned) split(tag string, now time.Time) (allow, deny map[netip.Addr]time.Duration) {
	allow, deny = map[netip.Addr]time.Duration{}, map[netip.Addr]time.Duration{}
	for a, e := range l[tag] {
		if left := e.until.Sub(now); left > time.Second {
			if e.allow {
				allow[a] = left
			} else {
				deny[a] = left
			}
		}
	}
	return allow, deny
}

// egressFirewall is the host firewall of one OS.
type egressFirewall interface {
	apply(policies []*egressPolicy, l learned) error
	learn(p *egressPolicy, addrs []netip.Addr, allow bool) error
}

type egress struct {
	dir      string
	fw       egressFirewall
	upstream string

	mu      sync.RWMutex
	bySrc   map[netip.Addr]*egressPolicy
	local   map[netip.Addr]bool
	learned learned
	sig     string
}

func runEgress(dir string) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		log.Fatal(err)
	}
	e := &egress{dir: dir, fw: newFirewall(), upstream: upstreamDNS(), learned: learned{}}
	e.reload(true)
	proxy, err := net.Listen("tcp", ":"+strconv.Itoa(egressProxyPort))
	if err != nil {
		log.Fatal(err)
	}
	dnsUDP, err := net.ListenPacket("udp", ":"+strconv.Itoa(egressDNSPort))
	if err != nil {
		log.Fatal(err)
	}
	dnsTCP, err := net.Listen("tcp", ":"+strconv.Itoa(egressDNSPort))
	if err != nil {
		log.Fatal(err)
	}
	go e.serveProxy(proxy)
	go e.serveDNS(dnsUDP)
	go e.serveDNSTCP(dnsTCP)
	log.Printf("egress: proxy on :%d, DNS on :%d, policies in %s", egressProxyPort, egressDNSPort, dir)
	// every five minutes the rules are written again anyway, dropping expired learned addresses (pf tables don't
	// expire entries) and putting back anything else that removed them
	for tick := 1; ; tick++ {
		time.Sleep(time.Second)
		e.reload(tick%300 == 0)
	}
}

// reload reads the policies when the directory changed and applies them; a failed apply is tried again next time.
func (e *egress) reload(force bool) {
	entries, err := os.ReadDir(e.dir)
	if err != nil {
		log.Printf("egress: %v", err)
		return
	}
	var sig strings.Builder
	var policies []*egressPolicy
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		fmt.Fprintf(&sig, "%s %d %d;", entry.Name(), info.ModTime().UnixNano(), info.Size())
		data, err := os.ReadFile(filepath.Join(e.dir, entry.Name()))
		if err != nil {
			continue
		}
		p := &egressPolicy{}
		if err := json.Unmarshal(data, p); err != nil {
			// most likely a file still being written: keep the rules as they are and read again next second
			log.Printf("egress: %s: %v", entry.Name(), err)
			return
		}
		if err := p.compile(); err != nil {
			log.Printf("egress: %s: %v", entry.Name(), err)
			continue
		}
		policies = append(policies, p)
	}
	if !force && sig.String() == e.sig {
		return
	}
	bySrc := map[netip.Addr]*egressPolicy{}
	tags := map[string]bool{}
	for _, p := range policies {
		tags[p.tag()] = true
		for _, a := range p.addrs {
			bySrc[a] = p
		}
	}
	now := time.Now()
	e.mu.Lock()
	e.bySrc, e.local = bySrc, hostAddrs()
	snapshot := learned{}
	for tag, addrs := range e.learned {
		if !tags[tag] {
			delete(e.learned, tag)
			continue
		}
		snapshot[tag] = map[netip.Addr]learnedAddr{}
		for a, l := range addrs {
			if now.After(l.until) {
				delete(addrs, a)
			} else {
				snapshot[tag][a] = l
			}
		}
	}
	e.mu.Unlock()
	if err := e.fw.apply(policies, snapshot); err != nil {
		log.Printf("egress: applying %d policies: %v", len(policies), err)
		return
	}
	e.sig = sig.String()
}

func (e *egress) policyFor(a netip.Addr) *egressPolicy {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.bySrc[a]
}

func (e *egress) isLocal(a netip.Addr) bool {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.local[a]
}

func (e *egress) learn(p *egressPolicy, addrs []netip.Addr, allow bool) {
	var v4 []netip.Addr
	for _, a := range addrs {
		if a = a.Unmap(); a.Is4() {
			v4 = append(v4, a)
		}
	}
	if len(v4) == 0 {
		return
	}
	until := time.Now().Add(egressLearnTTL)
	e.mu.Lock()
	m := e.learned[p.tag()]
	if m == nil {
		m = map[netip.Addr]learnedAddr{}
		e.learned[p.tag()] = m
	}
	for _, a := range v4 {
		m[a] = learnedAddr{allow, until}
	}
	e.mu.Unlock()
	if err := e.fw.learn(p, v4, allow); err != nil {
		log.Printf("egress: %s: learning %v: %v", p.ID, v4, err)
	}
}

func hostAddrs() map[netip.Addr]bool {
	out := map[netip.Addr]bool{}
	addrs, _ := net.InterfaceAddrs()
	for _, a := range addrs {
		if n, ok := a.(*net.IPNet); ok {
			if ip, ok := netip.AddrFromSlice(n.IP); ok {
				out[ip.Unmap()] = true
			}
		}
	}
	return out
}

func addrOf(a net.Addr) netip.Addr {
	switch a := a.(type) {
	case *net.TCPAddr:
		return a.AddrPort().Addr().Unmap()
	case *net.UDPAddr:
		return a.AddrPort().Addr().Unmap()
	}
	return netip.Addr{}
}

// The proxy

func (e *egress) serveProxy(ln net.Listener) {
	for {
		c, err := ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return
			}
			time.Sleep(10 * time.Millisecond)
			continue
		}
		go e.handle(c)
	}
}

func (e *egress) handle(c net.Conn) {
	defer c.Close()
	p := e.policyFor(addrOf(c.RemoteAddr()))
	if p == nil {
		return
	}
	c.SetReadDeadline(time.Now().Add(egressHeadTimeout))
	br := bufio.NewReaderSize(c, 17<<10)
	first, err := br.Peek(1)
	if err != nil {
		return
	}
	if first[0] == 0x16 {
		// TLS redirected here by the firewall: the server name decides
		host, port := clientHelloSNI(br), 443
		if dst, ok := originalDst(c); ok {
			port = int(dst.Port())
			if host == "" {
				host = dst.Addr().String()
			}
		}
		up, err := e.dial(p, host, port)
		if err != nil {
			return
		}
		defer up.Close()
		splice(c, br, up)
		return
	}
	head, err := peekHead(br)
	if err != nil {
		return
	}
	req, err := http.ReadRequest(bufio.NewReader(bytes.NewReader(head)))
	if err != nil {
		return
	}
	switch {
	case req.Method == http.MethodConnect:
		host, port := splitTarget(req.Host, 443)
		up, err := e.dial(p, host, port)
		if err != nil {
			refuse(c, err)
			return
		}
		defer up.Close()
		br.Discard(len(head))
		io.WriteString(c, "HTTP/1.1 200 Connection established\r\n\r\n")
		splice(c, br, up)
	case req.URL.IsAbs():
		// a plain proxy request: sent on in origin form, one request per connection
		if req.URL.Scheme != "http" {
			refuse(c, errors.New("only http:// URLs can be proxied without CONNECT"))
			return
		}
		host, port := splitTarget(req.URL.Host, 80)
		up, err := e.dial(p, host, port)
		if err != nil {
			refuse(c, err)
			return
		}
		defer up.Close()
		full, err := http.ReadRequest(br)
		if err != nil {
			return
		}
		full.Header.Del("Proxy-Connection")
		full.Header.Del("Proxy-Authorization")
		full.Close = true
		if err := full.Write(up); err != nil {
			return
		}
		splice(c, br, up)
	default:
		// plain HTTP redirected here by the firewall: the Host header decides
		host, port := splitTarget(req.Host, 80)
		if dst, ok := originalDst(c); ok {
			port = int(dst.Port())
			if host == "" {
				host = dst.Addr().String()
			}
		}
		up, err := e.dial(p, host, port)
		if err != nil {
			refuse(c, err)
			return
		}
		defer up.Close()
		splice(c, br, up)
	}
}

// dial resolves the name itself, so a sandbox can't pair an allowed name with an address of its choosing, and
// connects to the first address the policy allows.
func (e *egress) dial(p *egressPolicy, host string, port int) (net.Conn, error) {
	host = strings.TrimSuffix(strings.ToLower(strings.Trim(host, "[]")), ".")
	if host == "" || port <= 0 || port > 65535 {
		return nil, errDenied
	}
	name := host
	var addrs []netip.Addr
	if a, err := netip.ParseAddr(host); err == nil {
		addrs, name = []netip.Addr{a.Unmap()}, ""
	} else {
		if p.hostVerdict(host) == denied {
			log.Printf("egress: %s: denied %s:%d", p.ID, host, port)
			return nil, errDenied
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		found, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
		if err != nil {
			return nil, err
		}
		for _, a := range found {
			addrs = append(addrs, a.Unmap())
		}
	}
	var last error = errDenied
	for _, a := range addrs {
		if !p.allowDial(name, a, port, e.isLocal) {
			continue
		}
		c, err := net.DialTimeout("tcp", netip.AddrPortFrom(a, uint16(port)).String(), egressDialTimeout)
		if err == nil {
			return c, nil
		}
		last = err
	}
	if last == errDenied {
		log.Printf("egress: %s: denied %s:%d", p.ID, host, port)
	}
	return nil, last
}

func refuse(c net.Conn, err error) {
	status, msg := "502 Bad Gateway", err.Error()+"\n"
	if errors.Is(err, errDenied) {
		status = "403 Forbidden"
	}
	fmt.Fprintf(c, "HTTP/1.1 %s\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: %d\r\n\r\n%s",
		status, len(msg), msg)
}

func splitTarget(s string, port int) (string, int) {
	host, p, err := net.SplitHostPort(s)
	if err != nil {
		return s, port
	}
	n, err := strconv.Atoi(p)
	if err != nil {
		return "", 0
	}
	return host, n
}

// peekHead returns an HTTP request's head without consuming it.
func peekHead(br *bufio.Reader) ([]byte, error) {
	n := max(br.Buffered(), 1)
	for {
		b, err := br.Peek(n)
		if err != nil {
			return nil, err
		}
		if i := bytes.Index(b, []byte("\r\n\r\n")); i >= 0 {
			return b[:i+4], nil
		}
		if n >= br.Size() {
			return nil, errors.New("request head too large")
		}
		n = min(max(n+1, br.Buffered()), br.Size())
	}
}

// splice copies both ways until both sides are done, or a minute after one of them is.
func splice(c net.Conn, br *bufio.Reader, up net.Conn) {
	c.SetReadDeadline(time.Time{})
	done := make(chan struct{}, 1)
	go func() {
		io.Copy(up, br)
		closeWrite(up)
		done <- struct{}{}
	}()
	io.Copy(c, up)
	closeWrite(c)
	select {
	case <-done:
	case <-time.After(time.Minute):
	}
}

func closeWrite(c net.Conn) {
	if cw, ok := c.(interface{ CloseWrite() error }); ok {
		cw.CloseWrite()
	} else {
		c.Close()
	}
}

// clientHelloSNI reads the server name from a TLS ClientHello without consuming it, or "" without one.
func clientHelloSNI(br *bufio.Reader) string {
	h, err := br.Peek(5)
	if err != nil {
		return ""
	}
	record, err := br.Peek(5 + int(binary.BigEndian.Uint16(h[3:5])))
	if err != nil {
		return ""
	}
	return parseSNI(record[5:])
}

type cursor struct {
	b   []byte
	bad bool
}

func (c *cursor) take(n int) []byte {
	if c.bad || n < 0 || n > len(c.b) {
		c.bad = true
		return nil
	}
	out := c.b[:n]
	c.b = c.b[n:]
	return out
}

func (c *cursor) u8() int {
	if b := c.take(1); b != nil {
		return int(b[0])
	}
	return 0
}

func (c *cursor) u16() int {
	if b := c.take(2); b != nil {
		return int(binary.BigEndian.Uint16(b))
	}
	return 0
}

func (c *cursor) sub(n int) *cursor { return &cursor{b: c.take(n), bad: c.bad} }

func parseSNI(handshake []byte) string {
	c := &cursor{b: handshake}
	if c.u8() != 1 { // ClientHello
		return ""
	}
	c.take(3)       // length
	c.take(2 + 32)  // version, random
	c.take(c.u8())  // session id
	c.take(c.u16()) // cipher suites
	c.take(c.u8())  // compression methods
	ext := c.sub(c.u16())
	for !ext.bad && len(ext.b) >= 4 {
		typ := ext.u16()
		body := ext.sub(ext.u16())
		if typ != 0 { // server_name
			continue
		}
		list := body.sub(body.u16())
		for !list.bad && len(list.b) >= 3 {
			kind := list.u8()
			name := list.take(list.u16())
			if kind == 0 && !list.bad {
				return strings.ToLower(string(name))
			}
		}
		return ""
	}
	return ""
}

// DNS

func (e *egress) serveDNS(pc net.PacketConn) {
	buf := make([]byte, 65535)
	for {
		n, addr, err := pc.ReadFrom(buf)
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return
			}
			continue
		}
		msg := append([]byte(nil), buf[:n]...)
		go func() {
			if out := e.answer(msg, addrOf(addr), false); out != nil {
				pc.WriteTo(out, addr)
			}
		}()
	}
}

func (e *egress) serveDNSTCP(ln net.Listener) {
	for {
		c, err := ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return
			}
			time.Sleep(10 * time.Millisecond)
			continue
		}
		go func() {
			defer c.Close()
			src := addrOf(c.RemoteAddr())
			for {
				c.SetDeadline(time.Now().Add(10 * time.Second))
				var size [2]byte
				if _, err := io.ReadFull(c, size[:]); err != nil {
					return
				}
				msg := make([]byte, binary.BigEndian.Uint16(size[:]))
				if _, err := io.ReadFull(c, msg); err != nil {
					return
				}
				out := e.answer(msg, src, true)
				if out == nil {
					return
				}
				binary.BigEndian.PutUint16(size[:], uint16(len(out)))
				if _, err := c.Write(append(size[:], out...)); err != nil {
					return
				}
			}
		}()
	}
}

// answer resolves one query for a sandbox: refused without a policy or with DNS off, NXDOMAIN for a name the
// policy doesn't allow, and the upstream answer otherwise.
func (e *egress) answer(msg []byte, src netip.Addr, tcp bool) []byte {
	var parser dnsmessage.Parser
	h, err := parser.Start(msg)
	if err != nil {
		return nil
	}
	q, err := parser.Question()
	if err != nil {
		return nil
	}
	p := e.policyFor(src)
	if p == nil || !p.DNS {
		return dnsReply(h, q, dnsmessage.RCodeRefused)
	}
	name := strings.TrimSuffix(strings.ToLower(q.Name.String()), ".")
	if !p.allowName(name) {
		if p.Default == "allow" {
			// keep the name's addresses closed on ports the proxy doesn't see, too
			go func() {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if found, err := net.DefaultResolver.LookupNetIP(ctx, "ip4", name); err == nil {
					e.learn(p, found, false)
				}
			}()
		}
		return dnsReply(h, q, dnsmessage.RCodeNameError)
	}
	resp, err := exchange(e.upstream, msg, tcp)
	if err != nil {
		return dnsReply(h, q, dnsmessage.RCodeServerFailure)
	}
	if p.Default == "deny" {
		// before the answer goes out, so the sandbox's first connection already finds the address open
		e.learn(p, answerAddrs(resp), true)
	}
	return resp
}

func dnsReply(h dnsmessage.Header, q dnsmessage.Question, rcode dnsmessage.RCode) []byte {
	b := dnsmessage.NewBuilder(nil, dnsmessage.Header{
		ID: h.ID, Response: true, OpCode: h.OpCode, RecursionDesired: h.RecursionDesired,
		RecursionAvailable: true, RCode: rcode,
	})
	b.StartQuestions()
	b.Question(q)
	out, _ := b.Finish()
	return out
}

func answerAddrs(resp []byte) []netip.Addr {
	var p dnsmessage.Parser
	if _, err := p.Start(resp); err != nil {
		return nil
	}
	if err := p.SkipAllQuestions(); err != nil {
		return nil
	}
	var out []netip.Addr
	for {
		h, err := p.AnswerHeader()
		if err != nil {
			return out
		}
		if h.Type != dnsmessage.TypeA {
			if err := p.SkipAnswer(); err != nil {
				return out
			}
			continue
		}
		r, err := p.AResource()
		if err != nil {
			return out
		}
		out = append(out, netip.AddrFrom4(r.A))
	}
}

func exchange(server string, msg []byte, tcp bool) ([]byte, error) {
	network := "udp"
	if tcp {
		network = "tcp"
	}
	c, err := net.DialTimeout(network, server, 5*time.Second)
	if err != nil {
		return nil, err
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(5 * time.Second))
	if !tcp {
		if _, err := c.Write(msg); err != nil {
			return nil, err
		}
		buf := make([]byte, 65535)
		n, err := c.Read(buf)
		if err != nil {
			return nil, err
		}
		return buf[:n], nil
	}
	size := binary.BigEndian.AppendUint16(nil, uint16(len(msg)))
	if _, err := c.Write(append(size, msg...)); err != nil {
		return nil, err
	}
	if _, err := io.ReadFull(c, size); err != nil {
		return nil, err
	}
	out := make([]byte, binary.BigEndian.Uint16(size))
	_, err = io.ReadFull(c, out)
	return out, err
}

// upstreamDNS is the host's first nameserver.
func upstreamDNS() string {
	if data, err := os.ReadFile("/etc/resolv.conf"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if f := strings.Fields(line); len(f) >= 2 && f[0] == "nameserver" {
				if a, err := netip.ParseAddr(f[1]); err == nil {
					return netip.AddrPortFrom(a, 53).String()
				}
			}
		}
	}
	return "1.1.1.1:53"
}

// Firewall rules. Every value in them comes from a parsed address or prefix, never from the policy file's text.

func setElements(addrs map[netip.Addr]time.Duration) string {
	if len(addrs) == 0 {
		return ""
	}
	var out []string
	for a, left := range addrs {
		out = append(out, fmt.Sprintf("%s timeout %ds", a, int(left.Seconds())))
	}
	slices.Sort(out)
	return " elements = { " + strings.Join(out, ", ") + " };"
}

// renderNft is the whole `inet zoo` table, replaced in one transaction.
func renderNft(policies []*egressPolicy, l learned, now time.Time) string {
	var b strings.Builder
	w := func(format string, a ...any) { fmt.Fprintf(&b, format+"\n", a...) }
	ports := fmt.Sprintf("{ %d, %d }", egressProxyPort, egressDNSPort)
	var sources []string
	for _, p := range policies {
		for _, a := range p.addrs {
			if a.Is4() {
				sources = append(sources, a.String())
			}
		}
	}
	w("table inet zoo")
	w("delete table inet zoo")
	w("table inet zoo {")
	if len(sources) > 0 {
		w("  set sandboxes { type ipv4_addr; elements = { %s }; }", strings.Join(sources, ", "))
	} else {
		w("  set sandboxes { type ipv4_addr; }")
	}
	for _, p := range policies {
		allow, deny := l.split(p.tag(), now)
		w("  set a_%s { type ipv4_addr; flags timeout;%s }", p.tag(), setElements(allow))
		w("  set d_%s { type ipv4_addr; flags timeout;%s }", p.tag(), setElements(deny))
	}
	jump := func(p *egressPolicy, prefix string) {
		for _, a := range p.addrs {
			family := "ip"
			if a.Is6() {
				family = "ip6"
			}
			w("    %s saddr %s jump %s_%s", family, a, prefix, p.tag())
		}
	}
	always := func(p *egressPolicy, verb string) {
		for _, e := range p.always {
			family := "ip"
			if e.Addr().Is6() {
				family = "ip6"
			}
			w("    %s daddr %s tcp dport %d %s", family, e.Addr(), e.Port(), verb)
		}
	}

	// web and DNS traffic of proxied sandboxes goes to the daemon, except to the API and ip rules that allow
	w("  chain pre {")
	w("    type nat hook prerouting priority dstnat - 10; policy accept;")
	for _, p := range policies {
		if p.proxied() {
			jump(p, "p")
		}
	}
	w("  }")
	for _, p := range policies {
		if !p.proxied() {
			continue
		}
		w("  chain p_%s {", p.tag())
		w("    meta nfproto ipv6 return")
		always(p, "return")
		if allow := p.prefixes(true, true); len(allow) > 0 {
			w("    ip daddr { %s } return", strings.Join(allow, ", "))
		}
		if p.DNS {
			w("    meta l4proto { tcp, udp } th dport 53 redirect to :%d", egressDNSPort)
		}
		w("    tcp dport { 80, 443 } redirect to :%d", egressProxyPort)
		w("  }")
	}

	// everything else a sandbox sends through the host
	w("  chain forward {")
	w("    type filter hook forward priority filter - 10; policy accept;")
	for _, p := range policies {
		jump(p, "f")
	}
	w("  }")
	for _, p := range policies {
		w("  chain f_%s {", p.tag())
		always(p, "accept")
		if p.proxied() {
			w("    meta nfproto ipv6 reject")
		}
		if !p.DNS {
			w("    meta l4proto { tcp, udp } th dport 53 reject")
		}
		if deny := p.prefixes(false, true); len(deny) > 0 {
			w("    ip daddr { %s } reject", strings.Join(deny, ", "))
		}
		if deny := p.prefixes(false, false); len(deny) > 0 {
			w("    ip6 daddr { %s } reject", strings.Join(deny, ", "))
		}
		w("    ip daddr @d_%s reject", p.tag())
		w("    ct state established,related accept")
		if allow := p.prefixes(true, true); len(allow) > 0 {
			w("    ip daddr { %s } accept", strings.Join(allow, ", "))
		}
		if allow := p.prefixes(true, false); len(allow) > 0 {
			w("    ip6 daddr { %s } accept", strings.Join(allow, ", "))
		}
		w("    ip daddr @a_%s accept", p.tag())
		if p.Default == "deny" {
			w("    reject")
		}
		w("  }")
	}

	// the daemon's ports are for sandboxes only, and a deny-by-default sandbox reaches nothing else on the host
	w("  chain inp {")
	w("    type filter hook input priority filter - 10; policy accept;")
	w("    meta l4proto { tcp, udp } th dport %s ip saddr != @sandboxes drop", ports)
	w("    meta nfproto ipv6 meta l4proto { tcp, udp } th dport %s drop", ports)
	for _, p := range policies {
		if p.Default == "deny" {
			jump(p, "i")
		}
	}
	w("  }")
	for _, p := range policies {
		if p.Default != "deny" {
			continue
		}
		w("  chain i_%s {", p.tag())
		w("    tcp dport %d accept", egressProxyPort)
		if p.DNS {
			w("    meta l4proto { tcp, udp } th dport %d accept", egressDNSPort)
		}
		always(p, "accept")
		w("    ct state established,related accept")
		w("    reject")
		w("  }")
	}
	w("}")
	return b.String()
}

// pfLink is the host interface a VM's traffic arrives on and the host's address there.
type pfLink struct {
	name string
	gw   netip.Addr
}

func pfTable(addrs map[netip.Addr]time.Duration) string {
	var out []string
	for a := range addrs {
		out = append(out, a.String())
	}
	slices.Sort(out)
	return strings.Join(out, " ")
}

// renderPf is the `com.apple/zoo` anchor: translation rules first, then filter rules, as pf requires.
func renderPf(policies []*egressPolicy, l learned, now time.Time, link func(netip.Addr) (pfLink, bool)) string {
	var tables, rdr, filter strings.Builder
	links := map[string]bool{}
	for _, p := range policies {
		tag := p.tag()
		allow, deny := l.split(tag, now)
		fmt.Fprintf(&tables, "table <zoo_a_%s> persist { %s }\n", tag, pfTable(allow))
		fmt.Fprintf(&tables, "table <zoo_d_%s> persist { %s }\n", tag, pfTable(deny))
		for _, vm := range p.addrs {
			if !vm.Is4() {
				continue
			}
			lk, ok := link(vm)
			if !ok {
				// the VM isn't on any of this host's networks yet; keep it shut until it is
				if p.Default == "deny" {
					fmt.Fprintf(&filter, "block return in quick from %s\n", vm)
				}
				continue
			}
			on := "on " + lk.name
			if p.proxied() {
				links[lk.name] = true
				for _, e := range p.always {
					fmt.Fprintf(&rdr, "no rdr %s inet proto tcp from %s to %s port %d\n", on, vm, e.Addr(), e.Port())
				}
				if allow := p.prefixes(true, true); len(allow) > 0 {
					fmt.Fprintf(&rdr, "no rdr %s inet from %s to { %s }\n", on, vm, strings.Join(allow, " "))
				}
				if p.DNS {
					fmt.Fprintf(&rdr, "rdr pass %s inet proto { tcp udp } from %s to any port 53 -> %s port %d\n",
						on, vm, lk.gw, egressDNSPort)
				}
				fmt.Fprintf(&rdr, "rdr pass %s inet proto tcp from %s to any port { 80 443 } -> %s port %d\n",
					on, vm, lk.gw, egressProxyPort)
			}
			for _, e := range p.always {
				fmt.Fprintf(&filter, "pass in quick %s inet proto tcp from %s to %s port %d\n", on, vm, e.Addr(), e.Port())
			}
			fmt.Fprintf(&filter, "pass in quick %s inet proto tcp from %s to %s port %d\n", on, vm, lk.gw, egressProxyPort)
			if p.DNS {
				fmt.Fprintf(&filter, "pass in quick %s inet proto { tcp udp } from %s to %s port %d\n",
					on, vm, lk.gw, egressDNSPort)
			} else {
				fmt.Fprintf(&filter, "block return in quick %s inet proto { tcp udp } from %s to any port 53\n", on, vm)
			}
			if deny := p.prefixes(false, true); len(deny) > 0 {
				fmt.Fprintf(&filter, "block return in quick %s inet from %s to { %s }\n", on, vm, strings.Join(deny, " "))
			}
			fmt.Fprintf(&filter, "block return in quick %s inet from %s to <zoo_d_%s>\n", on, vm, tag)
			if allow := p.prefixes(true, true); len(allow) > 0 {
				fmt.Fprintf(&filter, "pass in quick %s inet from %s to { %s }\n", on, vm, strings.Join(allow, " "))
			}
			fmt.Fprintf(&filter, "pass in quick %s inet from %s to <zoo_a_%s>\n", on, vm, tag)
			if p.Default == "deny" {
				links[lk.name] = true
				fmt.Fprintf(&filter, "block return in quick %s inet from %s\n", on, vm)
			}
		}
	}
	var head strings.Builder
	head.WriteString(tables.String())
	head.WriteString(rdr.String())
	head.WriteString("pass in quick inet proto udp from any port 68 to any port 67\n")
	// VM addresses are IPv4 here, so IPv6 from a VM on a filtered network would go around every rule above
	names := make([]string, 0, len(links))
	for name := range links {
		names = append(names, name)
	}
	slices.Sort(names)
	for _, name := range names {
		fmt.Fprintf(&head, "block return in quick on %s inet6\n", name)
	}
	head.WriteString(filter.String())
	return head.String()
}
