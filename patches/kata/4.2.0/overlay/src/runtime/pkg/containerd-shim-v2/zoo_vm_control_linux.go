package containerdshim

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/containerd/containerd/errdefs"
	vc "github.com/kata-containers/kata-containers/src/runtime/virtcontainers"
	"golang.org/x/sys/unix"
)

type zooVMBackend interface {
	ZooVMGuestReady() bool
	ZooVMState(context.Context) (vc.ZooVMState, error)
	ZooSetVMState(context.Context, string, string, uint64, string) (vc.ZooVMState, error)
	ZooResumeVMForTeardown(context.Context) error
}

func (service *service) zooRequireRunningVM() error {
	backend, supported := service.sandbox.(zooVMBackend)
	if !supported || !backend.ZooVMGuestReady() {
		return errdefs.ToGRPCf(errdefs.ErrFailedPrecondition, "Zoo VM is paused, unhealthy, or unsupported")
	}
	return nil
}

type zooRootPeerKey struct{}

func (service *service) zooVMHandler(writer http.ResponseWriter, request *http.Request) {
	if root, _ := request.Context().Value(zooRootPeerKey{}).(bool); !root {
		http.Error(writer, "root broker peer required", http.StatusForbidden)
		return
	}
	if request.URL.Path != "/zoo/v1/vm" || request.URL.RawQuery != "" {
		http.NotFound(writer, request)
		return
	}
	if request.Method != http.MethodGet && request.Method != http.MethodPut {
		http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	backend, supported := service.sandbox.(zooVMBackend)
	if !supported || service.sandbox == nil {
		http.Error(writer, "pinned Zoo VM extension is unavailable", http.StatusServiceUnavailable)
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 10*time.Second)
	defer cancel()
	var state vc.ZooVMState
	var err error
	if request.Method == http.MethodGet {
		state, err = backend.ZooVMState(ctx)
	} else {
		var operation struct {
			SandboxID          string  `json:"sandbox_id"`
			DesiredState       string  `json:"desired_state"`
			OperationID        string  `json:"operation_id"`
			ExpectedRevision   *uint64 `json:"expected_revision"`
			ExpectedGeneration string  `json:"expected_generation"`
		}
		decoder := json.NewDecoder(http.MaxBytesReader(writer, request.Body, 4096))
		decoder.DisallowUnknownFields()
		if decodeErr := decoder.Decode(&operation); decodeErr != nil || decoder.Decode(new(any)) != io.EOF {
			http.Error(writer, "invalid request", http.StatusBadRequest)
			return
		}
		if operation.SandboxID != service.sandbox.ID() || operation.ExpectedRevision == nil || len(operation.ExpectedGeneration) != 64 {
			http.Error(writer, "sandbox identity, generation and revision required", http.StatusConflict)
			return
		}
		state, err = backend.ZooSetVMState(ctx, operation.DesiredState, operation.OperationID, *operation.ExpectedRevision, operation.ExpectedGeneration)
	}
	if err != nil {
		http.Error(writer, err.Error(), http.StatusConflict)
		return
	}
	state.Namespace = service.namespace
	writer.Header().Set("Content-Type", "application/json")
	json.NewEncoder(writer).Encode(state)
}

func (service *service) startZooVMControlServer(ctx context.Context) error {
	if os.Geteuid() != 0 {
		return errors.New("Zoo VM extension requires the rootful Go QEMU or CLH runtime")
	}
	backend, supported := service.sandbox.(zooVMBackend)
	if !supported {
		return errors.New("pinned Zoo VM extension is unavailable")
	}
	verification, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := backend.ZooVMState(verification); err != nil {
		return err
	}
	path := filepath.Join(GetSandboxesStoragePath(), service.id, "zoo-vm-control.sock")
	listener, err := net.Listen("unix", path)
	if err != nil {
		return err
	}
	if err := os.Chmod(path, 0600); err != nil {
		listener.Close()
		return err
	}
	server := &http.Server{
		Handler: http.HandlerFunc(service.zooVMHandler), ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout: 5 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 15 * time.Second,
		MaxHeaderBytes: 4096,
		ConnContext: func(parent context.Context, connection net.Conn) context.Context {
			peer, supported := connection.(*net.UnixConn)
			if !supported {
				return parent
			}
			raw, err := peer.SyscallConn()
			if err != nil {
				return parent
			}
			var credentials *unix.Ucred
			var credentialErr error
			if err := raw.Control(func(fd uintptr) {
				credentials, credentialErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
			}); err != nil {
				return parent
			}
			return context.WithValue(parent, zooRootPeerKey{}, credentialErr == nil && credentials != nil && credentials.Uid == 0)
		},
	}
	go func() { <-ctx.Done(); server.Close() }()
	go func() {
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			shimLog.WithError(err).Error("Zoo VM control server stopped")
		}
	}()
	return nil
}
