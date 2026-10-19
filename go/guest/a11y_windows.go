package main

import (
	"context"
	"errors"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

// UI Automation through its COM interfaces, with no go-ole. One cache request fetches the whole window's control
// view with every property below in a single call into the app; the walk then reads only the local cache.

var (
	ole32                        = windows.NewLazySystemDLL("ole32.dll")
	procCoCreateInstance         = ole32.NewProc("CoCreateInstance")
	oleaut32                     = windows.NewLazySystemDLL("oleaut32.dll")
	procVariantClear             = oleaut32.NewProc("VariantClear")
	procSafeArrayAccessData      = oleaut32.NewProc("SafeArrayAccessData")
	procSafeArrayUnaccess        = oleaut32.NewProc("SafeArrayUnaccessData")
	procSafeArrayGetUBound       = oleaut32.NewProc("SafeArrayGetUBound")
	procGetForegroundWindow      = user32.NewProc("GetForegroundWindow")
	clsidCUIAutomation           = windows.GUID{Data1: 0xff48dba4, Data2: 0x60ef, Data3: 0x4201, Data4: [8]byte{0xaa, 0x87, 0x54, 0x10, 0x3e, 0xef, 0x59, 0x4e}}
	iidIUIAutomation             = windows.GUID{Data1: 0x30cbe57d, Data2: 0xd9d0, Data3: 0x452a, Data4: [8]byte{0xab, 0x13, 0x7a, 0xc5, 0xac, 0x48, 0x25, 0xee}}
	errNoAutomation              = errors.New("UI Automation is unavailable")
	uiaTreeScopeSubtree     uint = 7
)

// vtable slots, from UIAutomationClient.h
const (
	uiaRelease                     = 2
	uiaElementFromHandleBuildCache = 10 // IUIAutomation
	uiaCreateCacheRequest          = 20 // IUIAutomation
	uiaCacheAddProperty            = 3  // IUIAutomationCacheRequest
	uiaCachePutTreeScope           = 7  // IUIAutomationCacheRequest
	uiaGetCachedPropertyValue      = 12 // IUIAutomationElement
	uiaGetCachedChildren           = 19 // IUIAutomationElement
	uiaArrayLength                 = 3  // IUIAutomationElementArray
	uiaArrayElement                = 4  // IUIAutomationElementArray
)

// property ids
const (
	propBoundingRectangle = 30001
	propControlType       = 30003
	propName              = 30005
	propHasKeyboardFocus  = 30008
	propIsEnabled         = 30010
	propIsPassword        = 30019
	propIsOffscreen       = 30022
	propHasExpandCollapse = 30028
	propHasSelectionItem  = 30036
	propHasToggle         = 30041
	propHasValue          = 30043
	propValue             = 30045
	propValueReadOnly     = 30046
	propExpandState       = 30070
	propIsSelected        = 30079
	propToggleState       = 30086
)

var cachedProperties = []uintptr{
	propBoundingRectangle, propControlType, propName, propHasKeyboardFocus, propIsEnabled, propIsPassword,
	propIsOffscreen, propHasExpandCollapse, propHasSelectionItem, propHasToggle, propHasValue, propValue,
	propValueReadOnly, propExpandState, propIsSelected, propToggleState,
}

// control type ids from 50000 on
var controlTypes = []string{
	"button", "calendar", "check box", "combo box", "edit", "hyperlink", "image", "list item", "list", "menu",
	"menu bar", "menu item", "progress bar", "radio button", "scroll bar", "slider", "spinner", "status bar", "tab",
	"tab item", "text", "tool bar", "tool tip", "tree", "tree item", "custom", "group", "thumb", "data grid",
	"data item", "document", "split button", "window", "pane", "header", "header item", "table", "title bar",
	"separator", "semantic zoom", "app bar",
}

const (
	vtI4    = 3
	vtBSTR  = 8
	vtBool  = 11
	vtArray = 0x2000
	vtR8    = 5
)

type comObject struct{ p unsafe.Pointer }

func (o comObject) call(slot int, args ...uintptr) uintptr {
	vtable := *(*unsafe.Pointer)(o.p)
	fn := *(*uintptr)(unsafe.Add(vtable, slot*int(unsafe.Sizeof(uintptr(0)))))
	r, _, _ := syscall.SyscallN(fn, append([]uintptr{uintptr(o.p)}, args...)...)
	return r
}

func (o comObject) release() {
	if o.p != nil {
		o.call(uiaRelease)
	}
}

// variant is a VARIANT: a type tag, then the value in the first word of its union.
type variant struct {
	vt  uint16
	_   [3]uint16
	val unsafe.Pointer
	_   uintptr
}

func (v *variant) clear() { procVariantClear.Call(uintptr(unsafe.Pointer(v))) }

func (v *variant) str() string {
	if v.vt != vtBSTR || v.val == nil {
		return ""
	}
	return windows.UTF16PtrToString((*uint16)(v.val))
}

func (v *variant) i4() (int32, bool) {
	if v.vt != vtI4 {
		return 0, false
	}
	return *(*int32)(unsafe.Pointer(&v.val)), true
}

func (v *variant) boolean() bool {
	return v.vt == vtBool && *(*int16)(unsafe.Pointer(&v.val)) != 0
}

func (v *variant) rect() []int32 {
	if v.vt != vtArray|vtR8 || v.val == nil {
		return nil
	}
	var upper int32
	if r, _, _ := procSafeArrayGetUBound.Call(uintptr(v.val), 1, uintptr(unsafe.Pointer(&upper))); r != 0 || upper < 3 {
		return nil
	}
	var data unsafe.Pointer
	if r, _, _ := procSafeArrayAccessData.Call(uintptr(v.val), uintptr(unsafe.Pointer(&data))); r != 0 {
		return nil
	}
	defer procSafeArrayUnaccess.Call(uintptr(v.val))
	d := (*[4]float64)(data)
	if d[2] <= 0 || d[3] <= 0 {
		return nil
	}
	return []int32{int32(d[0]), int32(d[1]), int32(d[2]), int32(d[3])}
}

type uiaElement struct{ comObject }

func (e uiaElement) prop(id uintptr) variant {
	var v variant
	e.call(uiaGetCachedPropertyValue, id, uintptr(unsafe.Pointer(&v)))
	return v
}

func (e uiaElement) str(id uintptr) string {
	v := e.prop(id)
	defer v.clear()
	return v.str()
}

func (e uiaElement) flag(id uintptr) bool {
	v := e.prop(id)
	defer v.clear()
	return v.boolean()
}

func (e uiaElement) number(id uintptr) (int32, bool) {
	v := e.prop(id)
	defer v.clear()
	return v.i4()
}

func readTree(ctx context.Context, a a11yArgs) (a11yResult, error) {
	hwnd, app, title, err := pickWindow(a.App, a.Title)
	if err != nil {
		return a11yResult{}, err
	}
	// COM objects belong to the thread that made them
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	// S_FALSE (1) means this thread already had COM; it still needs the matching uninitialize
	if err := windows.CoInitializeEx(0, windows.COINIT_MULTITHREADED); err != nil && err != syscall.Errno(1) {
		return a11yResult{}, err
	}
	defer windows.CoUninitialize()
	var automation comObject
	if r, _, _ := procCoCreateInstance.Call(
		uintptr(unsafe.Pointer(&clsidCUIAutomation)), 0, windows.CLSCTX_INPROC_SERVER,
		uintptr(unsafe.Pointer(&iidIUIAutomation)), uintptr(unsafe.Pointer(&automation.p)),
	); r != 0 || automation.p == nil {
		return a11yResult{}, errNoAutomation
	}
	defer automation.release()
	var request comObject
	if automation.call(uiaCreateCacheRequest, uintptr(unsafe.Pointer(&request.p))) != 0 || request.p == nil {
		return a11yResult{}, errNoAutomation
	}
	defer request.release()
	for _, id := range cachedProperties {
		request.call(uiaCacheAddProperty, id)
	}
	request.call(uiaCachePutTreeScope, uintptr(uiaTreeScopeSubtree))
	var root uiaElement
	if r := automation.call(uiaElementFromHandleBuildCache, hwnd, uintptr(request.p), uintptr(unsafe.Pointer(&root.p))); r != 0 || root.p == nil {
		return a11yResult{}, errors.New("the window is gone or doesn't answer UI Automation (HRESULT 0x" + strconv.FormatUint(uint64(uint32(r)), 16) + ")")
	}
	defer root.release()
	w := uiaWalker{ctx: ctx, max: a.MaxNodes}
	w.walk(root, 0)
	return a11yResult{App: app, Window: title, Nodes: w.nodes, Truncated: w.truncated}, nil
}

// pickWindow is the foreground window, or the first top-level window whose app and title contain the filters.
func pickWindow(app, title string) (uintptr, string, string, error) {
	if app == "" && title == "" {
		hwnd, _, _ := procGetForegroundWindow.Call()
		if hwnd == 0 {
			return 0, "", "", errors.New("no window has the focus; pass app or title")
		}
		var pid uint32
		windows.GetWindowThreadProcessId(windows.HWND(hwnd), &pid)
		return hwnd, processName(pid), windowTitle(hwnd), nil
	}
	var seen []string
	for _, w := range listWindows() {
		seen = append(seen, w.App+": "+w.Title)
		if matches(w.App, app) && matches(w.Title, title) {
			id, _ := strconv.ParseInt(w.ID, 10, 64)
			return uintptr(id), w.App, w.Title, nil
		}
	}
	return 0, "", "", errors.New("no matching window; pass app or title, one of: " + strings.Join(seen, "; "))
}

func windowTitle(hwnd uintptr) string {
	n, _, _ := procGetWindowTextLenW.Call(hwnd)
	buf := make([]uint16, n+1)
	procGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), n+1)
	return windows.UTF16ToString(buf)
}

type uiaWalker struct {
	ctx       context.Context
	max       int
	nodes     []a11yNode
	truncated bool
}

func (w *uiaWalker) walk(e uiaElement, depth int) {
	if len(w.nodes) >= w.max || w.ctx.Err() != nil {
		w.truncated = true
		return
	}
	if depth > 0 && e.flag(propIsOffscreen) {
		return
	}
	n := a11yNode{Depth: depth, Name: e.str(propName)}
	if t, ok := e.number(propControlType); ok && t >= 50000 && int(t-50000) < len(controlTypes) {
		n.Role = controlTypes[t-50000]
	}
	box := e.prop(propBoundingRectangle)
	n.Box = box.rect()
	box.clear()
	if e.flag(propHasKeyboardFocus) {
		n.States = append(n.States, "focused")
	}
	if !e.flag(propIsEnabled) {
		n.States = append(n.States, "disabled")
	}
	if e.flag(propHasValue) {
		if !e.flag(propValueReadOnly) {
			n.States = append(n.States, "editable")
		}
		if !e.flag(propIsPassword) {
			n.Value = clip(e.str(propValue))
		}
	}
	if e.flag(propHasToggle) {
		if s, _ := e.number(propToggleState); s == 1 {
			n.States = append(n.States, "checked")
		}
	}
	if e.flag(propHasSelectionItem) && e.flag(propIsSelected) {
		n.States = append(n.States, "selected")
	}
	if e.flag(propHasExpandCollapse) {
		switch s, _ := e.number(propExpandState); s {
		case 0:
			n.States = append(n.States, "collapsed")
		case 1:
			n.States = append(n.States, "expanded")
		}
	}
	w.nodes = append(w.nodes, n)
	if depth >= a11yMaxDepth {
		return
	}
	var children comObject
	if e.call(uiaGetCachedChildren, uintptr(unsafe.Pointer(&children.p))) != 0 || children.p == nil {
		return
	}
	defer children.release()
	var count int32
	children.call(uiaArrayLength, uintptr(unsafe.Pointer(&count)))
	for i := int32(0); i < count; i++ {
		var child uiaElement
		if children.call(uiaArrayElement, uintptr(i), uintptr(unsafe.Pointer(&child.p))) != 0 || child.p == nil {
			continue
		}
		w.walk(child, depth+1)
		child.release()
	}
}
