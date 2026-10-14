package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	mode := flag.String("mode", "", "broker, frontend, enroll, csr, install-certificate or credentials-check")
	nodeName := flag.String("node-name", os.Getenv("NODE_NAME"), "immutable local node name")
	nodeUID := flag.String("node-uid", "", "immutable local node generation UUID")
	trustDomain := flag.String("trust-domain", "", "installation SPIFFE trust domain")
	controllersValue := flag.String("controllers", "", "comma-separated permitted controller certificate URI identities")
	brokerSocket := flag.String("broker-socket", "/run/zoo-vm-control/broker.sock", "private local broker socket")
	frontendUID := flag.Uint("frontend-uid", 0, "dedicated non-root frontend UID")
	backend := flag.String("runtime", "", "docker or kube")
	runtimeSocket := flag.String("runtime-socket", "", "explicit local Docker API or CRI socket")
	namespace := flag.String("namespace", "", "installation Pod namespace or Docker containerd namespace")
	handler := flag.String("runtime-handler", "", "installed Go QEMU/CLH CRI handler")
	shimRoot := flag.String("shim-root", "/run/vc/sbs", "root-owned Go Kata sandbox state directory")
	shimManifest := flag.String("shim-manifest", "/opt/kata/share/zoo-vm-control/manifest.json", "required installed patched shim manifest")
	shimBinary := flag.String("shim-binary", "/opt/kata/bin/containerd-shim-kata-v2", "required installed patched shim binary")
	memoryPath := flag.String("host-memory", "/host/proc/meminfo", "read-only host MemAvailable report")
	listen := flag.String("listen", ":9444", "frontend HTTPS listener")
	certificate := flag.String("tls-cert", "", "node server certificate")
	key := flag.String("tls-key", "", "node server private key")
	ca := flag.String("controller-ca", "", "dedicated controller certificate authority")
	credentialDirectory := flag.String("credential-dir", "", "private node-local generated credential directory")
	enrollURL := flag.String("enroll-url", "", "explicit HTTPS Pod-bound certificate issuer")
	enrollCA := flag.String("enroll-ca", "", "public issuer HTTPS certificate authority")
	nodeCA := flag.String("node-ca", "", "pinned public node certificate authority")
	enrollToken := flag.String("enroll-token", "", "projected Pod-bound enrollment audience token")
	serverName := flag.String("server-name", "", "installation frontend DNS certificate SAN")
	podName := flag.String("pod-name", "", "own controller Pod name")
	podUID := flag.String("pod-uid", "", "own controller Pod generation")
	flag.Parse()
	if flag.NArg() != 0 || !nodePattern.MatchString(*nodeName) || !nodePattern.MatchString(*trustDomain) {
		return errors.New("explicit node name, generation UUID, and trust domain required")
	}
	provision := enrollment{url: *enrollURL, issuerCA: *enrollCA, nodeCA: *nodeCA, tokenPath: *enrollToken,
		directory: *credentialDirectory, serverName: *serverName, nodeName: *nodeName, podName: *podName,
		podUID: *podUID, trustDomain: *trustDomain, frontendUID: *frontendUID, backend: *backend, nodeUID: *nodeUID}
	if *mode == "csr" || *mode == "install-certificate" {
		return provision.bootstrap(*mode, *certificate)
	}
	if *mode == "enroll" {
		if os.Geteuid() != 0 {
			return errors.New("credential provisioning requires root")
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_, err := provision.provision(ctx)
		return err
	}
	if *credentialDirectory != "" {
		credential, err := readNodeCredential(*credentialDirectory, *nodeName)
		if err != nil {
			return err
		}
		if *nodeUID != "" && *nodeUID != credential.NodeUID {
			return errors.New("node generation binding differs")
		}
		*nodeUID = credential.NodeUID
		provision.nodeUID = credential.NodeUID
		*certificate, *key = filepath.Join(*credentialDirectory, "node.pem"), filepath.Join(*credentialDirectory, "node-key.pem")
	}
	if !uuidPattern.MatchString(*nodeUID) {
		return errors.New("immutable node generation UUID required")
	}
	controllers, err := controllerIdentities(*controllersValue, *trustDomain)
	if err != nil {
		return err
	}
	if *mode == "credentials-check" {
		nodeIdentity := "spiffe://" + *trustDomain + "/vm-control/node/" + *nodeName + "/" + *nodeUID
		if _, err := serverTLS(*certificate, *key, *ca, nodeIdentity, controllers); err != nil {
			return err
		}
		client := unixClient(*brokerSocket, true)
		defer client.CloseIdleConnections()
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://broker/v1/host/memory", nil)
		if err != nil {
			return err
		}
		for identity := range controllers {
			request.Header.Set("X-Zoo-Controller", identity)
			break
		}
		response, err := client.Do(request)
		if err != nil {
			return errors.New("local root broker unavailable")
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			return errors.New("local root broker not ready")
		}
		return nil
	}
	if !filepath.IsAbs(*brokerSocket) || filepath.Clean(*brokerSocket) != *brokerSocket {
		return errors.New("absolute private broker socket required")
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	var server *http.Server
	var listener net.Listener
	switch *mode {
	case "frontend":
		if os.Geteuid() == 0 {
			return errors.New("frontend must run as a dedicated non-root user")
		}
		nodeIdentity := "spiffe://" + *trustDomain + "/vm-control/node/" + *nodeName + "/" + *nodeUID
		configuration, err := serverTLS(*certificate, *key, *ca, nodeIdentity, controllers)
		if err != nil {
			return err
		}
		client := unixClient(*brokerSocket, true)
		defer client.CloseIdleConnections()
		server = newHTTPServer(*listen, &frontend{client: client, controllers: controllers, nodeName: *nodeName, nodeUID: *nodeUID})
		server.TLSConfig = configuration
		listener, err = net.Listen("tcp", *listen)
		if err != nil {
			return err
		}
	case "broker":
		if os.Geteuid() != 0 || *frontendUID == 0 || uint64(*frontendUID) > uint64(^uint32(0)) {
			return errors.New("root broker and explicit non-root frontend UID required")
		}
		if err := verifyInstallation(*shimManifest, *shimBinary); err != nil {
			return err
		}
		if *enrollURL != "" {
			go provision.renew(ctx)
		}
		if !filepath.IsAbs(*runtimeSocket) || filepath.Clean(*runtimeSocket) != *runtimeSocket || *namespace == "" {
			return errors.New("explicit local runtime socket and installation namespace required")
		}
		var source inventory
		switch *backend {
		case "kube":
			if *handler == "" {
				return errors.New("explicit pinned Go QEMU/CLH runtime handler required")
			}
			cri, connection, err := connectCRI(*runtimeSocket, *namespace, *handler)
			if err != nil {
				return err
			}
			defer connection.Close()
			source = cri
		case "docker":
			client := unixClient(*runtimeSocket, true)
			defer client.CloseIdleConnections()
			source = &dockerInventory{client: client, namespace: *namespace}
		default:
			return errors.New("runtime must be docker or kube")
		}
		service := &broker{
			nodeName: *nodeName, nodeUID: *nodeUID, frontendUID: uint32(*frontendUID), controllers: controllers,
			inventory: source, control: &shimControl{root: *shimRoot}, memoryPath: *memoryPath,
		}
		parent := filepath.Dir(*brokerSocket)
		if err := os.MkdirAll(parent, 0711); err != nil {
			return err
		}
		info, err := os.Lstat(parent)
		if err != nil {
			return err
		}
		ownership, supported := info.Sys().(*syscall.Stat_t)
		if !supported || !info.IsDir() || info.Mode().Perm()&0022 != 0 || ownership.Uid != 0 {
			return errors.New("broker directory must be root-owned and not writable by others")
		}
		if info, err := os.Lstat(*brokerSocket); err == nil {
			owner, valid := info.Sys().(*syscall.Stat_t)
			if !valid || info.Mode()&os.ModeSocket == 0 || owner.Uid != uint32(*frontendUID) {
				return errors.New("unsafe existing broker socket")
			}
			active, err := net.DialTimeout("unix", *brokerSocket, time.Second)
			if err == nil {
				active.Close()
				return errors.New("broker socket already active")
			}
			if !errors.Is(err, syscall.ECONNREFUSED) && !errors.Is(err, syscall.ENOENT) {
				return err
			}
			if err := os.Remove(*brokerSocket); err != nil {
				return err
			}
		} else if !os.IsNotExist(err) {
			return err
		}
		listener, err = net.Listen("unix", *brokerSocket)
		if err != nil {
			return err
		}
		defer os.Remove(*brokerSocket)
		if err := os.Chown(*brokerSocket, int(*frontendUID), -1); err != nil {
			listener.Close()
			return err
		}
		if err := os.Chmod(*brokerSocket, 0600); err != nil {
			listener.Close()
			return err
		}
		server = newHTTPServer("", service)
		server.ConnContext = func(parent context.Context, connection net.Conn) context.Context {
			uid, err := peerUID(connection)
			if err != nil {
				return parent
			}
			return context.WithValue(parent, peerUIDKey{}, uid)
		}
	default:
		return errors.New("mode must be broker or frontend")
	}
	defer listener.Close()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		server.Shutdown(shutdown)
	}()
	log.Printf("vm-control mode=%s node=%s generation=%s", *mode, *nodeName, *nodeUID)
	if *mode == "frontend" {
		err = server.ServeTLS(listener, "", "")
	} else {
		err = server.Serve(listener)
	}
	if err != nil && err != http.ErrServerClosed {
		return fmt.Errorf("control listener failed: %w", err)
	}
	return nil
}
