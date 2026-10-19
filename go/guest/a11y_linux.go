package main

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/godbus/dbus/v5"
)

// AT-SPI lives on its own D-Bus bus, whose address the session bus hands out (org.a11y.Bus); the image runs one
// session bus for the desktop and the guest. Each element takes several calls, so they go out together: the app
// answers them back to back instead of waiting a round trip for each, and sibling subtrees are read in parallel.

const (
	atspiAccessible = "org.a11y.atspi.Accessible"
	atspiComponent  = "org.a11y.atspi.Component"
	atspiText       = "org.a11y.atspi.Text"
	atspiValue      = "org.a11y.atspi.Value"
	dbusProperties  = "org.freedesktop.DBus.Properties"
	// calls in flight at once
	a11yParallel = 64
)

// AtspiStateType bit positions
const (
	stateActive    = 1
	stateChecked   = 4
	stateCollapsed = 5
	stateEditable  = 7
	stateEnabled   = 8
	stateExpanded  = 10
	stateFocused   = 12
	stateSelected  = 23
	stateShowing   = 25
)

var (
	a11yMu   sync.Mutex
	a11yConn *dbus.Conn
)

type accessible struct {
	Dest string
	Path dbus.ObjectPath
}

var registryRoot = accessible{"org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root"}

func readTree(ctx context.Context, a a11yArgs) (a11yResult, error) {
	a11yMu.Lock()
	defer a11yMu.Unlock()
	conn, err := a11yBus()
	if err != nil {
		return a11yResult{}, err
	}
	w := &walker{ctx: ctx, conn: conn, max: int64(a.MaxNodes), slots: make(chan struct{}, a11yParallel)}
	app, win, title, err := w.window(a.App, a.Title)
	if err != nil {
		if !conn.Connected() {
			a11yConn = nil
		}
		return a11yResult{}, err
	}
	var nodes []a11yNode
	w.walk(win, 0).flatten(&nodes)
	return a11yResult{App: app, Window: title, Nodes: nodes, Truncated: w.truncated.Load()}, nil
}

// a11yBus connects to the AT-SPI bus once, after turning accessibility on so toolkits start exporting their trees.
func a11yBus() (*dbus.Conn, error) {
	if a11yConn != nil && a11yConn.Connected() {
		return a11yConn, nil
	}
	session, err := dbus.ConnectSessionBus()
	if err != nil {
		return nil, errors.New("no session bus: " + err.Error())
	}
	defer session.Close()
	bus := session.Object("org.a11y.Bus", "/org/a11y/bus")
	bus.SetProperty("org.a11y.Status.IsEnabled", dbus.MakeVariant(true))
	var address string
	if err := bus.Call("org.a11y.Bus.GetAddress", 0).Store(&address); err != nil {
		return nil, errors.New("the accessibility bus isn't running: " + err.Error())
	}
	conn, err := dbus.Connect(address)
	if err != nil {
		return nil, err
	}
	a11yConn = conn
	return conn, nil
}

type walker struct {
	ctx       context.Context
	conn      *dbus.Conn
	max       int64
	count     atomic.Int64
	truncated atomic.Bool
	slots     chan struct{}
}

type subtree struct {
	node     *a11yNode
	children []*subtree
}

func (t *subtree) flatten(out *[]a11yNode) {
	if t == nil {
		return
	}
	if t.node != nil {
		*out = append(*out, *t.node)
	}
	for _, c := range t.children {
		c.flatten(out)
	}
}

func (w *walker) send(o accessible, method string, args ...any) *dbus.Call {
	return w.conn.Object(o.Dest, o.Path).GoWithContext(w.ctx, method, 0, make(chan *dbus.Call, 1), args...)
}

func wait(c *dbus.Call, out ...any) bool {
	<-c.Done
	return c.Err == nil && c.Store(out...) == nil
}

func (w *walker) children(o accessible) []accessible {
	var out []accessible
	wait(w.send(o, atspiAccessible+".GetChildren"), &out)
	return out
}

func (w *walker) name(o accessible) string {
	var v dbus.Variant
	if !wait(w.send(o, dbusProperties+".Get", atspiAccessible, "Name"), &v) {
		return ""
	}
	s, _ := v.Value().(string)
	return s
}

func (w *walker) states(o accessible) [2]uint32 {
	var bits []uint32
	wait(w.send(o, atspiAccessible+".GetState"), &bits)
	var out [2]uint32
	copy(out[:], bits)
	return out
}

func has(bits [2]uint32, state uint) bool {
	return bits[state/32]&(1<<(state%32)) != 0
}

// window picks the window to read: the first whose app and title contain the filters, or the active one.
func (w *walker) window(app, title string) (string, accessible, string, error) {
	var seen []string
	for _, a := range w.children(registryRoot) {
		appName := w.name(a)
		if !matches(appName, app) {
			continue
		}
		for _, win := range w.children(a) {
			bits := w.states(win)
			if !has(bits, stateShowing) {
				continue
			}
			winName := w.name(win)
			seen = append(seen, appName+": "+winName)
			if !matches(winName, title) {
				continue
			}
			if app != "" || title != "" || has(bits, stateActive) {
				return appName, win, winName, nil
			}
		}
	}
	if err := w.ctx.Err(); err != nil {
		return "", accessible{}, "", err
	}
	if len(seen) == 0 {
		return "", accessible{}, "", errors.New("no accessible windows are showing")
	}
	return "", accessible{}, "", errors.New("no matching window; pass app or title, one of: " + strings.Join(seen, "; "))
}

// walk reads one element with all its calls in flight at once, then its children in parallel.
func (w *walker) walk(o accessible, depth int) *subtree {
	if w.count.Add(1) > w.max || w.ctx.Err() != nil {
		w.truncated.Store(true)
		return nil
	}
	w.slots <- struct{}{}
	var (
		name, role string
		nameV      dbus.Variant
		bits       []uint32
		ifaces     []string
		kids       []accessible
		box        struct{ X, Y, W, H int32 }
	)
	nameC := w.send(o, dbusProperties+".Get", atspiAccessible, "Name")
	roleC := w.send(o, atspiAccessible+".GetRoleName")
	stateC := w.send(o, atspiAccessible+".GetState")
	ifaceC := w.send(o, atspiAccessible+".GetInterfaces")
	kidsC := w.send(o, atspiAccessible+".GetChildren")
	boxC := w.send(o, atspiComponent+".GetExtents", uint32(0))
	if wait(nameC, &nameV) {
		name, _ = nameV.Value().(string)
	}
	wait(roleC, &role)
	wait(stateC, &bits)
	wait(ifaceC, &ifaces)
	wait(kidsC, &kids)
	gotBox := wait(boxC, &box)
	var state [2]uint32
	copy(state[:], bits)
	if depth > 0 && !has(state, stateShowing) {
		<-w.slots
		w.count.Add(-1)
		return nil
	}
	n := &a11yNode{Depth: depth, Name: name, Role: role}
	if gotBox && box.W > 0 && box.H > 0 {
		n.Box = []int32{box.X, box.Y, box.W, box.H}
	}
	for _, s := range []struct {
		bit  uint
		name string
	}{{stateFocused, "focused"}, {stateSelected, "selected"}, {stateChecked, "checked"},
		{stateExpanded, "expanded"}, {stateCollapsed, "collapsed"}, {stateEditable, "editable"}} {
		if has(state, s.bit) {
			n.States = append(n.States, s.name)
		}
	}
	if !has(state, stateEnabled) {
		n.States = append(n.States, "disabled")
	}
	for _, iface := range ifaces {
		switch iface {
		case atspiText:
			// static labels carry their text as the name; this is for fields and documents
			if role != "password text" && (has(state, stateEditable) || name == "") {
				var text string
				wait(w.send(o, atspiText+".GetText", int32(0), int32(a11yMaxText)), &text)
				n.Value = clip(text)
			}
		case atspiValue:
			var v dbus.Variant
			if wait(w.send(o, dbusProperties+".Get", atspiValue, "CurrentValue"), &v) {
				if f, ok := v.Value().(float64); ok {
					n.Value = strconv.FormatFloat(f, 'f', -1, 64)
				}
			}
		}
	}
	<-w.slots
	t := &subtree{node: n}
	if depth >= a11yMaxDepth || len(kids) == 0 {
		return t
	}
	t.children = make([]*subtree, len(kids))
	var wg sync.WaitGroup
	for i, kid := range kids {
		wg.Add(1)
		go func() {
			defer wg.Done()
			t.children[i] = w.walk(kid, depth+1)
		}()
	}
	wg.Wait()
	return t
}
