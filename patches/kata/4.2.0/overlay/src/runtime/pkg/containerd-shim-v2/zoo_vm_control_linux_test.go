package containerdshim

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	vc "github.com/kata-containers/kata-containers/src/runtime/virtcontainers"
)

type zooHandlerSandbox struct {
	vc.VCSandbox
	queries   int
	mutations int
	ready     bool
}

func (sandbox *zooHandlerSandbox) ID() string                                   { return strings.Repeat("a", 64) }
func (sandbox *zooHandlerSandbox) ZooVMGuestReady() bool                        { return sandbox.ready }
func (sandbox *zooHandlerSandbox) ZooResumeVMForTeardown(context.Context) error { return nil }
func (sandbox *zooHandlerSandbox) ZooVMState(context.Context) (vc.ZooVMState, error) {
	sandbox.queries++
	return vc.ZooVMState{Protocol: vc.ZooVMProtocol, SandboxID: sandbox.ID(), State: "paused"}, nil
}
func (sandbox *zooHandlerSandbox) ZooSetVMState(context.Context, string, string, uint64, string) (vc.ZooVMState, error) {
	sandbox.mutations++
	return vc.ZooVMState{SandboxID: sandbox.ID(), State: "paused"}, nil
}

func TestZooPrivateHandlerRequiresKernelRootPeer(test *testing.T) {
	sandbox := &zooHandlerSandbox{}
	service := &service{sandbox: sandbox, namespace: "k8s.io"}
	request := httptest.NewRequest(http.MethodGet, "/zoo/v1/vm", nil)
	request.Header.Set("X-Root", "true")
	response := httptest.NewRecorder()
	service.zooVMHandler(response, request)
	if response.Code != http.StatusForbidden || sandbox.queries != 0 {
		test.Fatal("header bypassed Unix peer authentication")
	}
}

func TestZooPrivateHandlerRejectsUnboundMutationAndUnknownFields(test *testing.T) {
	for _, body := range []string{
		`{"sandbox_id":"other","desired_state":"paused","operation_id":"pause-operation-0001","expected_revision":0}`,
		`{"sandbox_id":"` + strings.Repeat("a", 64) + `","desired_state":"paused","operation_id":"pause-operation-0001"}`,
		`{"sandbox_id":"` + strings.Repeat("a", 64) + `","command":"quit"}`,
		`{"sandbox_id":"` + strings.Repeat("a", 64) + `","desired_state":"paused","operation_id":"pause-operation-0001","expected_revision":0}`,
	} {
		sandbox := &zooHandlerSandbox{}
		service := &service{sandbox: sandbox, namespace: "k8s.io"}
		request := httptest.NewRequest(http.MethodPut, "/zoo/v1/vm", strings.NewReader(body))
		request = request.WithContext(context.WithValue(request.Context(), zooRootPeerKey{}, true))
		response := httptest.NewRecorder()
		service.zooVMHandler(response, request)
		if response.Code == http.StatusOK || sandbox.mutations != 0 {
			test.Fatal("unbound mutation accepted")
		}
	}
}

func TestZooPausedGuestGuardDoesNotBlockNativeStateQuery(test *testing.T) {
	sandbox := &zooHandlerSandbox{ready: false}
	service := &service{sandbox: sandbox, namespace: "k8s.io"}
	if service.zooRequireRunningVM() == nil {
		test.Fatal("paused guest-dependent operation accepted")
	}
	request := httptest.NewRequest(http.MethodGet, "/zoo/v1/vm", nil)
	request = request.WithContext(context.WithValue(request.Context(), zooRootPeerKey{}, true))
	response := httptest.NewRecorder()
	service.zooVMHandler(response, request)
	if response.Code != http.StatusOK || sandbox.queries != 1 {
		test.Fatal("native query blocked by guest pause")
	}
}
