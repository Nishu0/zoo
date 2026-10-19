package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"hash/fnv"
	"image"
	"image/jpeg"
	"math"
	"strings"
	"time"
	"unicode/utf8"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

func screenImageOptions(request *gatewaypb.ScreenRequest) (string, float64, int, error) {
	format, scale, quality := strings.ToLower(request.Format), request.Scale, int(request.Quality)
	if format == "" {
		format = "png"
	}
	if scale == 0 {
		scale = 1
	}
	if quality == 0 {
		quality = 80
	}
	if format != "png" && format != "jpeg" || math.IsNaN(scale) || scale <= 0 || scale > 1 || quality < 1 || quality > 100 {
		return "", 0, 0, status.Error(codes.InvalidArgument, "screen format must be png or jpeg, scale in (0,1], and quality in [1,100]")
	}
	return format, scale, quality, nil
}

func screenEncode(frame *image.RGBA, format string, scale float64, quality int) ([]byte, uint32, uint32, error) {
	if scale < 1 {
		frame = screenScale(frame, scale)
	}
	var data []byte
	var err error
	if format == "jpeg" {
		var output bytes.Buffer
		started := time.Now()
		err = jpeg.Encode(&output, frame, &jpeg.Options{Quality: quality})
		if err == nil {
			observeScreenEncode(time.Since(started))
		}
		data = output.Bytes()
	} else {
		data, err = fastPNG(frame)
	}
	return data, uint32(frame.Bounds().Dx()), uint32(frame.Bounds().Dy()), err
}

func screenScale(frame *image.RGBA, scale float64) *image.RGBA {
	bounds := frame.Bounds()
	width, height := max(1, int(float64(bounds.Dx())*scale+0.5)), max(1, int(float64(bounds.Dy())*scale+0.5))
	result := image.NewRGBA(image.Rect(0, 0, width, height))
	for vertical := range height {
		positionY := max(0, (float64(vertical)+0.5)*float64(bounds.Dy())/float64(height)-0.5)
		firstY := int(positionY)
		secondY := min(firstY+1, bounds.Dy()-1)
		weightY := positionY - float64(firstY)
		for horizontal := range width {
			positionX := max(0, (float64(horizontal)+0.5)*float64(bounds.Dx())/float64(width)-0.5)
			firstX := int(positionX)
			secondX := min(firstX+1, bounds.Dx()-1)
			weightX := positionX - float64(firstX)
			upperLeft := frame.PixOffset(bounds.Min.X+firstX, bounds.Min.Y+firstY)
			upperRight := frame.PixOffset(bounds.Min.X+secondX, bounds.Min.Y+firstY)
			lowerLeft := frame.PixOffset(bounds.Min.X+firstX, bounds.Min.Y+secondY)
			lowerRight := frame.PixOffset(bounds.Min.X+secondX, bounds.Min.Y+secondY)
			destination := result.PixOffset(horizontal, vertical)
			for component := range 4 {
				upper := float64(frame.Pix[upperLeft+component])*(1-weightX) + float64(frame.Pix[upperRight+component])*weightX
				lower := float64(frame.Pix[lowerLeft+component])*(1-weightX) + float64(frame.Pix[lowerRight+component])*weightX
				result.Pix[destination+component] = byte(upper*(1-weightY) + lower*weightY + 0.5)
			}
		}
	}
	return result
}

func (service *gatewayServer) ScreenShot(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	format, scale, quality, err := screenImageOptions(request)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	entry, err := service.screens.acquire(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	if format == "png" && scale == 1 {
		data, version, err := entry.session.Screenshot(ctx)
		if err != nil {
			return nil, screenTransportError(err)
		}
		if len(data) < 24 {
			return nil, status.Error(codes.Internal, "invalid encoded screen frame")
		}
		return &gatewaypb.ScreenReply{Image: data, Width: binary.BigEndian.Uint32(data[16:20]), Height: binary.BigEndian.Uint32(data[20:24]), Version: version}, nil
	}
	frame, version, err := entry.session.Frame(ctx)
	if err != nil {
		return nil, screenTransportError(err)
	}
	data, width, height, err := screenEncode(frame, format, scale, quality)
	if err != nil {
		return nil, status.Error(codes.Internal, "screen image encoding failed")
	}
	return &gatewaypb.ScreenReply{Image: data, Width: width, Height: height, Version: version}, nil
}

func (service *gatewayServer) ScreenDiff(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	format, scale, quality, err := screenImageOptions(request)
	if err != nil {
		return nil, err
	}
	if len(request.Session) > 128 {
		return nil, status.Error(codes.InvalidArgument, "screen diff session name is too long")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	entry, err := service.screens.acquire(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	select {
	case <-ctx.Done():
		return nil, status.FromContextError(ctx.Err()).Err()
	case <-entry.diffLock:
	}
	defer func() { entry.diffLock <- struct{}{} }()
	since := request.SinceVersion
	if since == 0 && request.Session != "" {
		since = entry.baselines[request.Session]
	}
	diff, err := entry.session.Diff(ctx, since)
	if err != nil {
		if ctx.Err() != nil {
			return nil, status.FromContextError(ctx.Err()).Err()
		}
		if entry.session.Err() != nil {
			return nil, screenTransportError(err)
		}
		return nil, status.Error(codes.InvalidArgument, "screen diff version is ahead of the current framebuffer")
	}
	frame := diff.Frame
	reply := &gatewaypb.ScreenReply{Width: uint32(frame.Bounds().Dx()), Height: uint32(frame.Bounds().Dy()), Version: diff.Version, Changed: len(diff.ChangedTiles) != 0}
	columns := (frame.Bounds().Dx() + 31) / 32
	var box image.Rectangle
	total := 0
	for _, tileID := range diff.ChangedTiles {
		horizontal, vertical := int(tileID)%columns*32, int(tileID)/columns*32
		rectangle := image.Rect(horizontal, vertical, horizontal+32, vertical+32).Intersect(frame.Bounds())
		crop := frame.SubImage(rectangle).(*image.RGBA)
		encoded, err := fastPNG(crop)
		if err != nil {
			return nil, status.Error(codes.Internal, "screen tile encoding failed")
		}
		digest := fnv.New64a()
		for row := rectangle.Min.Y; row < rectangle.Max.Y; row++ {
			start := frame.PixOffset(rectangle.Min.X, row)
			digest.Write(frame.Pix[start : start+rectangle.Dx()*4])
		}
		reply.ChangedTiles = append(reply.ChangedTiles, &gatewaypb.ScreenTile{X: uint32(horizontal), Y: uint32(vertical), Width: uint32(rectangle.Dx()), Height: uint32(rectangle.Dy()), Hash: digest.Sum64(), Data: encoded})
		total += len(encoded)
		if total > 64<<20 {
			return nil, status.Error(codes.ResourceExhausted, "screen diff exceeds the image payload limit")
		}
		box = box.Union(rectangle)
	}
	for _, rectangle := range diff.Boxes {
		reply.Boxes = append(reply.Boxes, &gatewaypb.ScreenBox{X: uint32(rectangle.Min.X), Y: uint32(rectangle.Min.Y), Width: uint32(rectangle.Dx()), Height: uint32(rectangle.Dy())})
	}
	if !box.Empty() {
		reply.X, reply.Y, reply.RegionWidth, reply.RegionHeight = uint32(box.Min.X), uint32(box.Min.Y), uint32(box.Dx()), uint32(box.Dy())
		reply.Image, _, _, err = screenEncode(frame.SubImage(box).(*image.RGBA), format, scale, quality)
		if err != nil {
			return nil, status.Error(codes.Internal, "screen diff image encoding failed")
		}
		if total+len(reply.Image) > 64<<20 {
			return nil, status.Error(codes.ResourceExhausted, "screen diff exceeds the image payload limit")
		}
	}
	if request.Session != "" {
		if _, exists := entry.baselines[request.Session]; !exists && len(entry.baselines) >= 16 {
			for name := range entry.baselines {
				delete(entry.baselines, name)
				break
			}
		}
		entry.baselines[request.Session] = diff.Version
	}
	return reply, nil
}

func (service *gatewayServer) WaitStable(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	timeout, quiet := request.TimeoutMs, request.QuietMs
	if timeout == 0 {
		timeout = 5000
	}
	if quiet == 0 {
		quiet = 500
	}
	if timeout > 60000 || quiet > 60000 || math.IsNaN(request.Threshold) || request.Threshold < 0 || request.Threshold >= 1 {
		return nil, status.Error(codes.InvalidArgument, "stability timeout/quiet must be at most 60000ms and threshold in [0,1)")
	}
	entry, err := service.screens.acquire(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	start := time.Now()
	var version uint64
	if request.Threshold == 0 {
		version, err = entry.session.WaitStable(ctx, time.Duration(quiet)*time.Millisecond, time.Duration(timeout)*time.Millisecond)
	} else {
		version, err = screenWaitThreshold(ctx, entry.session, time.Duration(quiet)*time.Millisecond, time.Duration(timeout)*time.Millisecond, request.Threshold)
	}
	if err != nil && (!errors.Is(err, context.DeadlineExceeded) || ctx.Err() != nil) {
		return nil, screenTransportError(err)
	}
	return &gatewaypb.ScreenReply{Version: version, Stable: err == nil, ElapsedMs: uint64(time.Since(start).Milliseconds())}, nil
}

func screenWaitThreshold(ctx context.Context, session *VNCSession, quiet, timeout time.Duration, threshold float64) (uint64, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	previous, version, err := session.Frame(ctx)
	if err != nil {
		return 0, err
	}
	lastChange := time.Now()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		if time.Since(lastChange) >= quiet {
			return version, nil
		}
		select {
		case <-ctx.Done():
			return version, ctx.Err()
		case <-ticker.C:
		}
		current, next, err := session.Frame(ctx)
		if err != nil {
			return 0, err
		}
		if screenChangedFraction(previous, current) > threshold {
			lastChange = time.Now()
		}
		previous, version = current, next
	}
}

func screenChangedFraction(previous, current *image.RGBA) float64 {
	if previous == nil || previous.Bounds() != current.Bounds() {
		return 1
	}
	changed, total := 0, 0
	for vertical := current.Rect.Min.Y; vertical < current.Rect.Max.Y; vertical += 32 {
		for horizontal := current.Rect.Min.X; horizontal < current.Rect.Max.X; horizontal += 32 {
			total++
			for row := vertical; row < min(vertical+32, current.Rect.Max.Y); row++ {
				left := previous.PixOffset(horizontal, row)
				right := current.PixOffset(horizontal, row)
				length := min(32, current.Rect.Max.X-horizontal) * 4
				if !bytes.Equal(previous.Pix[left:left+length], current.Pix[right:right+length]) {
					changed++
					break
				}
			}
		}
	}
	return float64(changed) / float64(max(1, total))
}

func (service *gatewayServer) Pointer(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	if request.X < 0 || request.Y < 0 || request.X > 65535 || request.Y > 65535 || request.Mask > 255 {
		return nil, status.Error(codes.InvalidArgument, "invalid screen pointer coordinates or mask")
	}
	entry, err := service.screens.acquire(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	frame, _, err := entry.session.Frame(ctx)
	if err != nil {
		return nil, screenTransportError(err)
	}
	if int(request.X) >= frame.Rect.Dx() || int(request.Y) >= frame.Rect.Dy() {
		return nil, status.Error(codes.InvalidArgument, "screen pointer is outside the framebuffer")
	}
	return &gatewaypb.ScreenReply{}, screenTransportError(entry.session.Pointer(ctx, int(request.X), int(request.Y), byte(request.Mask)))
}

func (service *gatewayServer) Key(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	entry, err := service.screens.acquire(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	return &gatewaypb.ScreenReply{}, screenTransportError(entry.session.Key(ctx, request.Keysym, request.Down))
}

func (service *gatewayServer) Type(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	if len(request.Text) > 64<<10 || !utf8.ValidString(request.Text) {
		return nil, status.Error(codes.InvalidArgument, "screen text must be valid UTF-8 and at most 64KiB")
	}
	entry, err := service.screens.acquire(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	return &gatewaypb.ScreenReply{}, screenTransportError(entry.session.Type(ctx, request.Text))
}

func (service *gatewayServer) CloseScreen(ctx context.Context, request *gatewaypb.ScreenRequest) (*gatewaypb.ScreenReply, error) {
	if err := service.routes.requireOwner(ctx, request.SandboxId); err != nil {
		return nil, err
	}
	service.screens.closeSandbox(request.SandboxId)
	return &gatewaypb.ScreenReply{}, nil
}
