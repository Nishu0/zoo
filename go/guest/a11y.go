package main

import (
	"context"
	"encoding/json"
	"strings"
	"time"
)

// The a11y service reads one window's accessibility tree through the OS's own API: AT-SPI on Linux, AX on macOS
// and UI Automation on Windows. Every OS returns the same pre-order node list, which the API renders as an outline.

const (
	a11yMaxDepth = 40
	a11yMaxText  = 500
	a11yTimeout  = 20 * time.Second
)

func init() {
	handlers["a11y"] = a11yTree
}

type a11yArgs struct {
	App      string `json:"app"`
	Title    string `json:"title"`
	MaxNodes int    `json:"max_nodes"`
}

// a11yNode is one element, in pre-order with its depth.
type a11yNode struct {
	Depth  int      `json:"d"`
	Role   string   `json:"role"`
	Name   string   `json:"name"`
	Value  string   `json:"value,omitempty"`
	States []string `json:"states,omitempty"`
	Box    []int32  `json:"box,omitempty"`
}

type a11yResult struct {
	App       string     `json:"app"`
	Window    string     `json:"window"`
	Nodes     []a11yNode `json:"nodes"`
	Truncated bool       `json:"truncated"`
}

func a11yTree(c call) (any, []byte, error) {
	a := a11yArgs{MaxNodes: 300}
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	if a.MaxNodes <= 0 {
		a.MaxNodes = 300
	}
	ctx, cancel := context.WithTimeout(c.ctx, a11yTimeout)
	defer cancel()
	r, err := readTree(ctx, a)
	if err != nil {
		return nil, nil, err
	}
	if r.Nodes == nil {
		r.Nodes = []a11yNode{}
	}
	return r, nil, nil
}

// matches is a case-insensitive substring test; an empty filter matches everything.
func matches(s, filter string) bool {
	return filter == "" || strings.Contains(strings.ToLower(s), strings.ToLower(filter))
}

func clip(s string) string {
	s = strings.TrimSpace(s)
	if len(s) > a11yMaxText {
		s = s[:a11yMaxText]
	}
	return s
}
