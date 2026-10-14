package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"

	"github.com/chann44/zoo/node/nodepb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"zoo/gateway/gatewaypb"
)

type nodeLinkRouter struct {
	nodepb.UnimplementedNodeServer
	hub    *nodeHub
	local  string
	owner  func(string) (string, error)
	client func(string) (gatewaypb.GatewayClient, error)
	key    string
}

func (router *nodeLinkRouter) Connect(stream grpc.BidiStreamingServer[nodepb.NodeMessage, nodepb.ApiMessage]) error {
	record, err := router.hub.authenticate(stream.Context())
	if err != nil {
		return err
	}
	owner, err := router.owner(record.ServerID)
	if err != nil {
		return status.Error(codes.Unavailable, "gateway membership is unavailable")
	}
	if owner == router.local {
		return (&nativeNodeConnect{hub: router.hub}).Connect(stream)
	}
	client, err := router.client(owner)
	if err != nil {
		return status.Error(codes.Unavailable, "the node owner is unavailable")
	}
	ctx, cancel := context.WithCancel(stream.Context())
	defer cancel()
	ctx = metadata.NewOutgoingContext(ctx, metadata.Pairs("authorization", "Bearer "+router.key))
	relay, err := client.RelayNode(ctx)
	if err != nil {
		return err
	}
	remote, _ := peer.FromContext(stream.Context())
	authenticated := remote.AuthInfo.(credentials.TLSInfo)
	if err := relay.Send(&gatewaypb.NodeRelayFrame{CertificateDer: authenticated.State.PeerCertificates[0].Raw}); err != nil {
		return err
	}
	failures := make(chan error, 1)
	go func() {
		for {
			message, err := stream.Recv()
			if errors.Is(err, io.EOF) {
				failures <- relay.CloseSend()
				return
			}
			if err != nil {
				failures <- err
				return
			}
			encoded, err := proto.Marshal(message)
			if err == nil {
				err = relay.Send(&gatewaypb.NodeRelayFrame{Message: encoded})
			}
			if err != nil {
				failures <- err
				cancel()
				return
			}
		}
	}()
	for {
		encoded, err := relay.Recv()
		if err != nil {
			select {
			case failure := <-failures:
				if failure != nil {
					return failure
				}
			default:
			}
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		message := new(nodepb.ApiMessage)
		if err := proto.Unmarshal(encoded.Data, message); err != nil {
			return status.Error(codes.DataLoss, "invalid node owner response")
		}
		if err := stream.Send(message); err != nil {
			return err
		}
	}
}

type relayedNodeStream struct {
	grpc.ServerStream
	stream grpc.BidiStreamingServer[gatewaypb.NodeRelayFrame, gatewaypb.Bytes]
	ctx    context.Context
}

func (stream *relayedNodeStream) Context() context.Context {
	return stream.ctx
}

func (stream *relayedNodeStream) Recv() (*nodepb.NodeMessage, error) {
	frame, err := stream.stream.Recv()
	if err != nil {
		return nil, err
	}
	if len(frame.CertificateDer) != 0 || len(frame.Message) == 0 {
		return nil, status.Error(codes.InvalidArgument, "invalid relayed node frame")
	}
	message := new(nodepb.NodeMessage)
	if err := proto.Unmarshal(frame.Message, message); err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid relayed node message")
	}
	return message, nil
}

func (stream *relayedNodeStream) Send(message *nodepb.ApiMessage) error {
	encoded, err := proto.Marshal(message)
	if err != nil {
		return err
	}
	return stream.stream.Send(&gatewaypb.Bytes{Data: encoded})
}

func (router *nodeLinkRouter) RelayNode(stream grpc.BidiStreamingServer[gatewaypb.NodeRelayFrame, gatewaypb.Bytes]) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	if len(first.CertificateDer) == 0 || len(first.CertificateDer) > 64<<10 || len(first.Message) != 0 {
		return status.Error(codes.InvalidArgument, "the original node certificate is required first")
	}
	certificate, err := x509.ParseCertificate(first.CertificateDer)
	if err != nil {
		return status.Error(codes.Unauthenticated, "invalid original node certificate")
	}
	roots := x509.NewCertPool()
	roots.AddCert(router.hub.authority.certificate)
	chains, err := certificate.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}})
	if err != nil {
		return status.Error(codes.Unauthenticated, "the original node certificate is not trusted")
	}
	remote, _ := peer.FromContext(stream.Context())
	original := &peer.Peer{AuthInfo: credentials.TLSInfo{State: tls.ConnectionState{PeerCertificates: []*x509.Certificate{certificate}, VerifiedChains: chains}}}
	if remote != nil {
		original.Addr = remote.Addr
	}
	ctx := peer.NewContext(stream.Context(), original)
	record, err := router.hub.authenticate(ctx)
	if err != nil {
		return err
	}
	owner, err := router.owner(record.ServerID)
	if err != nil {
		return status.Error(codes.Unavailable, "gateway membership is unavailable")
	}
	if owner != router.local {
		return status.Error(codes.FailedPrecondition, "node ownership changed before relay attachment")
	}
	adapter := &relayedNodeStream{ServerStream: stream, stream: stream, ctx: ctx}
	return (&nativeNodeConnect{hub: router.hub}).Connect(adapter)
}
