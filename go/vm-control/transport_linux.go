package main

import (
	"context"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

type peerUIDKey struct{}

func peerUID(connection net.Conn) (uint32, error) {
	peer, supported := connection.(*net.UnixConn)
	if !supported {
		return 0, errors.New("Unix peer required")
	}
	raw, err := peer.SyscallConn()
	if err != nil {
		return 0, err
	}
	var credentials *unix.Ucred
	var credentialErr error
	if err := raw.Control(func(fd uintptr) {
		credentials, credentialErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil {
		return 0, err
	}
	if credentialErr != nil {
		return 0, credentialErr
	}
	if credentials == nil {
		return 0, errors.New("missing Unix credentials")
	}
	return credentials.Uid, nil
}

func unixClient(socket string, rootPeer bool) *http.Client {
	return &http.Client{
		Timeout:       12 * time.Second,
		CheckRedirect: func(request *http.Request, previous []*http.Request) error { return http.ErrUseLastResponse },
		Transport: &http.Transport{
			DisableKeepAlives: true,
			DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
				connection, err := (&net.Dialer{}).DialContext(ctx, "unix", socket)
				if err != nil {
					return nil, err
				}
				if rootPeer {
					uid, err := peerUID(connection)
					if err != nil || uid != 0 {
						connection.Close()
						return nil, errors.New("runtime peer must be root")
					}
				}
				return connection, nil
			},
		},
	}
}

func shimSocket(root, id string) (string, error) {
	if !runtimeIDPattern.MatchString(id) || !filepath.IsAbs(root) || filepath.Clean(root) != root {
		return "", errors.New("invalid trusted runtime identity or root")
	}
	for _, path := range []string{root, filepath.Join(root, id)} {
		info, err := os.Lstat(path)
		if err != nil {
			return "", err
		}
		stat, supported := info.Sys().(*syscall.Stat_t)
		if !supported {
			return "", errors.New("runtime directory ownership is unavailable")
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 || stat.Uid != 0 {
			return "", errors.New("runtime directory must be root-owned and not writable by others")
		}
	}
	path := filepath.Join(root, id, "zoo-vm-control.sock")
	info, err := os.Lstat(path)
	if err != nil {
		return "", err
	}
	if info.Mode()&os.ModeSocket == 0 || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm() != 0600 {
		return "", errors.New("private shim control socket is required")
	}
	return path, nil
}

func newHTTPServer(address string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr: address, Handler: handler, ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout: 5 * time.Second, WriteTimeout: 20 * time.Second, IdleTimeout: 15 * time.Second,
		MaxHeaderBytes: 8192,
	}
}
