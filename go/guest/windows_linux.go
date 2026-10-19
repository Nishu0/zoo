package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/jezek/xgb"
	"github.com/jezek/xgb/xproto"
)

type window struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	App   string `json:"app"`
	PID   uint32 `json:"pid"`
}

type windowArgs struct {
	Action  string  `json:"action"`
	ID      string  `json:"id"`
	Display string  `json:"display"`
	X       *int32  `json:"x"`
	Y       *int32  `json:"y"`
	Width   *uint32 `json:"width"`
	Height  *uint32 `json:"height"`
}

type windowBackend struct {
	conn  *xgb.Conn
	root  xproto.Window
	atoms map[string]xproto.Atom
}

func windowConn(display string) (*windowBackend, error) {
	conn, err := xgb.NewConnDisplay(display)
	if err != nil {
		return nil, fmt.Errorf("windows service unavailable on %s: %w", display, err)
	}
	backend := &windowBackend{conn: conn, root: xproto.Setup(conn).DefaultScreen(conn).Root, atoms: map[string]xproto.Atom{}}
	if err := backend.check(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("windows service unavailable on %s: %w", display, err)
	}
	return backend, nil
}

func (backend *windowBackend) check() error {
	for _, name := range []string{
		"_NET_SUPPORTING_WM_CHECK", "_NET_SUPPORTED", "_NET_CLIENT_LIST", "_NET_ACTIVE_WINDOW",
		"_NET_CLOSE_WINDOW", "_NET_WM_STATE", "_NET_WM_STATE_HIDDEN", "_NET_WM_STATE_MAXIMIZED_VERT",
		"_NET_WM_STATE_MAXIMIZED_HORZ", "_NET_MOVERESIZE_WINDOW", "_NET_WM_NAME", "UTF8_STRING",
		"_NET_WM_PID", "WM_CHANGE_STATE",
	} {
		reply, err := xproto.InternAtom(backend.conn, false, uint16(len(name)), name).Reply()
		if err != nil {
			return err
		}
		if reply == nil {
			return errors.New("X11 connection closed while resolving " + name)
		}
		backend.atoms[name] = reply.Atom
	}
	check, err := backend.words(backend.root, "_NET_SUPPORTING_WM_CHECK", xproto.AtomWindow)
	if err != nil {
		return err
	}
	if len(check) != 1 || check[0] == 0 {
		return errors.New("no EWMH window manager is running")
	}
	owner, err := backend.words(xproto.Window(check[0]), "_NET_SUPPORTING_WM_CHECK", xproto.AtomWindow)
	if err != nil {
		return err
	}
	if len(owner) != 1 || owner[0] != check[0] {
		return errors.New("invalid EWMH window manager")
	}
	supported, err := backend.words(backend.root, "_NET_SUPPORTED", xproto.AtomAtom)
	if err != nil {
		return err
	}
	for _, name := range []string{
		"_NET_CLIENT_LIST", "_NET_ACTIVE_WINDOW", "_NET_CLOSE_WINDOW", "_NET_WM_STATE",
		"_NET_WM_STATE_HIDDEN", "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ",
		"_NET_MOVERESIZE_WINDOW",
	} {
		if !slices.Contains(supported, uint32(backend.atoms[name])) {
			return errors.New("window manager does not support " + name)
		}
	}
	_, err = backend.clients()
	return err
}

func (backend *windowBackend) property(id xproto.Window, atom, kind xproto.Atom, format byte) ([]byte, error) {
	reply, err := xproto.GetProperty(backend.conn, false, id, atom, kind, 0, 16384).Reply()
	if err != nil {
		return nil, err
	}
	if reply == nil {
		return nil, errors.New("X11 connection closed while reading window property")
	}
	if reply.Type == xproto.AtomNone {
		return nil, nil
	}
	if reply.Type != kind || reply.Format != format || reply.BytesAfter != 0 {
		return nil, errors.New("invalid or oversized window property")
	}
	return reply.Value, nil
}

func (backend *windowBackend) words(id xproto.Window, name string, kind xproto.Atom) ([]uint32, error) {
	data, err := backend.property(id, backend.atoms[name], kind, 32)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", name, err)
	}
	if data == nil && name != "_NET_WM_PID" {
		return nil, errors.New("missing window manager property " + name)
	}
	words := make([]uint32, len(data)/4)
	for index := range words {
		words[index] = xgb.Get32(data[index*4:])
	}
	return words, nil
}

func (backend *windowBackend) clients() ([]uint32, error) {
	return backend.words(backend.root, "_NET_CLIENT_LIST", xproto.AtomWindow)
}

func (backend *windowBackend) list() ([]window, error) {
	clients, err := backend.clients()
	if err != nil {
		return nil, err
	}
	windows := make([]window, 0, len(clients))
	for _, id := range clients {
		client := xproto.Window(id)
		title, err := backend.property(client, backend.atoms["_NET_WM_NAME"], backend.atoms["UTF8_STRING"], 8)
		if _, closed := err.(xproto.WindowError); closed {
			continue
		}
		if err != nil {
			return nil, err
		}
		class, err := backend.property(client, xproto.AtomWmClass, xproto.AtomString, 8)
		if _, closed := err.(xproto.WindowError); closed {
			continue
		}
		if err != nil {
			return nil, err
		}
		pid, err := backend.words(client, "_NET_WM_PID", xproto.AtomCardinal)
		var closed xproto.WindowError
		if errors.As(err, &closed) {
			continue
		}
		if err != nil {
			return nil, err
		}
		entry := window{ID: strconv.FormatUint(uint64(id), 10), Title: strings.TrimRight(string(title), "\x00")}
		if parts := strings.Split(strings.TrimRight(string(class), "\x00"), "\x00"); len(parts) == 2 {
			entry.App = parts[1]
		}
		if len(pid) == 1 {
			entry.PID = pid[0]
		}
		windows = append(windows, entry)
	}
	return windows, nil
}

func (backend *windowBackend) message(id xproto.Window, name string, data [5]uint32) error {
	event := xproto.ClientMessageEvent{
		Format: 32, Window: id, Type: backend.atoms[name],
		Data: xproto.ClientMessageDataUnionData32New(data[:]),
	}
	return xproto.SendEventChecked(backend.conn, false, backend.root,
		xproto.EventMaskSubstructureRedirect|xproto.EventMaskSubstructureNotify, string(event.Bytes())).Check()
}

// desktopReadyOp waits until a window manager owns the display's root window (_NET_SUPPORTING_WM_CHECK), up to
// timeout_ms, so a boot reports a desktop that is usable rather than only an X server that is up.
func desktopReadyOp(request call) (any, []byte, error) {
	args := struct {
		Display   string `json:"display"`
		TimeoutMS int    `json:"timeout_ms"`
	}{Display: ":1", TimeoutMS: 30000}
	if err := json.Unmarshal(request.args, &args); err != nil {
		return nil, nil, err
	}
	deadline := time.Now().Add(time.Duration(args.TimeoutMS) * time.Millisecond)
	for {
		backend, err := windowConn(args.Display)
		if err == nil {
			backend.conn.Close()
			return map[string]any{"ready": true}, nil, nil
		}
		if time.Now().After(deadline) {
			return map[string]any{"ready": false, "error": err.Error()}, nil, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func windowOp(request call) (any, []byte, error) {
	args := windowArgs{Display: ":1"}
	if err := json.Unmarshal(request.args, &args); err != nil {
		return nil, nil, err
	}
	switch args.Action {
	case "list", "focus", "close", "minimize", "maximize", "unmaximize", "restore", "move", "resize":
	default:
		return nil, nil, errors.New("unknown window action " + args.Action)
	}
	if args.Action == "move" && (args.X == nil || args.Y == nil) {
		return nil, nil, errors.New("move requires x and y")
	}
	if args.Action == "resize" && (args.Width == nil || args.Height == nil || *args.Width == 0 || *args.Height == 0 || *args.Width > 65535 || *args.Height > 65535) {
		return nil, nil, errors.New("resize requires width and height in [1, 65535]")
	}
	backend, err := windowConn(args.Display)
	if err != nil {
		return nil, nil, err
	}
	defer backend.conn.Close()
	if args.Action == "list" {
		windows, err := backend.list()
		if err != nil {
			return nil, nil, err
		}
		return map[string]any{"windows": windows}, nil, nil
	}
	number, err := strconv.ParseUint(args.ID, 10, 32)
	if err != nil || number == 0 {
		return nil, nil, errors.New("invalid window id (use an id from windows_list)")
	}
	clients, err := backend.clients()
	if err != nil {
		return nil, nil, err
	}
	if !slices.Contains(clients, uint32(number)) {
		return nil, nil, errors.New("no window " + args.ID + " (use an id from windows_list)")
	}
	id := xproto.Window(number)
	switch args.Action {
	case "focus", "restore":
		err = xproto.MapWindowChecked(backend.conn, id).Check()
		if err == nil {
			err = backend.message(id, "_NET_ACTIVE_WINDOW", [5]uint32{2, xproto.TimeCurrentTime})
		}
	case "close":
		err = backend.message(id, "_NET_CLOSE_WINDOW", [5]uint32{xproto.TimeCurrentTime, 2})
	case "minimize":
		err = backend.message(id, "WM_CHANGE_STATE", [5]uint32{3})
	case "maximize", "unmaximize":
		state := uint32(1)
		if args.Action == "unmaximize" {
			state = 0
		}
		err = backend.message(id, "_NET_WM_STATE", [5]uint32{
			state, uint32(backend.atoms["_NET_WM_STATE_MAXIMIZED_VERT"]), uint32(backend.atoms["_NET_WM_STATE_MAXIMIZED_HORZ"]), 2,
		})
	case "move":
		err = backend.message(id, "_NET_MOVERESIZE_WINDOW", [5]uint32{1 | 1<<8 | 1<<9 | 2<<12, uint32(*args.X), uint32(*args.Y)})
	case "resize":
		err = backend.message(id, "_NET_MOVERESIZE_WINDOW", [5]uint32{1 | 1<<10 | 1<<11 | 2<<12, 0, 0, *args.Width, *args.Height})
	}
	if err != nil {
		return nil, nil, fmt.Errorf("window %s: %w", args.Action, err)
	}
	return map[string]any{}, nil, nil
}
