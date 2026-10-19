package main

import (
	"net/netip"
	"os"
	"os/exec"
	"testing"
	"time"
)

// TestRenderedRulesetLoads hands the rendered ruleset to the real nft, in a network namespace of its own: string
// checks can't catch a chain named after an nft keyword. It needs root and nft, so it runs where ZOO_TEST_NFT=1 (CI
// runs it under sudo).
func TestRenderedRulesetLoads(t *testing.T) {
	if os.Getenv("ZOO_TEST_NFT") != "1" {
		t.Skip("set ZOO_TEST_NFT=1, as root with nft installed")
	}
	deny := testPolicy(t, "deny", egressRule{"domain", "github.com", "allow"}, egressRule{"cidr", "10.1.0.0/16", "allow"},
		egressRule{"ip", "9.9.9.9", "deny"}, egressRule{"cidr", "2001:db8::/32", "allow"})
	open := testPolicy(t, "allow", egressRule{"cidr", "9.9.9.0/24", "deny"})
	open.ID, open.Addrs = "open", []string{"172.18.0.6"}
	if err := open.compile(); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	l := learned{deny.tag(): {netip.MustParseAddr("140.82.112.3"): {true, now.Add(time.Minute)}}}
	for _, policies := range [][]*egressPolicy{{deny, open}, {}} {
		script := renderNft(policies, l, now)
		path := t.TempDir() + "/ruleset.nft"
		if err := os.WriteFile(path, []byte(script), 0o600); err != nil {
			t.Fatal(err)
		}
		// loaded twice in one namespace: the second load replaces the first, as each reload does
		cmd := exec.Command("unshare", "--net", "sh", "-c", `nft -f "$0" && nft -f "$0"`, path)
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("nft refused the ruleset: %v\n%s\n%s", err, out, script)
		}
	}
}
