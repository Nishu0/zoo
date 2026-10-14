package main

import (
	"context"
	"crypto/tls"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

const maxMessageBytes = 128 << 20

type gatewayServer struct {
	gatewaypb.UnimplementedGatewayServer
	settings  config
	guests    *guestHub
	nodes     *nodeHub
	routes    *replicaRouter
	nodeLinks *nodeLinkRouter
	screens   *screenService
}

func newGatewayServer(settings config) *gatewayServer {
	return &gatewayServer{settings: settings, guests: newGuestHub()}
}

func (service *gatewayServer) NodeTunnel(stream grpc.BidiStreamingServer[gatewaypb.Bytes, gatewaypb.Bytes]) error {
	identities := metadata.ValueFromIncomingContext(stream.Context(), "server_id")
	if len(identities) != 1 {
		return status.Error(codes.InvalidArgument, "server_id metadata is required once")
	}
	if err := service.routes.requireOwner(stream.Context(), identities[0]); err != nil {
		return err
	}
	return service.nodes.NodeTunnel(stream)
}

func (service *gatewayServer) NodeStatus(ctx context.Context, request *gatewaypb.NodeStatusRequest) (*gatewaypb.NodeStatusReply, error) {
	if err := service.routes.requireOwner(ctx, request.ServerId); err != nil {
		return nil, err
	}
	return service.nodes.NodeStatus(ctx, request)
}

func run(ctx context.Context, settings config) error {
	startup, cancelStartup := context.WithTimeout(ctx, 30*time.Second)
	pool, err := pgxpool.New(startup, settings.databaseURL)
	if err != nil {
		cancelStartup()
		return err
	}
	defer pool.Close()
	if err := pool.Ping(startup); err != nil {
		cancelStartup()
		return fmt.Errorf("gateway database is unavailable: %w", err)
	}
	authority, err := loadNodeAuthority(startup, pool, settings)
	cancelStartup()
	if err != nil {
		return fmt.Errorf("cannot load node CA: %w", err)
	}
	version, err := os.ReadFile(filepath.Join(settings.nodeDist, "VERSION"))
	if err != nil || strings.TrimSpace(string(version)) == "" {
		return fmt.Errorf("node build VERSION is required in %s", settings.nodeDist)
	}
	service := newGatewayServer(settings)
	peers, err := newPeerSet(ctx, settings.peerName, settings.peerIP, nil)
	if err != nil {
		return fmt.Errorf("cannot discover gateway peers: %w", err)
	}
	defer peers.Close()
	service.routes = newReplicaRouter(peers, authority, settings)
	defer service.routes.close()
	service.nodes, err = newNodeHub(newPostgresNodeStore(pool), authority, nodeHubOptions{
		Version: strings.TrimSpace(string(version)), DistDir: settings.nodeDist,
		Endpoints: []string{net.JoinHostPort(settings.nodeHost, strconv.Itoa(settings.nodePort))},
	})
	if err != nil {
		return err
	}
	service.screens = newScreenService(service, pool)
	defer service.screens.close()
	nodeTLS, err := authority.serverTLSConfig([]string{settings.nodeHost, peers.LocalIP(), "localhost", "127.0.0.1"})
	if err != nil {
		return err
	}
	service.nodeLinks = &nodeLinkRouter{hub: service.nodes, local: peers.LocalIP(), owner: peers.Owner,
		client: service.routes.client, key: settings.token("zoo-gateway")}
	peers.OnOwnerChanges(func() { service.routes.closeMoved(service) })
	internalTLS := nodeTLS.Clone()
	internalTLS.ClientAuth = tls.NoClientCert
	listener, err := net.Listen("tcp", fmt.Sprintf(":%d", settings.grpcPort))
	if err != nil {
		return err
	}
	defer listener.Close()
	nodeListener, err := net.Listen("tcp", fmt.Sprintf(":%d", settings.nodeListenPort))
	if err != nil {
		return err
	}
	defer nodeListener.Close()
	rpcServer := grpc.NewServer(
		grpc.Creds(credentials.NewTLS(internalTLS)),
		grpc.UnaryInterceptor(settings.unaryAuth), grpc.StreamInterceptor(settings.streamAuth),
		grpc.MaxRecvMsgSize(maxMessageBytes), grpc.MaxSendMsgSize(maxMessageBytes),
	)
	gatewaypb.RegisterGatewayServer(rpcServer, service)
	nodeServer := grpc.NewServer(grpc.Creds(credentials.NewTLS(nodeTLS)), grpc.MaxRecvMsgSize(maxMessageBytes), grpc.MaxSendMsgSize(maxMessageBytes))
	nodepb.RegisterNodeServer(nodeServer, service.nodeLinks)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /guest/connect", service.serveGuest)
	mux.HandleFunc("GET /healthz", func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.Write([]byte("{\"status\":\"ok\"}\n"))
	})
	mux.HandleFunc("GET /readyz", func(writer http.ResponseWriter, request *http.Request) {
		probe, cancel := context.WithTimeout(request.Context(), 3*time.Second)
		defer cancel()
		writer.Header().Set("Content-Type", "application/json")
		if err := pool.Ping(probe); err != nil {
			writer.WriteHeader(http.StatusServiceUnavailable)
			writer.Write([]byte("{\"status\":\"unavailable\",\"check\":\"database\"}\n"))
			return
		}
		if _, err := peers.Owner("readiness"); err != nil {
			writer.WriteHeader(http.StatusServiceUnavailable)
			writer.Write([]byte("{\"status\":\"unavailable\",\"check\":\"gateway_peers\"}\n"))
			return
		}
		writer.Write([]byte("{\"status\":\"ok\"}\n"))
	})
	httpServer := &http.Server{
		Addr: fmt.Sprintf(":%d", settings.httpPort), Handler: mux,
		ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second,
	}
	peerMux := http.NewServeMux()
	peerMux.HandleFunc("GET /guest/connect", service.serveRelayedGuest)
	peerServer := &http.Server{Addr: peerListenerAddress(settings.peerHTTPPort), Handler: peerMux,
		TLSConfig: internalTLS, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	failures := make(chan error, 4)
	go func() { failures <- rpcServer.Serve(listener) }()
	go func() { failures <- nodeServer.Serve(nodeListener) }()
	go func() { failures <- httpServer.ListenAndServe() }()
	go func() { failures <- peerServer.ListenAndServeTLS("", "") }()
	select {
	case <-ctx.Done():
		err = nil
	case err = <-failures:
		if errors.Is(err, http.ErrServerClosed) || errors.Is(err, grpc.ErrServerStopped) {
			err = nil
		}
	}
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	service.screens.close()
	service.guests.close()
	service.nodes.close()
	if failure := peerServer.Shutdown(shutdown); failure != nil {
		slog.Error("peer HTTP shutdown failed", "error", failure)
	}
	if failure := httpServer.Shutdown(shutdown); failure != nil {
		slog.Error("HTTP shutdown failed", "error", failure)
	}
	finished := make(chan struct{})
	go func() { rpcServer.GracefulStop(); nodeServer.GracefulStop(); close(finished) }()
	select {
	case <-finished:
	case <-shutdown.Done():
		rpcServer.Stop()
		nodeServer.Stop()
	}
	return err
}

func main() {
	probeURL := flag.String("healthcheck", "", "check the gateway readiness endpoint and exit")
	flag.Parse()
	if *probeURL != "" {
		client := &http.Client{Timeout: 3 * time.Second}
		response, err := client.Get(*probeURL)
		if err != nil {
			os.Exit(1)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		return
	}
	settings, err := readConfig()
	if err != nil {
		slog.Error("invalid gateway configuration", "error", err)
		os.Exit(1)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	if err := run(ctx, settings); err != nil {
		slog.Error("gateway stopped", "error", err)
		os.Exit(1)
	}
}
