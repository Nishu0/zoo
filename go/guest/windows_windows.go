package main

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

// The windows service lists and moves top-level windows. The guest runs in the user's desktop session, so it sees
// the same windows the user does; it replaces windows/agent.ps1's ZooWin class.

var (
	user32                = windows.NewLazySystemDLL("user32.dll")
	procEnumWindows       = user32.NewProc("EnumWindows")
	procIsWindowVisible   = user32.NewProc("IsWindowVisible")
	procIsWindow          = user32.NewProc("IsWindow")
	procGetWindowTextW    = user32.NewProc("GetWindowTextW")
	procGetWindowTextLenW = user32.NewProc("GetWindowTextLengthW")
	procGetWindow         = user32.NewProc("GetWindow")
	procGetWindowLongW    = user32.NewProc("GetWindowLongW")
	procShowWindow        = user32.NewProc("ShowWindow")
	procSetForeground     = user32.NewProc("SetForegroundWindow")
	procBringWindowToTop  = user32.NewProc("BringWindowToTop")
	procPostMessageW      = user32.NewProc("PostMessageW")
	procKeybdEvent        = user32.NewProc("keybd_event")
	procDwmGetAttribute   = windows.NewLazySystemDLL("dwmapi.dll").NewProc("DwmGetWindowAttribute")
)

const (
	gwOwner         = 4
	gwlExStyle      = -20
	wsExToolWindow  = 0x80
	dwmwaCloaked    = 14
	swRestore       = 9
	wmClose         = 0x10
	vkMenu          = 0x12
	keyeventfKeyUp  = 2
	processNameSize = 1024
)

func init() {
	handlers["window"] = windowOp
}

type window struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	App   string `json:"app"`
	PID   int    `json:"pid"`
}

type windowArgs struct {
	Action string `json:"action"`
	ID     string `json:"id"`
	// cmd is a ShowWindow command, such as 3 (maximize), 6 (minimize) or 9 (restore)
	Cmd int `json:"cmd"`
}

func windowOp(c call) (any, []byte, error) {
	var a windowArgs
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	if a.Action == "list" {
		return map[string]any{"windows": listWindows()}, nil, nil
	}
	id, err := strconv.ParseInt(a.ID, 10, 64)
	hwnd := uintptr(id)
	if err != nil || !call1(procIsWindow, hwnd) {
		return nil, nil, errors.New("no window " + a.ID + " (use an id from windows_list)")
	}
	switch a.Action {
	case "show":
		procShowWindow.Call(hwnd, uintptr(a.Cmd))
	case "focus":
		procShowWindow.Call(hwnd, swRestore)
		// Windows only lets the process that last had input change the foreground; a synthetic Alt counts.
		procKeybdEvent.Call(vkMenu, 0, 0, 0)
		procKeybdEvent.Call(vkMenu, 0, keyeventfKeyUp, 0)
		procBringWindowToTop.Call(hwnd)
		procSetForeground.Call(hwnd)
	case "close":
		procPostMessageW.Call(hwnd, wmClose, 0, 0)
	default:
		return nil, nil, errors.New("unknown window action " + a.Action)
	}
	return map[string]any{}, nil, nil
}

func call1(p *windows.LazyProc, args ...uintptr) bool {
	r, _, _ := p.Call(args...)
	return r != 0
}

// listWindows returns the windows a taskbar would show: visible, unowned, titled, not tool windows, not cloaked.
func listWindows() []window {
	found := []window{}
	exStyle := int32(gwlExStyle)
	names := map[uint32]string{}
	cb := syscall.NewCallback(func(hwnd, _ uintptr) uintptr {
		if !call1(procIsWindowVisible, hwnd) || call1(procGetWindow, hwnd, gwOwner) {
			return 1
		}
		if style, _, _ := procGetWindowLongW.Call(hwnd, uintptr(exStyle)); style&wsExToolWindow != 0 {
			return 1
		}
		var cloaked uint32
		if r, _, _ := procDwmGetAttribute.Call(hwnd, dwmwaCloaked, uintptr(unsafe.Pointer(&cloaked)), 4); r == 0 && cloaked != 0 {
			return 1
		}
		n, _, _ := procGetWindowTextLenW.Call(hwnd)
		if n == 0 {
			return 1
		}
		buf := make([]uint16, n+1)
		procGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), n+1)
		var pid uint32
		windows.GetWindowThreadProcessId(windows.HWND(hwnd), &pid)
		name, ok := names[pid]
		if !ok {
			name = processName(pid)
			names[pid] = name
		}
		found = append(found, window{
			ID: strconv.FormatInt(int64(hwnd), 10), Title: windows.UTF16ToString(buf), App: name, PID: int(pid),
		})
		return 1
	})
	procEnumWindows.Call(cb, 0)
	return found
}

// processName matches .NET's Process.ProcessName: the image's file name without .exe.
func processName(pid uint32) string {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return ""
	}
	defer windows.CloseHandle(h)
	buf := make([]uint16, processNameSize)
	size := uint32(len(buf))
	if windows.QueryFullProcessImageName(h, 0, &buf[0], &size) != nil {
		return ""
	}
	name := filepath.Base(windows.UTF16ToString(buf[:size]))
	if strings.EqualFold(filepath.Ext(name), ".exe") {
		name = name[:len(name)-4]
	}
	return name
}
