package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"image"
	"image/draw"
	"io"
	"math/bits"
	"sync"
	"time"

	"github.com/klauspost/compress/zlib"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

const (
	screenViewerMessageLimit  = 1 << 20
	screenViewerFrameInterval = time.Second / 30
)

type screenViewerInput struct {
	rpc       grpc.BidiStreamingServer[gatewaypb.ViewerFrame, gatewaypb.Bytes]
	sandboxID string
	data      []byte
}

func (input *screenViewerInput) Read(destination []byte) (int, error) {
	for empty := 0; len(input.data) == 0; empty++ {
		if empty >= 16 {
			return 0, status.Error(codes.InvalidArgument, "too many empty viewer frames")
		}
		frame, err := input.rpc.Recv()
		if err != nil {
			return 0, err
		}
		if frame.SandboxId != "" && frame.SandboxId != input.sandboxID || len(frame.Data) > screenViewerMessageLimit {
			return 0, status.Error(codes.InvalidArgument, "invalid viewer sandbox or payload length")
		}
		input.data = frame.Data
	}
	count := copy(destination, input.data)
	input.data = input.data[count:]
	return count, nil
}

type screenViewerFormat struct {
	bytes     int
	bigEndian bool
	maxima    [3]uint32
	shifts    [3]uint
}

func screenParseViewerFormat(data []byte) (screenViewerFormat, error) {
	format := screenViewerFormat{}
	if len(data) != 16 || data[0] != 8 && data[0] != 16 && data[0] != 32 || data[1] > data[0] || data[2] > 1 || data[3] != 1 {
		return format, status.Error(codes.InvalidArgument, "viewer requires an 8/16/32-bit true-color pixel format")
	}
	format.bytes, format.bigEndian = int(data[0])/8, data[2] != 0
	var used uint32
	for component := range 3 {
		maximum := binary.BigEndian.Uint16(data[4+component*2 : 6+component*2])
		shift := uint(data[10+component])
		if maximum == 0 || uint32(maximum)&(uint32(maximum)+1) != 0 || uint(bits.Len16(maximum))+shift > uint(data[0]) {
			return format, status.Error(codes.InvalidArgument, "invalid viewer color channel mask")
		}
		mask := uint32(maximum) << shift
		if mask&used != 0 {
			return format, status.Error(codes.InvalidArgument, "overlapping viewer color channel masks")
		}
		used |= mask
		format.maxima[component], format.shifts[component] = uint32(maximum), shift
	}
	return format, nil
}

type screenViewerRequest struct {
	region      image.Rectangle
	incremental bool
	continuous  bool
}

type screenViewer struct {
	ctx        context.Context
	rpc        grpc.BidiStreamingServer[gatewaypb.ViewerFrame, gatewaypb.Bytes]
	input      *screenViewerInput
	session    *VNCSession
	viewOnly   bool
	writeMu    sync.Mutex
	mu         sync.Mutex
	format     screenViewerFormat
	resize     int32
	requests   chan screenViewerRequest
	active     *screenViewerRequest
	previous   *image.RGBA
	width      int
	height     int
	encoding   int32
	compressed bytes.Buffer
	compressor *zlib.Writer
}

func (viewer *screenViewer) send(data []byte) error {
	for len(data) > 0 {
		count := min(len(data), 64<<10)
		if err := viewer.rpc.Send(&gatewaypb.Bytes{Data: data[:count]}); err != nil {
			return err
		}
		data = data[count:]
	}
	return nil
}

func (viewer *screenViewer) write(data []byte) error {
	viewer.writeMu.Lock()
	defer viewer.writeMu.Unlock()
	return viewer.send(data)
}

func (viewer *screenViewer) handshake() error {
	if err := viewer.write([]byte("RFB 003.008\n")); err != nil {
		return err
	}
	version := make([]byte, 12)
	if _, err := io.ReadFull(viewer.input, version); err != nil {
		return err
	}
	switch string(version) {
	case "RFB 003.003\n":
		if err := viewer.write([]byte{0, 0, 0, 1}); err != nil {
			return err
		}
	case "RFB 003.007\n", "RFB 003.008\n":
		if err := viewer.write([]byte{1, 1}); err != nil {
			return err
		}
		selection := make([]byte, 1)
		if _, err := io.ReadFull(viewer.input, selection); err != nil {
			return err
		}
		if selection[0] != 1 {
			return status.Error(codes.PermissionDenied, "unsupported viewer security type")
		}
		if string(version) == "RFB 003.008\n" {
			if err := viewer.write([]byte{0, 0, 0, 0}); err != nil {
				return err
			}
		}
	default:
		return status.Error(codes.InvalidArgument, "unsupported viewer RFB version")
	}
	initialization := make([]byte, 1)
	if _, err := io.ReadFull(viewer.input, initialization); err != nil {
		return err
	}
	frame, _, err := viewer.session.Frame(viewer.ctx)
	if err != nil {
		return err
	}
	viewer.width, viewer.height = frame.Bounds().Dx(), frame.Bounds().Dy()
	pixelFormat := []byte{32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0}
	viewer.format, _ = screenParseViewerFormat(pixelFormat)
	name := []byte("Zoo screen")
	message := make([]byte, 24+len(name))
	binary.BigEndian.PutUint16(message[:2], uint16(viewer.width))
	binary.BigEndian.PutUint16(message[2:4], uint16(viewer.height))
	copy(message[4:20], pixelFormat)
	binary.BigEndian.PutUint32(message[20:24], uint32(len(name)))
	copy(message[24:], name)
	return viewer.write(message)
}

func screenReadViewerMessage(reader io.Reader) ([]byte, error) {
	message := make([]byte, 1)
	if _, err := io.ReadFull(reader, message); err != nil {
		return nil, err
	}
	prefix, length := 0, 0
	switch message[0] {
	case 0:
		length = 20
	case 2:
		prefix = 4
	case 3, 150:
		length = 10
	case 4:
		length = 8
	case 5:
		length = 6
	case 6, 251:
		prefix = 8
	case 248:
		prefix = 9
	case 250:
		length = 4
	case 253:
		prefix = 4
	case 255:
		prefix = 2
	default:
		return nil, status.Error(codes.InvalidArgument, "unknown viewer RFB client message")
	}
	if prefix != 0 {
		message = append(message, make([]byte, prefix-1)...)
		if _, err := io.ReadFull(reader, message[1:]); err != nil {
			return nil, err
		}
		switch message[0] {
		case 2:
			length = 4 + int(binary.BigEndian.Uint16(message[2:4]))*4
		case 6:
			payload := int64(int32(binary.BigEndian.Uint32(message[4:8])))
			if payload < 0 {
				payload = -payload
			}
			if payload > screenViewerMessageLimit-8 {
				return nil, status.Error(codes.ResourceExhausted, "viewer clipboard exceeds the payload limit")
			}
			length = 8 + int(payload)
		case 248:
			if message[8] > 64 {
				return nil, status.Error(codes.InvalidArgument, "viewer fence payload exceeds 64 bytes")
			}
			length = 9 + int(message[8])
		case 251:
			length = 8 + int(message[6])*16
		case 253:
			if message[1]&128 != 0 {
				length = 4 + int(binary.BigEndian.Uint16(message[2:4]))
			} else {
				length = 4 + int(binary.LittleEndian.Uint16(message[2:4]))
			}
		case 255:
			if message[1] != 0 {
				return nil, status.Error(codes.InvalidArgument, "unknown viewer QEMU message")
			}
			length = 12
		}
	}
	if length > screenViewerMessageLimit {
		return nil, status.Error(codes.ResourceExhausted, "viewer RFB message exceeds the payload limit")
	}
	start := len(message)
	message = append(message, make([]byte, length-start)...)
	if _, err := io.ReadFull(reader, message[start:]); err != nil {
		return nil, err
	}
	return message, nil
}

func screenViewerInputMessage(kind byte) bool {
	return kind == 4 || kind == 5 || kind == 6 || kind == 250 || kind == 251 || kind == 253 || kind == 255
}

func screenViewerRegion(message []byte) image.Rectangle {
	horizontal, vertical := int(binary.BigEndian.Uint16(message[2:4])), int(binary.BigEndian.Uint16(message[4:6]))
	return image.Rect(horizontal, vertical, horizontal+int(binary.BigEndian.Uint16(message[6:8])), vertical+int(binary.BigEndian.Uint16(message[8:10])))
}

func (viewer *screenViewer) receive() error {
	for {
		message, err := screenReadViewerMessage(viewer.input)
		if err != nil {
			return err
		}
		if err := viewer.session.Touch(); err != nil {
			return err
		}
		if viewer.viewOnly && screenViewerInputMessage(message[0]) {
			continue
		}
		switch message[0] {
		case 0:
			format, err := screenParseViewerFormat(message[4:])
			if err != nil {
				return err
			}
			viewer.mu.Lock()
			viewer.format = format
			viewer.mu.Unlock()
		case 2:
			var resize int32
			var pixelEncoding int32
			for offset := 4; offset < len(message); offset += 4 {
				encoding := int32(binary.BigEndian.Uint32(message[offset : offset+4]))
				if encoding == vncZlib {
					pixelEncoding = vncZlib
				}
				if encoding == -223 || encoding == -308 && resize == 0 {
					resize = encoding
				}
			}
			viewer.mu.Lock()
			viewer.resize = resize
			viewer.encoding = pixelEncoding
			viewer.mu.Unlock()
		case 3:
			request := screenViewerRequest{region: screenViewerRegion(message), incremental: message[1] != 0}
			select {
			case viewer.requests <- request:
			case <-viewer.ctx.Done():
				return viewer.ctx.Err()
			}
		case 4:
			if err := viewer.session.Key(viewer.ctx, binary.BigEndian.Uint32(message[4:8]), message[1] != 0); err != nil {
				return err
			}
		case 5:
			if err := viewer.session.Pointer(viewer.ctx, int(binary.BigEndian.Uint16(message[2:4])), int(binary.BigEndian.Uint16(message[4:6])), message[1]); err != nil {
				return err
			}
		case 6:
			if int32(binary.BigEndian.Uint32(message[4:8])) < 0 {
				return status.Error(codes.Unimplemented, "extended clipboard is not negotiated by the screen session")
			}
			if len(message)-8 > 64<<10 {
				return status.Error(codes.ResourceExhausted, "viewer clipboard exceeds 64KiB")
			}
			if err := viewer.session.InputMessage(viewer.ctx, message); err != nil {
				return err
			}
		case 251:
			if err := viewer.session.InputMessage(viewer.ctx, message); err != nil {
				return err
			}
		case 250, 253:
			return status.Error(codes.Unimplemented, "viewer XVP and GII controls are not negotiated by the screen session")
		case 255:
			down := binary.BigEndian.Uint16(message[2:4])
			if down > 1 {
				return status.Error(codes.InvalidArgument, "invalid viewer QEMU key state")
			}
			if err := viewer.session.Key(viewer.ctx, binary.BigEndian.Uint32(message[4:8]), down != 0); err != nil {
				return err
			}
		case 248:
			flags := binary.BigEndian.Uint32(message[4:8])
			if flags&0x80000000 != 0 {
				binary.BigEndian.PutUint32(message[4:8], flags&3)
				if err := viewer.write(message); err != nil {
					return err
				}
			}
		case 150:
			viewer.mu.Lock()
			viewer.active = nil
			if message[1] != 0 {
				viewer.active = &screenViewerRequest{region: screenViewerRegion(message), incremental: true, continuous: true}
			}
			viewer.mu.Unlock()
		}
	}
}

func screenViewerChangedBox(previous, current *image.RGBA, region image.Rectangle) image.Rectangle {
	if previous == nil || previous.Bounds() != current.Bounds() {
		return region
	}
	var changed image.Rectangle
	for vertical := region.Min.Y; vertical < region.Max.Y; vertical += 32 {
		for horizontal := region.Min.X; horizontal < region.Max.X; horizontal += 32 {
			tile := image.Rect(horizontal, vertical, min(horizontal+32, region.Max.X), min(vertical+32, region.Max.Y))
			for row := tile.Min.Y; row < tile.Max.Y; row++ {
				left := previous.PixOffset(tile.Min.X, row)
				right := current.PixOffset(tile.Min.X, row)
				if !bytes.Equal(previous.Pix[left:left+tile.Dx()*4], current.Pix[right:right+tile.Dx()*4]) {
					changed = changed.Union(tile)
					break
				}
			}
		}
	}
	return changed
}

func (viewer *screenViewer) update(request screenViewerRequest) error {
	for {
		changed := viewer.session.Changed()
		frame, _, err := viewer.session.Frame(viewer.ctx)
		if err != nil {
			return err
		}
		bounds := frame.Bounds()
		resized := viewer.width != bounds.Dx() || viewer.height != bounds.Dy()
		region := request.region.Intersect(bounds)
		if resized {
			region = bounds
		} else if request.incremental {
			region = screenViewerChangedBox(viewer.previous, frame, region)
		}
		if !region.Empty() || !request.incremental || resized {
			if err := viewer.frame(frame, region, resized); err != nil {
				return err
			}
			if viewer.previous == nil || viewer.previous.Bounds() != bounds {
				viewer.previous = image.NewRGBA(bounds)
			}
			draw.Draw(viewer.previous, region, frame, region.Min, draw.Src)
			viewer.width, viewer.height = bounds.Dx(), bounds.Dy()
			return nil
		}
		if request.continuous {
			return nil
		}
		select {
		case <-viewer.ctx.Done():
			return viewer.ctx.Err()
		case <-changed:
		}
		timer := time.NewTimer(screenViewerFrameInterval)
		select {
		case <-viewer.ctx.Done():
			timer.Stop()
			return viewer.ctx.Err()
		case <-timer.C:
		}
	}
}

func (viewer *screenViewer) render() error {
	defer func() {
		if viewer.compressor != nil {
			viewer.compressor.Close()
		}
	}()
	ticker := time.NewTicker(screenViewerFrameInterval)
	defer ticker.Stop()
	for {
		var request screenViewerRequest
		select {
		case <-viewer.ctx.Done():
			return viewer.ctx.Err()
		case request = <-viewer.requests:
		case <-ticker.C:
			viewer.mu.Lock()
			active := viewer.active
			viewer.mu.Unlock()
			if active == nil {
				continue
			}
			request = *active
		}
		if err := viewer.update(request); err != nil {
			return err
		}
	}
}

func (viewer *screenViewer) frame(frame *image.RGBA, region image.Rectangle, resized bool) error {
	viewer.mu.Lock()
	format, resize, encoding := viewer.format, viewer.resize, viewer.encoding
	viewer.mu.Unlock()
	if resized && resize == 0 {
		return status.Error(codes.FailedPrecondition, "viewer did not negotiate desktop resizing")
	}
	viewer.writeMu.Lock()
	defer viewer.writeMu.Unlock()
	count := uint16(0)
	if !region.Empty() {
		count++
	}
	if resized {
		count++
	}
	header := []byte{0, 0, byte(count >> 8), byte(count)}
	if err := viewer.send(header); err != nil {
		return err
	}
	if resized {
		if err := viewer.send(screenViewerRectangle(frame.Bounds(), resize)); err != nil {
			return err
		}
		if resize == -308 {
			layout := make([]byte, 20)
			layout[0] = 1
			binary.BigEndian.PutUint16(layout[12:14], uint16(frame.Bounds().Dx()))
			binary.BigEndian.PutUint16(layout[14:16], uint16(frame.Bounds().Dy()))
			if err := viewer.send(layout); err != nil {
				return err
			}
		}
	}
	if region.Empty() {
		return nil
	}
	if err := viewer.send(screenViewerRectangle(region, encoding)); err != nil {
		return err
	}
	writePixels := viewer.send
	if encoding == vncZlib {
		viewer.compressed.Reset()
		if viewer.compressor == nil {
			var err error
			viewer.compressor, err = zlib.NewWriterLevel(&viewer.compressed, zlib.BestSpeed)
			if err != nil {
				return err
			}
		}
		writePixels = func(data []byte) error {
			_, err := viewer.compressor.Write(data)
			return err
		}
	}
	row := make([]byte, region.Dx()*format.bytes)
	chunk := make([]byte, 0, 64<<10)
	native := format.bytes == 4 && !format.bigEndian && format.maxima == [3]uint32{255, 255, 255} && format.shifts == [3]uint{16, 8, 0}
	for vertical := region.Min.Y; vertical < region.Max.Y; vertical++ {
		for horizontal := region.Min.X; horizontal < region.Max.X; horizontal++ {
			source := frame.PixOffset(horizontal, vertical)
			destination := (horizontal - region.Min.X) * format.bytes
			if native {
				row[destination], row[destination+1], row[destination+2], row[destination+3] = frame.Pix[source+2], frame.Pix[source+1], frame.Pix[source], 0
				continue
			}
			pixel := uint32(0)
			for component := range 3 {
				pixel |= ((uint32(frame.Pix[source+component])*format.maxima[component] + 127) / 255) << format.shifts[component]
			}
			for index := range format.bytes {
				shift := index * 8
				if format.bigEndian {
					shift = (format.bytes - index - 1) * 8
				}
				row[destination+index] = byte(pixel >> shift)
			}
		}
		if len(chunk)+len(row) > cap(chunk) {
			if err := writePixels(chunk); err != nil {
				return err
			}
			chunk = chunk[:0]
		}
		chunk = append(chunk, row...)
	}
	if err := writePixels(chunk); err != nil {
		return err
	}
	if encoding == vncZlib {
		if err := viewer.compressor.Flush(); err != nil {
			return err
		}
		var length [4]byte
		binary.BigEndian.PutUint32(length[:], uint32(viewer.compressed.Len()))
		if err := viewer.send(length[:]); err != nil {
			return err
		}
		return viewer.send(viewer.compressed.Bytes())
	}
	return nil
}

func screenViewerRectangle(rectangle image.Rectangle, encoding int32) []byte {
	data := make([]byte, 12)
	binary.BigEndian.PutUint16(data[0:2], uint16(rectangle.Min.X))
	binary.BigEndian.PutUint16(data[2:4], uint16(rectangle.Min.Y))
	binary.BigEndian.PutUint16(data[4:6], uint16(rectangle.Dx()))
	binary.BigEndian.PutUint16(data[6:8], uint16(rectangle.Dy()))
	binary.BigEndian.PutUint32(data[8:12], uint32(encoding))
	return data
}

func (service *gatewayServer) ViewerStream(rpc grpc.BidiStreamingServer[gatewaypb.ViewerFrame, gatewaypb.Bytes]) error {
	first, err := rpc.Recv()
	if err != nil {
		return err
	}
	if len(first.Data) > screenViewerMessageLimit {
		return status.Error(codes.ResourceExhausted, "viewer initial payload exceeds the limit")
	}
	ctx, cancel := context.WithCancel(rpc.Context())
	defer cancel()
	opening, finish := context.WithTimeout(ctx, 30*time.Second)
	entry, err := service.screens.acquire(opening, first.SandboxId)
	finish()
	if err != nil {
		return err
	}
	if entry.viewers.Add(1) > 32 {
		entry.viewers.Add(-1)
		return status.Error(codes.ResourceExhausted, "sandbox viewer capacity exceeded")
	}
	defer entry.viewers.Add(-1)
	viewer := &screenViewer{ctx: ctx, rpc: rpc, input: &screenViewerInput{rpc: rpc, sandboxID: first.SandboxId, data: first.Data},
		session: entry.session, viewOnly: first.ViewOnly, requests: make(chan screenViewerRequest, 16)}
	handshake := make(chan error, 1)
	go func() { handshake <- viewer.handshake() }()
	timer := time.NewTimer(10 * time.Second)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return status.FromContextError(ctx.Err()).Err()
	case <-entry.session.Done():
		return screenTransportError(entry.session.Err())
	case <-timer.C:
		return status.Error(codes.DeadlineExceeded, "viewer RFB handshake timed out")
	case err := <-handshake:
		if err != nil {
			return screenTransportError(err)
		}
	}
	failures := make(chan error, 2)
	go func() { failures <- viewer.receive() }()
	go func() { failures <- viewer.render() }()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()
	for {
		select {
		case <-ctx.Done():
			return status.FromContextError(ctx.Err()).Err()
		case <-entry.session.Done():
			return screenTransportError(entry.session.Err())
		case err := <-failures:
			if errors.Is(err, io.EOF) {
				return nil
			}
			return screenTransportError(err)
		case <-keepalive.C:
			if err := entry.session.Touch(); err != nil {
				return screenTransportError(err)
			}
		}
	}
}
