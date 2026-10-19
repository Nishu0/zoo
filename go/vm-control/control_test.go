package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"google.golang.org/grpc"
	cri "k8s.io/cri-api/pkg/apis/runtime/v1"
)

const testSandbox = "11111111-1111-4111-8111-111111111111"
const testPodUID = "22222222-2222-4222-8222-222222222222"
const testNodeUID = "33333333-3333-4333-8333-333333333333"
const testController = "spiffe://zoo.test/vm-control/controller/api"
const testRuntimeID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const testGeneration = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

type fakeInventory struct {
	calls   int
	failure error
}

func (source *fakeInventory) Resolve(context.Context, target) (identity, error) {
	source.calls++
	return identity{ID: strings.Repeat("a", 64), Namespace: "k8s.io"}, source.failure
}

type fakeControl struct{ calls int }

func (control *fakeControl) State(context.Context, identity, controlRequest, string) (vmState, error) {
	control.calls++
	return vmState{State: "paused", Revision: 1}, nil
}

func brokerRequest(method string, operation controlRequest, uid *uint32) *http.Request {
	data, _ := json.Marshal(operation)
	request := httptest.NewRequest(method, "/v1/vm/state", bytes.NewReader(data))
	request.Header.Set("X-Zoo-Controller", testController)
	if uid != nil {
		request = request.WithContext(context.WithValue(request.Context(), peerUIDKey{}, *uid))
	}
	return request
}

func testOperation() controlRequest {
	revision := uint64(0)
	return controlRequest{
		Target:       target{NodeName: "node-a", NodeUID: testNodeUID, SandboxID: testSandbox, PodNamespace: "zoo", PodName: "zoo-sandbox-" + testSandbox, PodUID: testPodUID, NativeID: testRuntimeID, Generation: testGeneration},
		DesiredState: "paused", OperationID: "pause-operation-0001", ExpectedRevision: &revision,
	}
}

func TestBrokerRequiresFrontendPeerAndLocalNodeGeneration(test *testing.T) {
	for _, failure := range []string{"missing-peer", "wrong-peer", "controller", "node", "generation", "inventory", "native-generation", "native-missing"} {
		test.Run(failure, func(test *testing.T) {
			source, control := &fakeInventory{}, &fakeControl{}
			service := &broker{nodeName: "node-a", nodeUID: testNodeUID, frontendUID: 10001, controllers: map[string]bool{testController: true}, inventory: source, control: control}
			operation := testOperation()
			uid := uint32(10001)
			request := brokerRequest(http.MethodPut, operation, &uid)
			switch failure {
			case "missing-peer":
				request = brokerRequest(http.MethodPut, operation, nil)
			case "wrong-peer":
				uid = 10002
				request = brokerRequest(http.MethodPut, operation, &uid)
			case "controller":
				request.Header.Set("X-Zoo-Controller", "spiffe://zoo.test/node/other")
			case "node":
				operation.Target.NodeName = "node-b"
				request = brokerRequest(http.MethodPut, operation, &uid)
			case "generation":
				operation.Target.NodeUID = testPodUID
				request = brokerRequest(http.MethodPut, operation, &uid)
			case "inventory":
				source.failure = errors.New("stale Pod UID")
			case "native-generation":
				operation.Target.NativeID = strings.Repeat("b", 64)
				request = brokerRequest(http.MethodPut, operation, &uid)
			case "native-missing":
				operation.Target.NativeID = ""
				request = brokerRequest(http.MethodPut, operation, &uid)
			}
			response := httptest.NewRecorder()
			service.ServeHTTP(response, request)
			if response.Code == http.StatusOK || control.calls != 0 {
				test.Fatal("unauthorized native control invoked")
			}
		})
	}
}

type fakeCRI struct {
	cri.RuntimeServiceClient
	items []*cri.PodSandbox
}

func (source *fakeCRI) ListPodSandbox(ctx context.Context, request *cri.ListPodSandboxRequest, options ...grpc.CallOption) (*cri.ListPodSandboxResponse, error) {
	return &cri.ListPodSandboxResponse{Items: source.items}, nil
}

func TestCRIResolvesOnlyCurrentLocalKataPodUID(test *testing.T) {
	for _, handler := range []string{"kata-qemu", "kata-clh"} {
		for _, failure := range []string{"", "uid", "runtime", "owner", "state", "duplicate"} {
			test.Run(handler+"-"+failure, func(test *testing.T) {
				pod := &cri.PodSandbox{
					Id: strings.Repeat("a", 64), Metadata: &cri.PodSandboxMetadata{Name: "zoo-sandbox-" + testSandbox, Namespace: "zoo", Uid: testPodUID},
					Labels: map[string]string{"zoo.dev/sandbox": testSandbox, "app.kubernetes.io/managed-by": "zoo"}, RuntimeHandler: handler, State: cri.PodSandboxState_SANDBOX_READY,
				}
				source := &fakeCRI{items: []*cri.PodSandbox{pod}}
				switch failure {
				case "uid":
					pod.Metadata.Uid = testNodeUID
				case "runtime":
					pod.RuntimeHandler = "other-runtime"
				case "owner":
					pod.Labels["zoo.dev/sandbox"] = testNodeUID
				case "state":
					pod.State = cri.PodSandboxState_SANDBOX_NOTREADY
				case "duplicate":
					source.items = append(source.items, pod)
				}
				resolver := &criInventory{client: source, namespace: "zoo", handler: handler}
				resolved, err := resolver.Resolve(context.Background(), testOperation().Target)
				if failure == "" {
					if err != nil || resolved.ID != pod.Id {
						test.Fatalf("resolution: %+v %v", resolved, err)
					}
				} else if err == nil {
					test.Fatal("invalid runtime identity accepted")
				}
			})
		}
	}
}

type fakeTransport func(*http.Request) (*http.Response, error)

func (transport fakeTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return transport(request)
}

func TestDockerInventoryRequiresExactLabelRuntimeAndFullID(test *testing.T) {
	for _, failure := range []string{"", "label", "runtime", "paused", "id"} {
		test.Run(failure, func(test *testing.T) {
			containerID := strings.Repeat("a", 64)
			label, runtimeName, returnedID, paused := testSandbox, "io.containerd.kata.v2", containerID, false
			switch failure {
			case "label":
				label = testNodeUID
			case "runtime":
				runtimeName = "other"
			case "paused":
				paused = true
			case "id":
				returnedID = strings.Repeat("b", 64)
			}
			client := &http.Client{Transport: fakeTransport(func(request *http.Request) (*http.Response, error) {
				if request.URL.Path != "/containers/"+containerID+"/json" {
					test.Fatal("unexpected Docker API route")
				}
				data, _ := json.Marshal(map[string]any{"Id": returnedID, "Name": "/zoo-sandbox-" + testSandbox, "Config": map[string]any{"Labels": map[string]string{"zoo.sandbox": label}}, "HostConfig": map[string]string{"Runtime": runtimeName}, "State": map[string]bool{"Running": true, "Paused": paused}})
				return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(data))}, nil
			})}
			resolver := &dockerInventory{client: client, namespace: "moby"}
			resolved, err := resolver.Resolve(context.Background(), target{SandboxID: testSandbox, ContainerID: containerID})
			if failure == "" {
				if err != nil || resolved.ID != containerID || resolved.Namespace != "moby" {
					test.Fatal("valid Docker identity rejected")
				}
			} else if err == nil {
				test.Fatal("invalid Docker identity accepted")
			}
		})
	}
}

func TestFrontendRequiresVerifiedExactControllerIdentity(test *testing.T) {
	for _, failure := range []string{"", "no-tls", "unverified", "wrong-role", "wrong-node"} {
		test.Run(failure, func(test *testing.T) {
			calls := 0
			identity, _ := url.Parse(testController)
			if failure == "wrong-role" {
				identity, _ = url.Parse("spiffe://zoo.test/node/host")
			}
			certificate := &x509.Certificate{URIs: []*url.URL{identity}}
			operation := testOperation()
			if failure == "wrong-node" {
				operation.Target.NodeName = "node-b"
			}
			request := brokerRequest(http.MethodPut, operation, nil)
			request.TLS = &tls.ConnectionState{PeerCertificates: []*x509.Certificate{certificate}, VerifiedChains: [][]*x509.Certificate{{certificate}}}
			if failure == "no-tls" {
				request.TLS = nil
			}
			if failure == "unverified" {
				request.TLS.VerifiedChains = nil
			}
			client := &http.Client{Transport: fakeTransport(func(request *http.Request) (*http.Response, error) {
				calls++
				if request.Header.Get("X-Zoo-Controller") != testController {
					test.Fatal("controller identity not bound to certificate")
				}
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("{}"))}, nil
			})}
			service := &frontend{client: client, controllers: map[string]bool{testController: true}, nodeName: "node-a", nodeUID: testNodeUID}
			service.ServeHTTP(httptest.NewRecorder(), request)
			if failure == "" && calls != 1 || failure != "" && calls != 0 {
				test.Fatal("frontend authentication boundary failed")
			}
		})
	}
}

func TestHostMemoryRequiresAvailableNotFreeAndValidUnits(test *testing.T) {
	state, err := parseMemory("MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 100 kB\n")
	if err != nil || state.AvailableBytes != 102400 || state.TotalBytes != 1024000 {
		test.Fatal("wrong host memory accounting")
	}
	for _, invalid := range []string{"MemTotal: 1000 kB", "MemTotal: 0 kB\nMemAvailable: 0 kB", "MemTotal: 1 kB\nMemAvailable: 2 kB", "MemTotal: 1 MB\nMemAvailable: 0 kB"} {
		if _, err := parseMemory(invalid); err == nil {
			test.Fatal("invalid memory report accepted")
		}
	}
}

func TestRequestCannotSmugglePathsCommandsOrTrailingJSON(test *testing.T) {
	for _, body := range []string{`{"target":{},"socket_path":"/run/docker.sock"}`, `{"target":{},"command":"quit"}`, `{"target":{}} {"target":{}}`} {
		request := httptest.NewRequest(http.MethodPut, "/v1/vm/state", strings.NewReader(body))
		if err := decodeRequest(httptest.NewRecorder(), request, new(controlRequest)); err == nil {
			test.Fatal("unsupported request accepted")
		}
	}
}
