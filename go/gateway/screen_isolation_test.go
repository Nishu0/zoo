package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"image"
	"image/color"
	"testing"
	"time"

	"google.golang.org/grpc"
	"zoo/gateway/gatewaypb"
)

type isolationViewerRPC struct {
	grpc.ServerStream
	ctx    context.Context
	frames chan *gatewaypb.ViewerFrame
	sent   chan []byte
}

func (rpc *isolationViewerRPC) Context() context.Context { return rpc.ctx }
func (rpc *isolationViewerRPC) Recv() (*gatewaypb.ViewerFrame, error) {
	select {
	case <-rpc.ctx.Done():
		return nil, rpc.ctx.Err()
	case frame := <-rpc.frames:
		return frame, nil
	}
}
func (rpc *isolationViewerRPC) Send(frame *gatewaypb.Bytes) error {
	select {
	case <-rpc.ctx.Done():
		return rpc.ctx.Err()
	case rpc.sent <- bytes.Clone(frame.Data):
		return nil
	}
}

func TestScreenViewersKeepOtherSandboxesAndUpstreamConnectionsAlive(test *testing.T) {
	service, firstSession, _ := screenTestFixture(test)
	secondSession, secondRFB := startFakeRFB(test, 64, 64, "other-password")
	fakeRFBSend(test, secondRFB, fakeRFBRectangle(image.Rect(0, 0, 64, 64), vncRaw,
		fakeRFBPixels(64, 64, color.RGBA{G: 200, A: 255})))
	awaitVNCVersion(test, secondSession, 1)
	secondEntry := &screenEntry{ready: make(chan struct{}), session: secondSession, cancel: func() {}}
	close(secondEntry.ready)
	service.screens.entries["other"] = secondEntry

	startViewer := func(sandbox string) (*isolationViewerRPC, func()) {
		test.Helper()
		ctx, cancel := context.WithCancel(test.Context())
		rpc := &isolationViewerRPC{ctx: ctx, frames: make(chan *gatewaypb.ViewerFrame, 4), sent: make(chan []byte, 32)}
		rpc.frames <- &gatewaypb.ViewerFrame{SandboxId: sandbox, Data: append([]byte("RFB 003.008\n"), 1, 1)}
		done := make(chan error, 1)
		go func() { done <- service.ViewerStream(rpc) }()
		stop := func() {
			cancel()
			select {
			case <-done:
			case <-time.After(time.Second):
				test.Error("viewer cancellation did not finish its stream")
			}
		}
		test.Cleanup(func() { cancel() })
		for range 4 { // RFB version, security types, security result and desktop initialization.
			select {
			case <-rpc.sent:
			case <-time.After(time.Second):
				test.Fatal("viewer handshake did not complete")
			}
		}
		return rpc, stop
	}

	_, stopFirst := startViewer("sandbox")
	secondViewer, stopSecond := startViewer("other")
	defer stopSecond()
	stopFirst()
	if firstSession.Err() != nil || secondSession.Err() != nil {
		test.Fatal("opening another sandbox or disconnecting its sibling closed an upstream VNC session")
	}

	// A reconnect must adopt the original persistent upstream rather than close/reopen either VM's VNC server.
	_, stopReconnected := startViewer("sandbox")
	defer stopReconnected()
	request := make([]byte, 10)
	request[0] = 3
	binary.BigEndian.PutUint16(request[6:8], 64)
	binary.BigEndian.PutUint16(request[8:10], 64)
	secondViewer.frames <- &gatewaypb.ViewerFrame{Data: request}
	select {
	case header := <-secondViewer.sent:
		if !bytes.Equal(header, []byte{0, 0, 0, 1}) {
			test.Fatalf("second viewer no longer receives framebuffer updates: %v", header)
		}
	case <-time.After(time.Second):
		test.Fatal("second viewer stopped receiving frames after the first viewer reconnected")
	}
	if service.screens.entries["sandbox"].session != firstSession || secondSession.Err() != nil {
		test.Fatal("viewer reconnect replaced its upstream or closed the other sandbox")
	}
}
