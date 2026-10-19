package main

import (
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"unsafe"

	"github.com/ebitengine/purego"
)

// The windows service lists and moves top-level windows through AX, so it needs only the Accessibility grant the
// a11y service has. The System Events scripts the API falls back to (over SSH) also need an Automation consent.
// Window ids are "App:N", the Nth window of the app, as those scripts number them.

const (
	cgAllWindows = 0 // kCGWindowListOptionAll: minimized and hidden windows too
	menuBar      = 25
)

var (
	axSetAttribute  func(uintptr, uintptr, uintptr) int32
	axPerformAction func(uintptr, uintptr) int32
	axValueCreate   func(uint32, unsafe.Pointer) uintptr
	cgMainDisplayID func() uint32
	cgDisplayWide   func(uint32) uint
	cgDisplayHigh   func(uint32) uint
	cfTrue, cfFalse uintptr
)

func init() {
	if axLoaded && axIsProcessTrusted() && loadWindows() == nil {
		services = append(services, "windows")
		handlers["window"] = windowOp
	}
}

func loadWindows() error {
	cf, err := purego.Dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", purego.RTLD_NOW|purego.RTLD_GLOBAL)
	if err != nil {
		return err
	}
	as, err := purego.Dlopen("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices", purego.RTLD_NOW|purego.RTLD_GLOBAL)
	if err != nil {
		return err
	}
	for name, fn := range map[string]any{
		"AXUIElementSetAttributeValue": &axSetAttribute, "AXUIElementPerformAction": &axPerformAction,
		"AXValueCreate": &axValueCreate, "CGMainDisplayID": &cgMainDisplayID,
		"CGDisplayPixelsWide": &cgDisplayWide, "CGDisplayPixelsHigh": &cgDisplayHigh,
	} {
		purego.RegisterLibFunc(fn, as, name)
	}
	for name, ref := range map[string]*uintptr{"kCFBooleanTrue": &cfTrue, "kCFBooleanFalse": &cfFalse} {
		sym, err := purego.Dlsym(cf, name)
		if err != nil {
			return err
		}
		// the symbol is the address of a CFBooleanRef variable
		*ref = **(**uintptr)(unsafe.Pointer(&sym))
	}
	return nil
}

type macWindow struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	App   string `json:"app"`
	PID   int32  `json:"pid"`
}

type macWindowArgs struct {
	Action string `json:"action"`
	ID     string `json:"id"`
}

func windowOp(c call) (any, []byte, error) {
	var a macWindowArgs
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	axMu.Lock()
	defer axMu.Unlock()
	if a.Action == "list" {
		var all []macWindow
		for _, app := range axApps() {
			all = append(all, appWindows(app.pid, app.name)...)
		}
		return map[string]any{"windows": all}, nil, nil
	}
	app, win, err := axWindowByID(a.ID)
	if err != nil {
		return nil, nil, err
	}
	defer cfRelease(app)
	defer cfRelease(win)
	var code int32
	switch a.Action {
	case "focus":
		code = first(axSetAttribute(app, cfstr("AXFrontmost"), cfTrue), axPerformAction(win, cfstr("AXRaise")))
	case "restore":
		code = first(axSetAttribute(win, cfstr("AXMinimized"), cfFalse),
			axSetAttribute(app, cfstr("AXFrontmost"), cfTrue), axPerformAction(win, cfstr("AXRaise")))
	case "minimize":
		code = axSetAttribute(win, cfstr("AXMinimized"), cfTrue)
	case "maximize":
		w, h := screenSize()
		code = frame(win, 0, menuBar, w, h-menuBar)
	case "unmaximize":
		w, h := screenSize()
		code = frame(win, w/6, h/6, w*2/3, h*2/3)
	case "close":
		button := attribute(win, "AXCloseButton")
		if button == 0 {
			return nil, nil, errors.New("window " + a.ID + " has no close button")
		}
		defer cfRelease(button)
		code = axPerformAction(button, cfstr("AXPress"))
	default:
		return nil, nil, errors.New("unknown window action " + a.Action)
	}
	if code != 0 {
		return nil, nil, errors.New("macOS refused to " + a.Action + " window " + a.ID + " (AX error " + strconv.Itoa(int(code)) + ")")
	}
	return map[string]any{}, nil, nil
}

func first(codes ...int32) int32 {
	for _, c := range codes {
		if c != 0 {
			return c
		}
	}
	return 0
}

func screenSize() (float64, float64) {
	display := cgMainDisplayID()
	return float64(cgDisplayWide(display)), float64(cgDisplayHigh(display))
}

// frame moves the window, then sizes it.
func frame(win uintptr, x, y, w, h float64) int32 {
	point, size := [2]float64{x, y}, [2]float64{w, h}
	pos := axValueCreate(axValueCGPointType, unsafe.Pointer(&point))
	defer release(pos)
	dim := axValueCreate(axValueCGSizeType, unsafe.Pointer(&size))
	defer release(dim)
	return first(axSetAttribute(win, cfstr("AXPosition"), pos), axSetAttribute(win, cfstr("AXSize"), dim))
}

type axApp struct {
	pid  int32
	name string
}

// axApps is every app with a normal-layer window, minimized and hidden ones included, in front-to-back order.
func axApps() []axApp {
	info := cgWindowListCopyWindowInfo(cgAllWindows, 0)
	if info == 0 {
		return nil
	}
	defer cfRelease(info)
	var apps []axApp
	seen := map[int32]bool{}
	for i := 0; i < cfArrayGetCount(info); i++ {
		entry := cfArrayGetValueAtIndex(info, i)
		if layer, _ := number(cfDictionaryGetValue(entry, cfstr("kCGWindowLayer"))); layer != 0 {
			continue
		}
		var pid int32
		if !cfNumberGetValue(cfDictionaryGetValue(entry, cfstr("kCGWindowOwnerPID")), cfNumberSInt32Type, unsafe.Pointer(&pid)) || seen[pid] {
			continue
		}
		seen[pid] = true
		apps = append(apps, axApp{pid, goString(cfDictionaryGetValue(entry, cfstr("kCGWindowOwnerName")))})
	}
	return apps
}

func appWindows(pid int32, name string) []macWindow {
	el := axCreateApplication(pid)
	defer cfRelease(el)
	windows := attribute(el, "AXWindows")
	if windows == 0 {
		return nil
	}
	defer cfRelease(windows)
	var out []macWindow
	for i := 0; i < cfArrayGetCount(windows); i++ {
		title := attribute(cfArrayGetValueAtIndex(windows, i), "AXTitle")
		out = append(out, macWindow{ID: name + ":" + strconv.Itoa(i+1), Title: goString(title), App: name, PID: pid})
		release(title)
	}
	return out
}

// axWindowByID finds "App:N" among the apps of that name; the caller releases the app and the window.
func axWindowByID(id string) (uintptr, uintptr, error) {
	i := strings.LastIndex(id, ":")
	name, n := id[:max(i, 0)], id[i+1:]
	index, err := strconv.Atoi(n)
	if name == "" || err != nil || index < 1 {
		return 0, 0, errors.New("window_id must look like 'App:1' (from windows_list)")
	}
	for _, app := range axApps() {
		if app.name != name {
			continue
		}
		el := axCreateApplication(app.pid)
		windows := attribute(el, "AXWindows")
		if windows != 0 && index <= cfArrayGetCount(windows) {
			win := cfRetain(cfArrayGetValueAtIndex(windows, index-1))
			cfRelease(windows)
			return el, win, nil
		}
		release(windows)
		cfRelease(el)
	}
	return 0, 0, errors.New("no window " + id + " (use an id from windows_list)")
}
