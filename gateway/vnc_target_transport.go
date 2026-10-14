package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

type vncTransport struct {
	net.Conn
	cancel context.CancelFunc
	once   sync.Once
}

func (transport *vncTransport) Close() error {
	transport.once.Do(transport.cancel)
	return transport.Conn.Close()
}

func vncPipe(ctx context.Context) (*vncTransport, net.Conn, context.Context) {
	ctx, cancel := context.WithCancel(ctx)
	client, bridge := net.Pipe()
	transport := &vncTransport{Conn: client, cancel: cancel}
	go func() {
		<-ctx.Done()
		client.Close()
		bridge.Close()
	}()
	return transport, bridge, ctx
}

type vncGuestStream struct {
	grpc.ServerStream
	ctx       context.Context
	sandboxID string
	bridge    net.Conn
	first     bool
	opened    chan error
}

func (stream *vncGuestStream) Context() context.Context {
	return stream.ctx
}

func (stream *vncGuestStream) Recv() (*gatewaypb.Frame, error) {
	if !stream.first {
		stream.first = true
		return &gatewaypb.Frame{SandboxId: stream.sandboxID, HeaderJson: []byte(`{"id":1,"op":"tunnel_open","args":{"stream":"vnc","port":5900}}`)}, nil
	}
	data := make([]byte, 64<<10)
	count, err := stream.bridge.Read(data)
	if err != nil {
		return nil, err
	}
	return &gatewaypb.Frame{SandboxId: stream.sandboxID, HeaderJson: []byte(`{"op":"tunnel_write","args":{"stream":"vnc"}}`), Payload: data[:count]}, nil
}

func (stream *vncGuestStream) Send(frame *gatewaypb.Frame) error {
	header, err := decodeGuestHeader(frame.HeaderJson)
	if err != nil {
		return err
	}
	if headerID(header) != 0 {
		var accepted bool
		if json.Unmarshal(header["ok"], &accepted) != nil || !accepted {
			err = status.Error(codes.Unavailable, "the guest VNC tunnel could not be opened")
		}
		select {
		case stream.opened <- err:
		default:
		}
		return err
	}
	switch headerString(header, "op") {
	case "tunnel_data":
		_, err = stream.bridge.Write(frame.Payload)
		return err
	case "tunnel_close":
		return io.EOF
	default:
		return status.Error(codes.DataLoss, "unexpected guest VNC tunnel frame")
	}
}

func dialGuestVNC(ctx context.Context, service *gatewayServer, sandboxID string) (net.Conn, error) {
	transport, bridge, lifetime := vncPipe(ctx)
	stream := &vncGuestStream{ctx: lifetime, sandboxID: sandboxID, bridge: bridge, opened: make(chan error, 1)}
	go func() {
		err := service.GuestStream(stream)
		if err == nil {
			err = io.EOF
		}
		select {
		case stream.opened <- err:
		default:
		}
		transport.Close()
	}()
	timer := time.NewTimer(10 * time.Second)
	defer timer.Stop()
	select {
	case err := <-stream.opened:
		if err != nil {
			transport.Close()
			return nil, err
		}
		return transport, nil
	case <-ctx.Done():
		transport.Close()
		return nil, ctx.Err()
	case <-timer.C:
		transport.Close()
		return nil, status.Error(codes.DeadlineExceeded, "the guest VNC tunnel did not open")
	}
}

func dialNodeSSHTransport(ctx context.Context, service *gatewayServer, serverID string) (net.Conn, error) {
	owner, err := service.routes.peers.Owner(serverID)
	if err != nil {
		return nil, status.Error(codes.Unavailable, "gateway membership is unavailable")
	}
	client, err := service.routes.client(owner)
	if err != nil {
		return nil, status.Error(codes.Unavailable, "the node owner is unavailable")
	}
	transport, bridge, lifetime := vncPipe(ctx)
	lifetime = metadata.NewOutgoingContext(lifetime, metadata.Pairs("authorization", "Bearer "+service.settings.token("zoo-gateway"), "server_id", serverID, "target", "ssh"))
	stream, err := client.NodeTunnel(lifetime)
	if err != nil {
		transport.Close()
		return nil, err
	}
	go func() {
		defer transport.Close()
		for {
			message, err := stream.Recv()
			if err != nil {
				return
			}
			if _, err := bridge.Write(message.Data); err != nil {
				return
			}
		}
	}()
	go func() {
		defer transport.Close()
		defer stream.CloseSend()
		data := make([]byte, nodeTunnelChunk)
		for {
			count, err := bridge.Read(data)
			if count > 0 {
				if failure := stream.Send(&gatewaypb.Bytes{Data: data[:count]}); failure != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()
	return transport, nil
}

func screenTransportError(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return status.FromContextError(err).Err()
	}
	if _, valid := status.FromError(err); valid {
		return err
	}
	return status.Error(codes.Unavailable, "the sandbox screen transport is unavailable")
}
