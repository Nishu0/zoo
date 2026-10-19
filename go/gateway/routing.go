package main

import (
	"context"
	"crypto/hmac"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"slices"
	"strconv"
	"sync"
	"time"

	"github.com/coder/websocket"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

type replicaRouter struct {
	peers     *peerSet
	settings  config
	roots     *x509.CertPool
	transport *http.Transport
	mu        sync.Mutex
	channels  map[string]*grpc.ClientConn
	closed    bool
}

func newReplicaRouter(peers *peerSet, authority *nodeAuthority, settings config) *replicaRouter {
	roots := x509.NewCertPool()
	roots.AddCert(authority.certificate)
	return &replicaRouter{
		peers: peers, settings: settings, roots: roots, channels: make(map[string]*grpc.ClientConn),
		transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots},
			DialContext:         (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
			TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 10 * time.Second, IdleConnTimeout: 90 * time.Second},
	}
}

func (router *replicaRouter) requireOwner(ctx context.Context, resourceID string) error {
	if resourceID == "" {
		return status.Error(codes.InvalidArgument, "resource ID is required")
	}
	owner, err := router.peers.Owner(resourceID)
	if err != nil {
		return status.Error(codes.Unavailable, "gateway membership is unavailable")
	}
	if owner != router.peers.LocalIP() {
		grpc.SetTrailer(ctx, metadata.Pairs("zoo-owner", owner))
		return status.Error(codes.FailedPrecondition, "gateway ownership changed")
	}
	return nil
}

func (router *replicaRouter) client(owner string) (gatewaypb.GatewayClient, error) {
	members, err := router.peers.Members()
	if err != nil || !slices.Contains(members, owner) {
		return nil, errors.New("gateway owner is not in the current peer set")
	}
	router.mu.Lock()
	defer router.mu.Unlock()
	if router.closed {
		return nil, errors.New("gateway routing is shutting down")
	}
	channel := router.channels[owner]
	if channel == nil {
		channel, err = grpc.NewClient(net.JoinHostPort(owner, strconv.Itoa(router.settings.grpcPort)),
			grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12, RootCAs: router.roots})),
			grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(maxMessageBytes), grpc.MaxCallSendMsgSize(maxMessageBytes)))
		if err != nil {
			return nil, err
		}
		router.channels[owner] = channel
	}
	return gatewaypb.NewGatewayClient(channel), nil
}

func (router *replicaRouter) close() {
	router.mu.Lock()
	router.closed = true
	for address, channel := range router.channels {
		channel.Close()
		delete(router.channels, address)
	}
	router.mu.Unlock()
	router.transport.CloseIdleConnections()
}

func (router *replicaRouter) closeMoved(service *gatewayServer) {
	if service.screens != nil {
		service.screens.closeMoved()
	}
	service.guests.mu.Lock()
	guests := make([]*guestConnection, 0)
	for sandboxID, guest := range service.guests.guests {
		owner, err := router.peers.Owner(sandboxID)
		if err == nil && owner != router.peers.LocalIP() {
			guests = append(guests, guest)
		}
	}
	service.guests.mu.Unlock()
	for _, guest := range guests {
		guest.disconnect(status.Error(codes.Unavailable, "guest ownership changed"))
	}
	service.nodes.mu.Lock()
	sessions := make([]*nodeSession, 0)
	for serverID, session := range service.nodes.sessions {
		owner, err := router.peers.Owner(serverID)
		if err == nil && owner != router.peers.LocalIP() {
			sessions = append(sessions, session)
		}
	}
	service.nodes.mu.Unlock()
	for _, session := range sessions {
		session.close()
	}
	members, err := router.peers.Members()
	if err == nil {
		router.mu.Lock()
		for owner, channel := range router.channels {
			if !slices.Contains(members, owner) {
				channel.Close()
				delete(router.channels, owner)
			}
		}
		router.mu.Unlock()
	}
}

func (router *replicaRouter) routeGuest(writer http.ResponseWriter, request *http.Request, sandboxID string) bool {
	owner, err := router.peers.Owner(sandboxID)
	if err != nil {
		http.Error(writer, "gateway membership is unavailable", http.StatusServiceUnavailable)
		return true
	}
	pinned := request.URL.Query().Get("owner")
	if owner == router.peers.LocalIP() && (pinned == "" || pinned == owner) {
		return false
	}
	if pinned != owner {
		connection, err := websocket.Accept(writer, request, nil)
		if err == nil {
			connection.Close(websocket.StatusCode(4001), "owner="+owner)
			connection.CloseNow()
		}
		return true
	}
	endpoint := &url.URL{Scheme: "https", Host: net.JoinHostPort(owner, strconv.Itoa(router.settings.peerHTTPPort))}
	proxy := httputil.NewSingleHostReverseProxy(endpoint)
	proxy.Transport = router.transport
	director := proxy.Director
	proxy.Director = func(forward *http.Request) {
		director(forward)
		forward.Header.Set("X-Zoo-Gateway", router.settings.token("zoo-gateway"))
	}
	proxy.ErrorHandler = func(writer http.ResponseWriter, request *http.Request, failure error) {
		http.Error(writer, "guest owner is unavailable", http.StatusServiceUnavailable)
	}
	proxy.ServeHTTP(writer, request)
	return true
}

func (service *gatewayServer) serveRelayedGuest(writer http.ResponseWriter, request *http.Request) {
	keys := request.Header.Values("X-Zoo-Gateway")
	if len(keys) != 1 || !hmac.Equal([]byte(keys[0]), []byte(service.settings.token("zoo-gateway"))) {
		http.Error(writer, "invalid gateway authentication", http.StatusUnauthorized)
		return
	}
	owner, err := service.routes.peers.Owner(request.Header.Get("X-Zoo-Sandbox"))
	if err != nil || owner != service.routes.peers.LocalIP() {
		http.Error(writer, "guest ownership changed", http.StatusConflict)
		return
	}
	service.serveGuest(writer, request)
}

func (service *gatewayServer) RelayNode(stream grpc.BidiStreamingServer[gatewaypb.NodeRelayFrame, gatewaypb.Bytes]) error {
	return service.nodeLinks.RelayNode(stream)
}

func peerListenerAddress(port int) string {
	return fmt.Sprintf(":%d", port)
}
