package main

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"sync"
	"unsafe"

	"github.com/ebitengine/purego"
)

// The AX API, called through purego so the darwin build stays free of cgo. Each element costs one call into its
// app (AXUIElementCopyMultipleAttributeValues reads every attribute below, children included, at once).
//
// macOS only lets a process use AX when its responsible process has the Accessibility permission. The LaunchAgent
// starts the guest under /bin/zsh, which is Apple-signed and so keeps its grant when the guest binary is replaced;
// the guest offers a11y only once that grant is in place (macos/README.md, step 4).

const (
	cfStringEncodingUTF8 = 0x08000100
	cfNumberDoubleType   = 13
	cfNumberSInt32Type   = 3
	axValueCGPointType   = 1
	axValueCGSizeType    = 2
	axMessagingTimeout   = 2
	// kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
	cgOnScreenWindows = 1 | 16
)

var (
	cfRelease                   func(uintptr)
	cfRetain                    func(uintptr) uintptr
	cfGetTypeID                 func(uintptr) uint
	cfStringGetTypeID           func() uint
	cfBooleanGetTypeID          func() uint
	cfNumberGetTypeID           func() uint
	cfArrayGetTypeID            func() uint
	cfStringCreateWithCString   func(uintptr, string, uint32) uintptr
	cfStringGetLength           func(uintptr) int
	cfStringGetMaximumSize      func(int, uint32) int
	cfStringGetCString          func(uintptr, *byte, int, uint32) bool
	cfArrayGetCount             func(uintptr) int
	cfArrayGetValueAtIndex      func(uintptr, int) uintptr
	cfArrayCreate               func(uintptr, *uintptr, int, uintptr) uintptr
	cfBooleanGetValue           func(uintptr) bool
	cfNumberGetValue            func(uintptr, int, unsafe.Pointer) bool
	cfDictionaryGetValue        func(uintptr, uintptr) uintptr
	axIsProcessTrusted          func() bool
	axCreateApplication         func(int32) uintptr
	axCreateSystemWide          func() uintptr
	axCopyAttributeValue        func(uintptr, uintptr, *uintptr) int32
	axCopyMultipleAttributes    func(uintptr, uintptr, uint32, *uintptr) int32
	axSetMessagingTimeout       func(uintptr, float32) int32
	axValueGetTypeID            func() uint
	axValueGetType              func(uintptr) uint32
	axValueGetValue             func(uintptr, uint32, unsafe.Pointer) bool
	cgWindowListCopyWindowInfo  func(uint32, uint32) uintptr
	cfTypeArrayCallBacks        uintptr
	axLoaded                    bool
	axMu                        sync.Mutex
	cfStrings                   = map[string]uintptr{}
	axAttributes                uintptr
	stringID, boolID, numberID  uint
	arrayID, axValueID          uint
	errNoAccessibilityPermisson = errors.New("zoo-guest lacks the Accessibility permission; grant it to /bin/zsh (macos/README.md, step 4)")
)

// attributes read for every element, in this order
var axAttributeNames = []string{
	"AXRole", "AXRoleDescription", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPosition", "AXSize",
	"AXFocused", "AXSelected", "AXEnabled", "AXExpanded", "AXChildren",
}

const (
	attrRole = iota
	attrRoleDescription
	attrSubrole
	attrTitle
	attrDescription
	attrValue
	attrPosition
	attrSize
	attrFocused
	attrSelected
	attrEnabled
	attrExpanded
	attrChildren
)

func init() {
	if loadAX() == nil && axIsProcessTrusted() {
		services = append(services, "a11y")
	}
}

func loadAX() error {
	cf, err := purego.Dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", purego.RTLD_NOW|purego.RTLD_GLOBAL)
	if err != nil {
		return err
	}
	as, err := purego.Dlopen("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices", purego.RTLD_NOW|purego.RTLD_GLOBAL)
	if err != nil {
		return err
	}
	for name, fn := range map[string]any{
		"CFRelease": &cfRelease, "CFRetain": &cfRetain, "CFGetTypeID": &cfGetTypeID,
		"CFStringGetTypeID": &cfStringGetTypeID, "CFBooleanGetTypeID": &cfBooleanGetTypeID,
		"CFNumberGetTypeID": &cfNumberGetTypeID, "CFArrayGetTypeID": &cfArrayGetTypeID,
		"CFStringCreateWithCString": &cfStringCreateWithCString, "CFStringGetLength": &cfStringGetLength,
		"CFStringGetMaximumSizeForEncoding": &cfStringGetMaximumSize, "CFStringGetCString": &cfStringGetCString,
		"CFArrayGetCount": &cfArrayGetCount, "CFArrayGetValueAtIndex": &cfArrayGetValueAtIndex,
		"CFArrayCreate": &cfArrayCreate, "CFBooleanGetValue": &cfBooleanGetValue,
		"CFNumberGetValue": &cfNumberGetValue, "CFDictionaryGetValue": &cfDictionaryGetValue,
	} {
		purego.RegisterLibFunc(fn, cf, name)
	}
	for name, fn := range map[string]any{
		"AXIsProcessTrusted": &axIsProcessTrusted, "AXUIElementCreateApplication": &axCreateApplication,
		"AXUIElementCreateSystemWide": &axCreateSystemWide, "AXUIElementCopyAttributeValue": &axCopyAttributeValue,
		"AXUIElementCopyMultipleAttributeValues": &axCopyMultipleAttributes,
		"AXUIElementSetMessagingTimeout":         &axSetMessagingTimeout, "AXValueGetTypeID": &axValueGetTypeID,
		"AXValueGetType": &axValueGetType, "AXValueGetValue": &axValueGetValue,
		"CGWindowListCopyWindowInfo": &cgWindowListCopyWindowInfo,
	} {
		purego.RegisterLibFunc(fn, as, name)
	}
	if cfTypeArrayCallBacks, err = purego.Dlsym(cf, "kCFTypeArrayCallBacks"); err != nil {
		return err
	}
	stringID, boolID, numberID = cfStringGetTypeID(), cfBooleanGetTypeID(), cfNumberGetTypeID()
	arrayID, axValueID = cfArrayGetTypeID(), axValueGetTypeID()
	names := make([]uintptr, len(axAttributeNames))
	for i, n := range axAttributeNames {
		names[i] = cfstr(n)
	}
	axAttributes = cfArrayCreate(0, &names[0], len(names), cfTypeArrayCallBacks)
	// a hung app costs this long, not the default six seconds
	system := axCreateSystemWide()
	axSetMessagingTimeout(system, axMessagingTimeout)
	cfRelease(system)
	axLoaded = true
	return nil
}

// cfstr is a CFString kept for the life of the process.
func cfstr(s string) uintptr {
	if ref, ok := cfStrings[s]; ok {
		return ref
	}
	ref := cfStringCreateWithCString(0, s, cfStringEncodingUTF8)
	cfStrings[s] = ref
	return ref
}

func goString(ref uintptr) string {
	if ref == 0 || cfGetTypeID(ref) != stringID {
		return ""
	}
	size := cfStringGetMaximumSize(cfStringGetLength(ref), cfStringEncodingUTF8) + 1
	buf := make([]byte, size)
	if !cfStringGetCString(ref, &buf[0], size, cfStringEncodingUTF8) {
		return ""
	}
	for i, b := range buf {
		if b == 0 {
			return string(buf[:i])
		}
	}
	return string(buf)
}

func number(ref uintptr) (float64, bool) {
	var f float64
	if ref == 0 || cfGetTypeID(ref) != numberID || !cfNumberGetValue(ref, cfNumberDoubleType, unsafe.Pointer(&f)) {
		return 0, false
	}
	return f, true
}

func boolean(ref uintptr) bool {
	if ref == 0 {
		return false
	}
	if cfGetTypeID(ref) == boolID {
		return cfBooleanGetValue(ref)
	}
	f, ok := number(ref)
	return ok && f != 0
}

// attribute is one attribute of an element, which the caller releases.
func attribute(el uintptr, name string) uintptr {
	var out uintptr
	if axCopyAttributeValue(el, cfstr(name), &out) != 0 {
		return 0
	}
	return out
}

func readTree(ctx context.Context, a a11yArgs) (a11yResult, error) {
	axMu.Lock()
	defer axMu.Unlock()
	if !axLoaded {
		if err := loadAX(); err != nil {
			return a11yResult{}, err
		}
	}
	if !axIsProcessTrusted() {
		return a11yResult{}, errNoAccessibilityPermisson
	}
	app, win, err := pickAXWindow(a.App, a.Title)
	if err != nil {
		return a11yResult{}, err
	}
	defer cfRelease(win)
	w := axWalker{ctx: ctx, max: a.MaxNodes}
	w.walk(win, 0)
	title := attribute(win, "AXTitle")
	defer release(title)
	return a11yResult{App: app, Window: goString(title), Nodes: w.nodes, Truncated: w.truncated}, nil
}

func release(ref uintptr) {
	if ref != 0 {
		cfRelease(ref)
	}
}

// pickAXWindow is the focused window, or the first on-screen window whose app and title contain the filters. The
// caller releases the window.
func pickAXWindow(app, title string) (string, uintptr, error) {
	if app == "" && title == "" {
		system := axCreateSystemWide()
		defer cfRelease(system)
		focused := attribute(system, "AXFocusedApplication")
		if focused == 0 {
			return "", 0, errors.New("no app has the focus; pass app or title")
		}
		defer cfRelease(focused)
		name := attribute(focused, "AXTitle")
		defer release(name)
		win := attribute(focused, "AXFocusedWindow")
		if win == 0 {
			win = attribute(focused, "AXMainWindow")
		}
		if win == 0 {
			return "", 0, errors.New("the focused app has no window; pass app or title")
		}
		return goString(name), win, nil
	}
	info := cgWindowListCopyWindowInfo(cgOnScreenWindows, 0)
	if info == 0 {
		return "", 0, errors.New("couldn't list windows")
	}
	defer cfRelease(info)
	var seen []string
	done := map[int32]bool{}
	for i := 0; i < cfArrayGetCount(info); i++ {
		entry := cfArrayGetValueAtIndex(info, i)
		if layer, _ := number(cfDictionaryGetValue(entry, cfstr("kCGWindowLayer"))); layer != 0 {
			continue
		}
		var pid int32
		if !cfNumberGetValue(cfDictionaryGetValue(entry, cfstr("kCGWindowOwnerPID")), cfNumberSInt32Type, unsafe.Pointer(&pid)) || done[pid] {
			continue
		}
		done[pid] = true
		owner := goString(cfDictionaryGetValue(entry, cfstr("kCGWindowOwnerName")))
		if !matches(owner, app) {
			seen = append(seen, owner)
			continue
		}
		if win := findWindow(pid, title, owner, &seen); win != 0 {
			return owner, win, nil
		}
	}
	return "", 0, errors.New("no matching window; pass app or title, one of: " + strings.Join(seen, "; "))
}

func findWindow(pid int32, title, owner string, seen *[]string) uintptr {
	appEl := axCreateApplication(pid)
	defer cfRelease(appEl)
	windows := attribute(appEl, "AXWindows")
	if windows == 0 {
		return 0
	}
	defer cfRelease(windows)
	for i := 0; i < cfArrayGetCount(windows); i++ {
		win := cfArrayGetValueAtIndex(windows, i)
		name := attribute(win, "AXTitle")
		text := goString(name)
		release(name)
		*seen = append(*seen, owner+": "+text)
		if matches(text, title) {
			return cfRetain(win)
		}
	}
	return 0
}

type axWalker struct {
	ctx       context.Context
	max       int
	nodes     []a11yNode
	truncated bool
}

func (w *axWalker) walk(el uintptr, depth int) {
	if len(w.nodes) >= w.max || w.ctx.Err() != nil {
		w.truncated = true
		return
	}
	var values uintptr
	if axCopyMultipleAttributes(el, axAttributes, 0, &values) != 0 || values == 0 {
		return
	}
	defer cfRelease(values)
	if cfArrayGetCount(values) < len(axAttributeNames) {
		return
	}
	v := func(i int) uintptr {
		ref := cfArrayGetValueAtIndex(values, i)
		// a missing attribute comes back as an AXValue holding the error
		if ref != 0 && cfGetTypeID(ref) == axValueID && axValueGetType(ref) != axValueCGPointType && axValueGetType(ref) != axValueCGSizeType {
			return 0
		}
		return ref
	}
	role := goString(v(attrRole))
	n := a11yNode{Depth: depth, Role: goString(v(attrRoleDescription))}
	if n.Role == "" {
		n.Role = role
	}
	for _, i := range []int{attrTitle, attrDescription} {
		if n.Name = goString(v(i)); n.Name != "" {
			break
		}
	}
	if pos, size := v(attrPosition), v(attrSize); pos != 0 && size != 0 {
		var p, s struct{ A, B float64 }
		if axValueGetValue(pos, axValueCGPointType, unsafe.Pointer(&p)) && axValueGetValue(size, axValueCGSizeType, unsafe.Pointer(&s)) && s.A > 0 && s.B > 0 {
			n.Box = []int32{int32(p.A), int32(p.B), int32(s.A), int32(s.B)}
		}
	}
	if boolean(v(attrFocused)) {
		n.States = append(n.States, "focused")
	}
	if boolean(v(attrSelected)) {
		n.States = append(n.States, "selected")
	}
	if enabled := v(attrEnabled); enabled != 0 && !boolean(enabled) {
		n.States = append(n.States, "disabled")
	}
	if expanded := v(attrExpanded); expanded != 0 {
		if boolean(expanded) {
			n.States = append(n.States, "expanded")
		} else {
			n.States = append(n.States, "collapsed")
		}
	}
	value := v(attrValue)
	switch {
	case role == "AXCheckBox" || role == "AXRadioButton":
		if boolean(value) {
			n.States = append(n.States, "checked")
		}
	case goString(v(attrSubrole)) == "AXSecureTextField":
	case value != 0 && cfGetTypeID(value) == stringID:
		n.Value = clip(goString(value))
	default:
		if f, ok := number(value); ok {
			n.Value = strconv.FormatFloat(f, 'f', -1, 64)
		}
	}
	w.nodes = append(w.nodes, n)
	children := v(attrChildren)
	if depth >= a11yMaxDepth || children == 0 || cfGetTypeID(children) != arrayID {
		return
	}
	for i := 0; i < cfArrayGetCount(children); i++ {
		w.walk(cfArrayGetValueAtIndex(children, i), depth+1)
	}
}
