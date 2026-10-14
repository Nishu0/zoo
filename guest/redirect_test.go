package main

import (
	"errors"
	"testing"

	"github.com/coder/websocket"
)

func TestGuestOwnerRedirectPreservesPublicOrigin(test *testing.T) {
	for _, owner := range []string{"10.0.0.9", "2001:db8::9"} {
		redirected, accepted := guestOwnerURL("wss://zoo.example.test/api/guest/connect?extra=value", websocket.CloseError{Code: 4001, Reason: "owner=" + owner})
		if !accepted {
			test.Fatal("valid owner redirect rejected")
		}
		if owner == "10.0.0.9" && redirected != "wss://zoo.example.test/api/guest/connect?extra=value&owner=10.0.0.9" {
			test.Fatalf("redirect changed public origin: %s", redirected)
		}
	}
	for _, reason := range []string{"https://attacker.example/", "owner=attacker.example", "owner=127.0.0.1:9999", "owner=fe80::1%zone", "owner="} {
		if _, accepted := guestOwnerURL("wss://zoo.example/guest/connect", websocket.CloseError{Code: 4001, Reason: reason}); accepted {
			test.Fatal("unsafe owner redirect accepted")
		}
	}
	if _, accepted := guestOwnerURL("wss://zoo.example/guest/connect", errors.New("network failed")); accepted {
		test.Fatal("ordinary disconnect treated as owner handoff")
	}
}
