package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"image"
	"image/color"
	"image/png"
	"io"
	"math"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

func screenTestFixture(test *testing.T) (*gatewayServer, *VNCSession, *fakeRFB) {
	test.Helper()
	session, fake := startFakeRFB(test, 64, 64, "password")
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 64, 64), vncRaw, fakeRFBPixels(64, 64, color.RGBA{R: 100, G: 120, B: 140, A: 255})))
	awaitVNCVersion(test, session, 1)
	service := newGatewayServer(config{})
	peers, _ := testPeerSet(test, "127.0.0.1")
	service.routes = newReplicaRouter(peers, nodeTestCA(test), config{})
	entry := &screenEntry{ready: make(chan struct{}), session: session, cancel: func() {}, diffLock: make(chan struct{}, 1), baselines: make(map[string]uint64)}
	entry.diffLock <- struct{}{}
	close(entry.ready)
	service.screens = &screenService{service: service, ctx: test.Context(), entries: map[string]*screenEntry{"sandbox": entry}}
	test.Cleanup(service.routes.close)
	return service, session, fake
}

func TestScreenRPCUsesSharedFramebufferAndVersionedTiles(test *testing.T) {
	service, session, fake := screenTestFixture(test)
	ctx, cancel := context.WithTimeout(test.Context(), 3*time.Second)
	defer cancel()
	shot, err := service.ScreenShot(ctx, &gatewaypb.ScreenRequest{SandboxId: "sandbox"})
	if err != nil || shot.Width != 64 || shot.Height != 64 || shot.Version == 0 {
		test.Fatalf("invalid shared screen: %v %v", shot, err)
	}
	decoded, err := png.Decode(bytes.NewReader(shot.Image))
	if err != nil || decoded.Bounds() != image.Rect(0, 0, 64, 64) {
		test.Fatalf("screen PNG changed geometry: %v", err)
	}
	for _, caller := range []string{"alice", "bob"} {
		first, err := service.ScreenDiff(ctx, &gatewaypb.ScreenRequest{SandboxId: "sandbox", Session: caller})
		if err != nil || !first.Changed || first.Version != shot.Version || len(first.ChangedTiles) != 4 || len(first.Boxes) != 4 {
			test.Fatalf("caller baseline or 32px tiles missing: %v %v", first, err)
		}
		for _, tile := range first.ChangedTiles {
			if tile.Width != 32 || tile.Height != 32 || tile.Hash == 0 {
				test.Fatalf("invalid changed tile metadata: %v", tile)
			}
			if _, err := png.Decode(bytes.NewReader(tile.Data)); err != nil {
				test.Fatal(err)
			}
		}
		unchanged, err := service.ScreenDiff(ctx, &gatewaypb.ScreenRequest{SandboxId: "sandbox", Session: caller})
		if err != nil || unchanged.Changed || len(unchanged.Image) != 0 || len(unchanged.ChangedTiles) != 0 {
			test.Fatalf("unchanged frame was resent: %v %v", unchanged, err)
		}
	}
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(40, 40, 41, 41), vncRaw, fakeRFBPixels(1, 1, color.RGBA{R: 255, A: 255})))
	_, changedVersion := awaitVNCVersion(test, session, shot.Version+1)
	diff, err := service.ScreenDiff(ctx, &gatewaypb.ScreenRequest{SandboxId: "sandbox", SinceVersion: shot.Version})
	if err != nil || diff.Version != changedVersion || len(diff.ChangedTiles) != 1 || diff.X != 32 || diff.Y != 32 || diff.RegionWidth != 32 || diff.RegionHeight != 32 {
		test.Fatalf("since-version diff did not isolate the changed tile: %v %v", diff, err)
	}
	if _, err := service.ScreenDiff(ctx, &gatewaypb.ScreenRequest{SandboxId: "sandbox", SinceVersion: changedVersion + 100}); status.Code(err) != codes.InvalidArgument {
		test.Fatalf("future version accepted: %v", err)
	}
	jpeg, err := service.ScreenShot(ctx, &gatewaypb.ScreenRequest{SandboxId: "sandbox", Format: "jpeg", Scale: 0.5, Quality: 80})
	if err != nil || jpeg.Width != 32 || jpeg.Height != 32 || len(jpeg.Image) < 2 || !bytes.Equal(jpeg.Image[:2], []byte{255, 216}) {
		test.Fatalf("native JPEG/scaled frame failed: %v %v", jpeg, err)
	}
}

func TestScreenRPCValidationAndLifecycle(test *testing.T) {
	service, session, _ := screenTestFixture(test)
	for _, request := range []*gatewaypb.ScreenRequest{{Format: "webp"}, {Scale: -1}, {Scale: math.NaN()}, {Scale: 2}, {Quality: 101}} {
		request.SandboxId = "sandbox"
		if _, err := service.ScreenShot(test.Context(), request); status.Code(err) != codes.InvalidArgument {
			test.Fatalf("invalid image options accepted: %v", request)
		}
	}
	for _, request := range []*gatewaypb.ScreenRequest{{X: -1}, {Y: -1}, {X: 65536}, {Mask: 256}, {X: 64}} {
		request.SandboxId = "sandbox"
		if _, err := service.Pointer(test.Context(), request); status.Code(err) != codes.InvalidArgument {
			test.Fatalf("invalid pointer accepted: %v", request)
		}
	}
	stable, err := service.WaitStable(test.Context(), &gatewaypb.ScreenRequest{SandboxId: "sandbox", QuietMs: 1, TimeoutMs: 1000})
	if err != nil || !stable.Stable || stable.Version == 0 {
		test.Fatalf("quiet cached frame not stable: %v %v", stable, err)
	}
	if _, err := service.CloseScreen(test.Context(), &gatewaypb.ScreenRequest{SandboxId: "sandbox"}); err != nil {
		test.Fatal(err)
	}
	select {
	case <-session.Done():
	default:
		test.Fatal("sandbox lifecycle did not close the persistent VNC session")
	}
}

type screenTestViewerRPC struct {
	grpc.ServerStream
	ctx    context.Context
	frames []*gatewaypb.ViewerFrame
	mu     sync.Mutex
	sent   [][]byte
}

func (stream *screenTestViewerRPC) Context() context.Context { return stream.ctx }

func (stream *screenTestViewerRPC) Recv() (*gatewaypb.ViewerFrame, error) {
	if len(stream.frames) == 0 {
		return nil, io.EOF
	}
	frame := stream.frames[0]
	stream.frames = stream.frames[1:]
	return frame, nil
}

func (stream *screenTestViewerRPC) Send(frame *gatewaypb.Bytes) error {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	stream.sent = append(stream.sent, bytes.Clone(frame.Data))
	return nil
}

func TestScreenViewerRFBHandshakeAndFramebuffer(test *testing.T) {
	_, session, _ := screenTestFixture(test)
	input := append([]byte("RFB 003.008\n"), 1, 1)
	rpc := &screenTestViewerRPC{ctx: test.Context()}
	viewer := &screenViewer{ctx: test.Context(), rpc: rpc, session: session,
		input: &screenViewerInput{rpc: rpc, sandboxID: "sandbox", data: input}}
	if err := viewer.handshake(); err != nil {
		test.Fatal(err)
	}
	if len(rpc.sent) != 4 || string(rpc.sent[0]) != "RFB 003.008\n" || !bytes.Equal(rpc.sent[1], []byte{1, 1}) || binary.BigEndian.Uint16(rpc.sent[3][:2]) != 64 {
		test.Fatalf("browser RFB handshake is incompatible: %v", rpc.sent)
	}
	frame, _, err := session.Frame(test.Context())
	if err != nil {
		test.Fatal(err)
	}
	rpc.sent = nil
	region := image.Rect(0, 0, 2, 1)
	if err := viewer.frame(frame, region, false); err != nil {
		test.Fatal(err)
	}
	wire := bytes.Join(rpc.sent, nil)
	if len(wire) != 24 || !bytes.Equal(wire[:4], []byte{0, 0, 0, 1}) || !bytes.Equal(wire[16:20], []byte{140, 120, 100, 0}) {
		test.Fatalf("synthetic framebuffer changed RFB protocol bytes: %v", wire)
	}
}

func TestScreenViewerViewOnlyGateAndFragmentedFrames(test *testing.T) {
	_, session, fake := screenTestFixture(test)
	awaitRFBCommand(test, fake, 3)
	key := []byte{4, 1, 0, 0, 0, 0, 0, 65}
	pointer := []byte{5, 1, 0, 1, 0, 1}
	clipboard := []byte{6, 0, 0, 0, 0, 0, 0, 1, 'x'}
	input := append(append(append([]byte{}, key...), pointer...), clipboard...)
	rpc := &screenTestViewerRPC{ctx: test.Context(), frames: []*gatewaypb.ViewerFrame{{Data: input[:2], ViewOnly: false}, {SandboxId: "sandbox", Data: input[2:]}}}
	viewer := &screenViewer{ctx: test.Context(), rpc: rpc, session: session, viewOnly: true,
		input: &screenViewerInput{rpc: rpc, sandboxID: "sandbox"}}
	if err := viewer.receive(); !errors.Is(err, io.EOF) || !viewer.viewOnly {
		test.Fatalf("view-only gate failed or was changed by subsequent flags: %v", err)
	}
	for {
		select {
		case command := <-fake.commands:
			if screenViewerInputMessage(command[0]) {
				test.Fatalf("view-only forwarded input: %v", command)
			}
		default:
			return
		}
	}
}

func TestScreenViewerRejectsUnknownOversizedAndCrossSandboxMessages(test *testing.T) {
	for _, message := range [][]byte{{99}, {255, 1}, {6, 0, 0, 0, 127, 255, 255, 255}, {248, 0, 0, 0, 0, 0, 0, 0, 65}} {
		if _, err := screenReadViewerMessage(bytes.NewReader(message)); err == nil {
			test.Fatalf("unbounded or unknown viewer message accepted: %v", message)
		}
	}
	rpc := &screenTestViewerRPC{frames: []*gatewaypb.ViewerFrame{{SandboxId: "other", Data: []byte{1}}}}
	reader := &screenViewerInput{rpc: rpc, sandboxID: "sandbox"}
	if _, err := reader.Read(make([]byte, 1)); status.Code(err) != codes.InvalidArgument {
		test.Fatalf("viewer switched sandbox midstream: %v", err)
	}
	if fraction := screenChangedFraction(image.NewRGBA(image.Rect(0, 0, 1, 1)), image.NewRGBA(image.Rect(0, 0, 2, 2))); fraction != 1 {
		test.Fatal("screen geometry change was not counted as a full change")
	}
}
