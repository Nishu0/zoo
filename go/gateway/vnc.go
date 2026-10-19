package main

import (
	"bytes"
	"context"
	"crypto/des"
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"io"
	"math/bits"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	vncTileSize                  = 32
	vncIdleTimeout               = 60 * time.Second
	vncIOTimeout                 = 10 * time.Second
	vncMaxPixels                 = 16 * 1024 * 1024
	vncMaxPayload                = 64 * 1024 * 1024
	vncMaxText                   = 1024 * 1024
	vncRaw                 int32 = 0
	vncCopyRect            int32 = 1
	vncZlib                int32 = 6
	vncTight               int32 = 7
	vncDesktopSize         int32 = -223
	vncLastRect            int32 = -224
	vncExtendedDesktopSize int32 = -308
)

var (
	errVNCClosed = errors.New("VNC session is closed")
	errVNCIdle   = errors.New("VNC session expired after 60 seconds idle")
)

type VNCDiff struct {
	Version      uint64
	ChangedTiles []uint32
	Boxes        []image.Rectangle
	Frame        *image.RGBA
}

type VNCSession struct {
	transport    io.ReadWriteCloser
	ctx          context.Context
	cancel       context.CancelFunc
	closed       sync.Once
	done         chan struct{}
	readerDone   chan struct{}
	writes       chan struct{}
	pngMu        sync.Mutex
	mu           sync.Mutex
	frame        *image.RGBA
	version      uint64
	ready        bool
	tileHashes   []uint64
	tileVersions []uint64
	changed      chan struct{}
	lastChange   time.Time
	lastUse      time.Time
	idle         *time.Timer
	failure      error
	metricClose  func()
	pngVersion   uint64
	png          []byte
	zlib         vncInflater
	tight        [4]vncInflater
}

func NewVNCSession(ctx context.Context, transport io.ReadWriteCloser, password string) (*VNCSession, error) {
	if transport == nil {
		return nil, errors.New("VNC transport is required")
	}
	lifecycle, cancel := context.WithCancel(ctx)
	session := &VNCSession{transport: transport, ctx: lifecycle, cancel: cancel, done: make(chan struct{}), readerDone: make(chan struct{}), writes: make(chan struct{}, 1), changed: make(chan struct{})}
	context.AfterFunc(lifecycle, func() { session.terminate(lifecycle.Err()) })
	deadline := time.AfterFunc(vncIOTimeout, func() { session.terminate(fmt.Errorf("VNC handshake: %w", context.DeadlineExceeded)) })
	err := session.handshake(password)
	deadline.Stop()
	if err != nil {
		session.terminate(err)
		close(session.readerDone)
		return nil, session.Err()
	}
	session.mu.Lock()
	if session.failure != nil {
		err = session.failure
		session.mu.Unlock()
		close(session.readerDone)
		return nil, err
	}
	session.lastUse, session.lastChange = time.Now(), time.Now()
	session.metricClose = gatewaySessionOpened("vnc")
	session.idle = time.AfterFunc(vncIdleTimeout, session.expireIdle)
	session.mu.Unlock()
	go session.readLoop()
	return session, nil
}

func (session *VNCSession) handshake(password string) error {
	version, err := session.readBytes(12)
	if err != nil {
		return err
	}
	if string(version) != "RFB 003.008\n" {
		return fmt.Errorf("unsupported RFB version %q; RFB 3.8 is required", version)
	}
	if err := session.writeMessage(session.ctx, []byte("RFB 003.008\n")); err != nil {
		return err
	}
	count, err := session.readBytes(1)
	if err != nil {
		return err
	}
	if count[0] == 0 {
		reason, err := session.readString(64 * 1024)
		if err != nil {
			return err
		}
		return fmt.Errorf("VNC security negotiation rejected: %s", reason)
	}
	security, err := session.readBytes(int(count[0]))
	if err != nil {
		return err
	}
	selected := byte(2)
	if !bytes.Contains(security, []byte{2}) {
		if password != "" || !bytes.Contains(security, []byte{1}) {
			return fmt.Errorf("VNC authentication is unavailable: security types %v", security)
		}
		selected = 1
	}
	if err := session.writeMessage(session.ctx, []byte{selected}); err != nil {
		return err
	}
	if selected == 2 {
		challenge, err := session.readBytes(16)
		if err != nil {
			return err
		}
		response, err := vncAuthResponse(password, challenge)
		if err != nil {
			return err
		}
		if err := session.writeMessage(session.ctx, response); err != nil {
			return err
		}
	}
	result, err := session.readBytes(4)
	if err != nil {
		return err
	}
	if binary.BigEndian.Uint32(result) != 0 {
		reason, err := session.readString(64 * 1024)
		if err != nil {
			return fmt.Errorf("VNC authentication failed: %w", err)
		}
		return fmt.Errorf("VNC authentication failed: %s", reason)
	}
	if err := session.writeMessage(session.ctx, []byte{1}); err != nil {
		return err
	}
	initial, err := session.readBytes(20)
	if err != nil {
		return err
	}
	width, height := int(binary.BigEndian.Uint16(initial)), int(binary.BigEndian.Uint16(initial[2:]))
	if _, err := session.readString(64 * 1024); err != nil {
		return err
	}
	if err := session.resize(width, height, false); err != nil {
		return err
	}
	pixelFormat := []byte{0, 0, 0, 0, 32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0}
	if err := session.writeMessage(session.ctx, pixelFormat); err != nil {
		return err
	}
	encodings := []int32{vncTight, vncCopyRect, vncZlib, vncRaw, vncDesktopSize, vncExtendedDesktopSize, vncLastRect}
	message := make([]byte, 4+len(encodings)*4)
	message[0] = 2
	binary.BigEndian.PutUint16(message[2:], uint16(len(encodings)))
	for index, encoding := range encodings {
		binary.BigEndian.PutUint32(message[4+index*4:], uint32(encoding))
	}
	if err := session.writeMessage(session.ctx, message); err != nil {
		return err
	}
	return session.requestUpdate(false)
}

func vncAuthResponse(password string, challenge []byte) ([]byte, error) {
	if len(challenge) != 16 {
		return nil, errors.New("VNC challenge must contain 16 bytes")
	}
	var key [8]byte
	copy(key[:], []byte(password))
	for index := range key {
		key[index] = bits.Reverse8(key[index])
	}
	cipher, err := des.NewCipher(key[:])
	if err != nil {
		return nil, err
	}
	response := make([]byte, 16)
	cipher.Encrypt(response[:8], challenge[:8])
	cipher.Encrypt(response[8:], challenge[8:])
	return response, nil
}

func (session *VNCSession) readBytes(length int) ([]byte, error) {
	if length < 0 || length > vncMaxPayload {
		return nil, errors.New("VNC payload exceeds limit")
	}
	data := make([]byte, length)
	_, err := io.ReadFull(session.transport, data)
	return data, err
}

func (session *VNCSession) readString(limit int) (string, error) {
	header, err := session.readBytes(4)
	if err != nil {
		return "", err
	}
	length := binary.BigEndian.Uint32(header)
	if length > uint32(limit) {
		return "", errors.New("VNC text exceeds limit")
	}
	data, err := session.readBytes(int(length))
	return string(data), err
}

func (session *VNCSession) writeMessage(ctx context.Context, message []byte) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-session.done:
		return session.Err()
	case session.writes <- struct{}{}:
	}
	defer func() { <-session.writes }()
	if err := session.Err(); err != nil {
		return err
	}
	deadline := time.AfterFunc(vncIOTimeout, func() { session.terminate(fmt.Errorf("VNC write: %w", context.DeadlineExceeded)) })
	stop := context.AfterFunc(ctx, func() { session.terminate(ctx.Err()) })
	defer deadline.Stop()
	defer stop()
	for len(message) != 0 {
		written, err := session.transport.Write(message)
		if written < 0 || written > len(message) {
			err = errors.New("VNC transport returned invalid write count")
		}
		if err != nil {
			session.terminate(err)
			return session.Err()
		}
		if written == 0 {
			session.terminate(io.ErrShortWrite)
			return session.Err()
		}
		message = message[written:]
	}
	return session.Err()
}

func (session *VNCSession) requestUpdate(incremental bool) error {
	session.mu.Lock()
	width, height := session.frame.Rect.Dx(), session.frame.Rect.Dy()
	session.mu.Unlock()
	message := make([]byte, 10)
	message[0] = 3
	if incremental {
		message[1] = 1
	}
	binary.BigEndian.PutUint16(message[6:], uint16(width))
	binary.BigEndian.PutUint16(message[8:], uint16(height))
	return session.writeMessage(session.ctx, message)
}

func (session *VNCSession) readLoop() {
	defer close(session.readerDone)
	defer session.zlib.Close()
	defer func() {
		for index := range session.tight {
			session.tight[index].Close()
		}
	}()
	for {
		kind, err := session.readBytes(1)
		if err != nil {
			session.terminate(err)
			return
		}
		deadline := time.AfterFunc(vncIOTimeout, func() { session.terminate(fmt.Errorf("VNC message: %w", context.DeadlineExceeded)) })
		resized := false
		switch kind[0] {
		case 0:
			resized, err = session.readFramebufferUpdate()
		case 1:
			var header []byte
			header, err = session.readBytes(5)
			if err == nil {
				_, err = session.readBytes(int(binary.BigEndian.Uint16(header[3:])) * 6)
			}
		case 2:
		case 3:
			_, err = session.readBytes(3)
			if err == nil {
				_, err = session.readString(vncMaxText)
			}
		default:
			err = fmt.Errorf("unsupported VNC server message %d", kind[0])
		}
		deadline.Stop()
		if err != nil {
			session.terminate(err)
			return
		}
		if kind[0] == 0 {
			if err := session.requestUpdate(!resized); err != nil {
				session.terminate(err)
				return
			}
		}
	}
}

func (session *VNCSession) readFramebufferUpdate() (bool, error) {
	// A framebuffer update may clear and repaint the same area across several
	// rectangles. Readers must only capture the completed update.
	session.mu.Lock()
	wasReady := session.ready
	session.ready = false
	session.mu.Unlock()
	header, err := session.readBytes(3)
	if err != nil {
		return false, err
	}
	count := int(binary.BigEndian.Uint16(header[1:]))
	resized := false
	applied := false
	for index := range count {
		if index >= 4096 {
			return false, errors.New("VNC update exceeds rectangle limit")
		}
		header, err := session.readBytes(12)
		if err != nil {
			return false, err
		}
		horizontal, vertical := int(binary.BigEndian.Uint16(header)), int(binary.BigEndian.Uint16(header[2:]))
		width, height := int(binary.BigEndian.Uint16(header[4:])), int(binary.BigEndian.Uint16(header[6:]))
		encoding := int32(binary.BigEndian.Uint32(header[8:]))
		if encoding == vncLastRect {
			break
		}
		if encoding == vncDesktopSize || encoding == vncExtendedDesktopSize {
			if encoding == vncExtendedDesktopSize {
				screens, err := session.readBytes(4)
				if err != nil {
					return false, err
				}
				if _, err := session.readBytes(int(screens[0]) * 16); err != nil {
					return false, err
				}
				if vertical != 0 {
					return false, fmt.Errorf("VNC desktop resize rejected with result %d", vertical)
				}
			}
			// x11vnc can report its existing layout with every full refresh.
			// Only a geometry change needs a new full refresh; otherwise these
			// notifications loop forever and prevent the first frame becoming ready.
			session.mu.Lock()
			sameSize := session.frame.Rect.Dx() == width && session.frame.Rect.Dy() == height
			session.mu.Unlock()
			if sameSize {
				continue
			}
			if err := session.resize(width, height, true); err != nil {
				return false, err
			}
			resized = true
			continue
		}
		rectangle := image.Rect(horizontal, vertical, horizontal+width, vertical+height)
		session.mu.Lock()
		valid := width > 0 && height > 0 && rectangle.In(session.frame.Rect)
		session.mu.Unlock()
		if !valid {
			return false, fmt.Errorf("VNC rectangle outside framebuffer: %v", rectangle)
		}
		if err := session.decodeRectangle(rectangle, encoding); err != nil {
			return false, err
		}
		applied = true
	}
	session.mu.Lock()
	if !resized {
		session.ready = wasReady || applied
	}
	if session.ready {
		session.notifyLocked()
	}
	session.mu.Unlock()
	return resized, nil
}

func (session *VNCSession) resize(width, height int, changed bool) error {
	if width <= 0 || height <= 0 || width > 16384 || height > 16384 || width*height > vncMaxPixels {
		return errors.New("VNC framebuffer dimensions exceed limit")
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if session.frame != nil && session.frame.Rect.Dx() == width && session.frame.Rect.Dy() == height {
		return nil
	}
	session.frame = image.NewRGBA(image.Rect(0, 0, width, height))
	for index := 3; index < len(session.frame.Pix); index += 4 {
		session.frame.Pix[index] = 255
	}
	count := ((width + vncTileSize - 1) / vncTileSize) * ((height + vncTileSize - 1) / vncTileSize)
	session.tileHashes, session.tileVersions = make([]uint64, count), make([]uint64, count)
	session.png = nil
	session.ready = false
	if changed {
		session.version++
		session.lastChange = time.Now()
	}
	for index := range session.tileHashes {
		session.tileHashes[index] = session.hashTileLocked(index)
		session.tileVersions[index] = session.version
	}
	if changed {
		session.notifyLocked()
	}
	return nil
}

func (session *VNCSession) applyPixels(rectangle image.Rectangle, pixels []byte) {
	session.mu.Lock()
	defer session.mu.Unlock()
	for vertical := range rectangle.Dy() {
		destination := session.frame.PixOffset(rectangle.Min.X, rectangle.Min.Y+vertical)
		copy(session.frame.Pix[destination:destination+rectangle.Dx()*4], pixels[vertical*rectangle.Dx()*4:(vertical+1)*rectangle.Dx()*4])
	}
	session.updateTilesLocked(rectangle)
}

func (session *VNCSession) updateTilesLocked(rectangle image.Rectangle) {
	session.version++
	session.png = nil
	columns := (session.frame.Rect.Dx() + vncTileSize - 1) / vncTileSize
	changed := false
	for vertical := rectangle.Min.Y / vncTileSize; vertical <= (rectangle.Max.Y-1)/vncTileSize; vertical++ {
		for horizontal := rectangle.Min.X / vncTileSize; horizontal <= (rectangle.Max.X-1)/vncTileSize; horizontal++ {
			index := vertical*columns + horizontal
			hash := session.hashTileLocked(index)
			if hash != session.tileHashes[index] {
				session.tileHashes[index], session.tileVersions[index] = hash, session.version
				changed = true
			}
		}
	}
	if changed {
		session.lastChange = time.Now()
	}
}

func (session *VNCSession) hashTileLocked(index int) uint64 {
	columns := (session.frame.Rect.Dx() + vncTileSize - 1) / vncTileSize
	rectangle := image.Rect((index%columns)*vncTileSize, (index/columns)*vncTileSize, (index%columns+1)*vncTileSize, (index/columns+1)*vncTileSize).Intersect(session.frame.Rect)
	hash := uint64(14695981039346656037)
	for vertical := rectangle.Min.Y; vertical < rectangle.Max.Y; vertical++ {
		start := session.frame.PixOffset(rectangle.Min.X, vertical)
		for _, value := range session.frame.Pix[start : start+rectangle.Dx()*4] {
			hash = (hash ^ uint64(value)) * 1099511628211
		}
	}
	return hash
}

func (session *VNCSession) notifyLocked() {
	close(session.changed)
	session.changed = make(chan struct{})
}

func (session *VNCSession) Touch() error {
	session.mu.Lock()
	defer session.mu.Unlock()
	if session.failure != nil {
		return session.failure
	}
	session.lastUse = time.Now()
	if session.idle != nil {
		session.idle.Reset(vncIdleTimeout)
	}
	return nil
}

func (session *VNCSession) expireIdle() {
	session.mu.Lock()
	remaining := vncIdleTimeout - time.Since(session.lastUse)
	if remaining > 0 && session.failure == nil {
		session.idle.Reset(remaining)
		session.mu.Unlock()
		return
	}
	session.mu.Unlock()
	session.terminate(errVNCIdle)
}

func (session *VNCSession) terminate(err error) {
	if err == nil {
		err = errVNCClosed
	}
	session.mu.Lock()
	if session.failure != nil || (errors.Is(err, errVNCIdle) && time.Since(session.lastUse) < vncIdleTimeout) {
		session.mu.Unlock()
		return
	}
	session.failure = err
	if session.idle != nil {
		session.idle.Stop()
	}
	session.notifyLocked()
	metricClose := session.metricClose
	session.mu.Unlock()
	session.closed.Do(func() {
		if metricClose != nil {
			metricClose()
		}
		close(session.done)
		session.cancel()
		session.transport.Close()
	})
}

func (session *VNCSession) Done() <-chan struct{} { return session.done }

func (session *VNCSession) Err() error {
	session.mu.Lock()
	defer session.mu.Unlock()
	return session.failure
}

func (session *VNCSession) Changed() <-chan struct{} {
	session.mu.Lock()
	defer session.mu.Unlock()
	return session.changed
}

func (session *VNCSession) Close() error {
	session.terminate(errVNCClosed)
	<-session.readerDone
	return nil
}

func (session *VNCSession) lockFrame(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := session.Touch(); err != nil {
		return err
	}
	for {
		session.mu.Lock()
		ready, failure, changed := session.ready, session.failure, session.changed
		if failure != nil {
			session.mu.Unlock()
			return failure
		}
		if ready {
			return nil
		}
		session.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-session.done:
			return session.Err()
		case <-changed:
		}
	}
}

func (session *VNCSession) Frame(ctx context.Context) (*image.RGBA, uint64, error) {
	if err := session.lockFrame(ctx); err != nil {
		return nil, 0, err
	}
	defer session.mu.Unlock()
	if session.failure != nil {
		return nil, 0, session.failure
	}
	frame := image.NewRGBA(session.frame.Rect)
	copy(frame.Pix, session.frame.Pix)
	return frame, session.version, nil
}

func (session *VNCSession) Screenshot(ctx context.Context) ([]byte, uint64, error) {
	session.pngMu.Lock()
	defer session.pngMu.Unlock()
	if err := session.lockFrame(ctx); err != nil {
		return nil, 0, err
	}
	if session.failure != nil {
		err := session.failure
		session.mu.Unlock()
		return nil, 0, err
	}
	if session.png != nil && session.pngVersion == session.version {
		data, version := bytes.Clone(session.png), session.version
		session.mu.Unlock()
		return data, version, nil
	}
	frame := image.NewRGBA(session.frame.Rect)
	copy(frame.Pix, session.frame.Pix)
	version := session.version
	session.mu.Unlock()
	encoded, err := fastPNG(frame)
	if err != nil {
		return nil, 0, err
	}
	if err := ctx.Err(); err != nil {
		return nil, 0, err
	}
	session.mu.Lock()
	if session.failure != nil {
		err := session.failure
		session.mu.Unlock()
		return nil, 0, err
	}
	if session.version == version && session.failure == nil {
		session.png, session.pngVersion = bytes.Clone(encoded), version
	}
	session.mu.Unlock()
	return encoded, version, nil
}

func (session *VNCSession) Diff(ctx context.Context, since uint64) (VNCDiff, error) {
	if err := session.lockFrame(ctx); err != nil {
		return VNCDiff{}, err
	}
	defer session.mu.Unlock()
	if session.failure != nil {
		return VNCDiff{}, session.failure
	}
	if since > session.version {
		return VNCDiff{}, errors.New("screen diff version is ahead of framebuffer")
	}
	result := VNCDiff{Version: session.version, ChangedTiles: make([]uint32, 0), Boxes: make([]image.Rectangle, 0)}
	result.Frame = image.NewRGBA(session.frame.Rect)
	copy(result.Frame.Pix, session.frame.Pix)
	columns := (session.frame.Rect.Dx() + vncTileSize - 1) / vncTileSize
	for index, version := range session.tileVersions {
		if since != 0 && version <= since {
			continue
		}
		result.ChangedTiles = append(result.ChangedTiles, uint32(index))
		result.Boxes = append(result.Boxes, image.Rect((index%columns)*vncTileSize, (index/columns)*vncTileSize, (index%columns+1)*vncTileSize, (index/columns+1)*vncTileSize).Intersect(session.frame.Rect))
	}
	return result, nil
}

func (session *VNCSession) WaitStable(ctx context.Context, quiet, timeout time.Duration) (uint64, error) {
	if quiet < 0 || timeout <= 0 {
		return 0, errors.New("quiet duration must be nonnegative and timeout positive")
	}
	bounded, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	for {
		if err := session.lockFrame(bounded); err != nil {
			return 0, err
		}
		remaining := quiet - time.Since(session.lastChange)
		version, failure, changed := session.version, session.failure, session.changed
		session.mu.Unlock()
		if failure != nil {
			return 0, failure
		}
		if remaining <= 0 {
			return version, nil
		}
		timer := time.NewTimer(remaining)
		select {
		case <-bounded.Done():
			timer.Stop()
			return 0, bounded.Err()
		case <-session.done:
			timer.Stop()
			return 0, session.Err()
		case <-changed:
			timer.Stop()
		case <-timer.C:
		}
	}
}

func (session *VNCSession) Pointer(ctx context.Context, horizontal, vertical int, mask byte) error {
	session.mu.Lock()
	valid := horizontal >= 0 && vertical >= 0 && horizontal < session.frame.Rect.Dx() && vertical < session.frame.Rect.Dy()
	session.mu.Unlock()
	if !valid {
		return errors.New("pointer coordinates outside framebuffer")
	}
	if err := session.Touch(); err != nil {
		return err
	}
	message := []byte{5, mask, 0, 0, 0, 0}
	binary.BigEndian.PutUint16(message[2:], uint16(horizontal))
	binary.BigEndian.PutUint16(message[4:], uint16(vertical))
	return session.writeMessage(ctx, message)
}

func (session *VNCSession) Key(ctx context.Context, keysym uint32, down bool) error {
	if err := session.Touch(); err != nil {
		return err
	}
	return session.writeMessage(ctx, vncKeyMessage(keysym, down))
}

func vncKeyMessage(keysym uint32, down bool) []byte {
	message := make([]byte, 8)
	message[0] = 4
	if down {
		message[1] = 1
	}
	binary.BigEndian.PutUint32(message[4:], keysym)
	return message
}

func vncRuneKeysym(character rune) uint32 {
	switch character {
	case '\n', '\r':
		return 0xff0d
	case '\t':
		return 0xff09
	case '\b':
		return 0xff08
	case 0x1b:
		return 0xff1b
	}
	if character < 0x100 {
		return uint32(character)
	}
	return 0x01000000 + uint32(character)
}

func (session *VNCSession) Type(ctx context.Context, text string) error {
	if len(text) > vncMaxText || !utf8.ValidString(text) {
		return errors.New("typed text exceeds limit or is invalid UTF-8")
	}
	if err := session.Touch(); err != nil {
		return err
	}
	message := make([]byte, 0, utf8.RuneCountInString(text)*16)
	for _, character := range text {
		keysym := vncRuneKeysym(character)
		message = append(message, vncKeyMessage(keysym, true)...)
		message = append(message, vncKeyMessage(keysym, false)...)
	}
	return session.writeMessage(ctx, message)
}

func (session *VNCSession) InputMessage(ctx context.Context, message []byte) error {
	if len(message) < 8 {
		return errors.New("VNC viewer control message is incomplete or unsupported")
	}
	switch message[0] {
	case 6:
		length := int32(binary.BigEndian.Uint32(message[4:]))
		if length < 0 {
			return errors.New("VNC extended clipboard was not negotiated")
		}
		if length > vncMaxText || len(message) != 8+int(length) {
			return errors.New("VNC clipboard exceeds limit or has invalid framing")
		}
	case 251:
		width, height := int(binary.BigEndian.Uint16(message[2:])), int(binary.BigEndian.Uint16(message[4:]))
		if width <= 0 || height <= 0 || width > 16384 || height > 16384 || width*height > vncMaxPixels || message[6] == 0 || len(message) != 8+int(message[6])*16 {
			return errors.New("VNC desktop size exceeds limit or has invalid framing")
		}
		identifiers := make(map[uint32]bool, int(message[6]))
		for offset := 8; offset < len(message); offset += 16 {
			identifier := binary.BigEndian.Uint32(message[offset:])
			horizontal, vertical := int(binary.BigEndian.Uint16(message[offset+4:])), int(binary.BigEndian.Uint16(message[offset+6:]))
			screenWidth, screenHeight := int(binary.BigEndian.Uint16(message[offset+8:])), int(binary.BigEndian.Uint16(message[offset+10:]))
			if identifiers[identifier] || screenWidth == 0 || screenHeight == 0 || horizontal+screenWidth > width || vertical+screenHeight > height {
				return errors.New("VNC desktop screen layout is invalid")
			}
			identifiers[identifier] = true
		}
	default:
		return fmt.Errorf("unsupported VNC viewer control message %d", message[0])
	}
	if err := session.Touch(); err != nil {
		return err
	}
	return session.writeMessage(ctx, message)
}
