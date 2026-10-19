package main

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

func identityURI(value string) error {
	identity, err := url.Parse(value)
	if err != nil || identity.Scheme != "spiffe" || identity.Host == "" || identity.Port() != "" ||
		identity.User != nil || identity.RawQuery != "" || identity.Fragment != "" || identity.RawPath != "" {
		return errors.New("canonical SPIFFE identity required")
	}
	if identity.String() != value {
		return errors.New("noncanonical identity")
	}
	return nil
}

func certificateIdentity(certificate *x509.Certificate) (string, error) {
	if certificate == nil || len(certificate.URIs) != 1 {
		return "", errors.New("one certificate URI identity required")
	}
	identity := certificate.URIs[0].String()
	if err := identityURI(identity); err != nil {
		return "", err
	}
	return identity, nil
}

func serverTLS(certificatePath, keyPath, caPath, nodeIdentity string, controllers map[string]bool) (*tls.Config, error) {
	load := func() (*tls.Certificate, error) {
		certificate, err := tls.LoadX509KeyPair(certificatePath, keyPath)
		if err != nil {
			return nil, err
		}
		leaf, err := x509.ParseCertificate(certificate.Certificate[0])
		if err != nil {
			return nil, err
		}
		identity, err := certificateIdentity(leaf)
		if err != nil || identity != nodeIdentity || time.Now().Before(leaf.NotBefore) || !time.Now().Before(leaf.NotAfter) {
			return nil, errors.New("server certificate node identity mismatch")
		}
		return &certificate, nil
	}
	if _, err := load(); err != nil {
		return nil, err
	}
	ca, err := os.ReadFile(caPath)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(ca) {
		return nil, errors.New("controller CA has no certificates")
	}
	return &tls.Config{
		MinVersion: tls.VersionTLS13, GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) { return load() },
		ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots,
		VerifyConnection: func(connection tls.ConnectionState) error {
			if len(connection.VerifiedChains) == 0 || len(connection.PeerCertificates) == 0 {
				return errors.New("verified controller certificate required")
			}
			identity, err := certificateIdentity(connection.PeerCertificates[0])
			if err != nil || !controllers[identity] {
				return errors.New("controller certificate identity denied")
			}
			return nil
		},
	}, nil
}

type frontend struct {
	client      *http.Client
	controllers map[string]bool
	nodeName    string
	nodeUID     string
}

func (service *frontend) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	if request.TLS == nil || len(request.TLS.VerifiedChains) == 0 || len(request.TLS.PeerCertificates) == 0 {
		http.Error(writer, "verified controller certificate required", http.StatusForbidden)
		return
	}
	controller, err := certificateIdentity(request.TLS.PeerCertificates[0])
	if err != nil || !service.controllers[controller] {
		http.Error(writer, "controller denied", http.StatusForbidden)
		return
	}
	allowed := request.URL.Path == "/v1/vm/state" && (request.Method == http.MethodPost || request.Method == http.MethodPut) ||
		request.URL.Path == "/v1/host/memory" && request.Method == http.MethodGet
	if !allowed || request.URL.RawQuery != "" {
		http.NotFound(writer, request)
		return
	}
	data, err := io.ReadAll(http.MaxBytesReader(writer, request.Body, 8192))
	if err != nil {
		http.Error(writer, "request too large", http.StatusBadRequest)
		return
	}
	if request.URL.Path == "/v1/vm/state" {
		var operation controlRequest
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&operation); err != nil || decoder.Decode(new(any)) != io.EOF {
			http.Error(writer, "invalid request", http.StatusBadRequest)
			return
		}
		if operation.Target.NodeName != service.nodeName || operation.Target.NodeUID != service.nodeUID {
			http.Error(writer, "certificate node identity and target differ", http.StatusConflict)
			return
		}
	}
	proxy, err := http.NewRequestWithContext(request.Context(), request.Method, "http://broker"+request.URL.Path, bytes.NewReader(data))
	if err != nil {
		http.Error(writer, "invalid request", http.StatusBadRequest)
		return
	}
	proxy.Header.Set("Content-Type", "application/json")
	proxy.Header.Set("X-Zoo-Controller", controller)
	response, err := service.client.Do(proxy)
	if err != nil {
		http.Error(writer, "local broker unavailable", http.StatusServiceUnavailable)
		return
	}
	defer response.Body.Close()
	writer.Header().Set("Content-Type", response.Header.Get("Content-Type"))
	writer.WriteHeader(response.StatusCode)
	io.Copy(writer, io.LimitReader(response.Body, 8192))
}

func controllerIdentities(value, trustDomain string) (map[string]bool, error) {
	controllers := map[string]bool{}
	for _, identity := range strings.Split(value, ",") {
		if err := identityURI(identity); err != nil {
			return nil, err
		}
		if !strings.HasPrefix(identity, "spiffe://"+trustDomain+"/vm-control/controller/") ||
			strings.TrimPrefix(identity, "spiffe://"+trustDomain+"/vm-control/controller/") == "" {
			return nil, errors.New("explicit VM controller identities required")
		}
		controllers[identity] = true
	}
	return controllers, nil
}
