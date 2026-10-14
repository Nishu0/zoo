package virtcontainers

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"testing"

	chclient "github.com/kata-containers/kata-containers/src/runtime/virtcontainers/pkg/cloud-hypervisor/client"
)

type zooFakeCLHClient struct {
	clhClient
	state string
	err   error
}

func (client *zooFakeCLHClient) VmInfoGet(context.Context) (chclient.VmInfo, *http.Response, error) {
	return chclient.VmInfo{State: client.state}, nil, client.err
}

func (client *zooFakeCLHClient) VmPausePut(context.Context) (*http.Response, error) {
	client.state = clhStatePaused
	return nil, client.err
}

func (client *zooFakeCLHClient) ResumeVM(context.Context) (*http.Response, error) {
	client.state = clhStateRunning
	return nil, client.err
}

func TestZooCLHLifecycleUsesNativePauseAndResume(test *testing.T) {
	client := &zooFakeCLHClient{state: clhStateRunning}
	sandbox := &Sandbox{id: "sandbox", config: &SandboxConfig{HypervisorType: ClhHypervisor}}
	sandbox.zooVM.generation = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	sandbox.hypervisor = &cloudHypervisor{APIClient: client}
	state, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, sandbox.zooVM.generation)
	if err != nil || state.State != "paused" || state.Revision != 1 {
		test.Fatalf("native CLH pause: %+v %v", state, err)
	}
	state, err = sandbox.ZooSetVMState(context.Background(), "running", "resume-operation-001", 1, sandbox.zooVM.generation)
	if err != nil || state.State != "running" || state.Revision != 2 {
		test.Fatalf("native CLH resume: %+v %v", state, err)
	}
}

func TestZooCLHQueriesActualNativeState(test *testing.T) {
	for _, state := range []string{clhStateRunning, clhStatePaused, clhStateCreated, "Shutdown"} {
		hypervisor := &cloudHypervisor{APIClient: &zooFakeCLHClient{state: state}}
		actual, err := hypervisor.ZooVMStatus(context.Background())
		if state == clhStateRunning && (err != nil || actual != "running") {
			test.Fatalf("running state: %q %v", actual, err)
		}
		if state == clhStatePaused && (err != nil || actual != "paused") {
			test.Fatalf("paused state: %q %v", actual, err)
		}
		if state != clhStateRunning && state != clhStatePaused && err == nil {
			test.Fatal("non-live state accepted")
		}
	}
	for _, hypervisor := range []*cloudHypervisor{{}, {APIClient: &zooFakeCLHClient{err: errors.New("unavailable")}}} {
		if _, err := hypervisor.ZooVMStatus(context.Background()); err == nil {
			test.Fatal("missing native state accepted")
		}
	}
}

type zooFakeHypervisor struct {
	Hypervisor
	sandbox          *Sandbox
	state            string
	pauses           int
	resumes          int
	lostReply        bool
	wrongState       bool
	failVerification bool
}

func (hypervisor *zooFakeHypervisor) ZooVMStatus(context.Context) (string, error) {
	if hypervisor.failVerification && hypervisor.state == "paused" {
		return "", errors.New("query unavailable")
	}
	return hypervisor.state, nil
}

func (hypervisor *zooFakeHypervisor) PauseVM(context.Context) error {
	if !hypervisor.sandbox.zooVM.paused {
		return errors.New("agent health was not gated before pause")
	}
	hypervisor.pauses++
	if hypervisor.wrongState {
		return nil
	}
	hypervisor.state = "paused"
	if hypervisor.lostReply {
		return errors.New("command response lost")
	}
	return nil
}

func (hypervisor *zooFakeHypervisor) ResumeVM(context.Context) error {
	hypervisor.resumes++
	hypervisor.state = "running"
	if hypervisor.lostReply {
		return errors.New("command response lost")
	}
	return nil
}

func zooFakeSandbox() (*Sandbox, *zooFakeHypervisor) {
	sandbox := &Sandbox{id: "sandbox", config: &SandboxConfig{HypervisorType: QemuHypervisor}}
	sandbox.zooVM.generation = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	hypervisor := &zooFakeHypervisor{sandbox: sandbox, state: "running"}
	sandbox.hypervisor = hypervisor
	return sandbox, hypervisor
}

func TestZooRejectsShimEpochChangeEvenWhenNativeIDIsUnchanged(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	previous := sandbox.zooVM.generation
	sandbox.zooVM.generation = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
	if _, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, previous); err == nil {
		test.Fatal("mutation crossed a shim lifecycle epoch")
	}
	if hypervisor.pauses != 0 {
		test.Fatal("stale epoch reached the native VMM")
	}
}

func TestZooPauseRequiresVerifiedVMStateAndGatesGuestHealth(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	state, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, sandbox.zooVM.generation)
	if err != nil || state.State != "paused" || state.Revision != 1 || hypervisor.pauses != 1 {
		test.Fatalf("pause: %+v %v", state, err)
	}
	monitor := &monitor{sandbox: sandbox}
	monitor.watchAgent(context.Background())
	if sandbox.zooVM.failed.Load() {
		test.Fatal("paused VM marked unhealthy")
	}
	if err := sandbox.ZooResumeVMForTeardown(context.Background()); err != nil {
		test.Fatal(err)
	}
	if hypervisor.state != "running" || sandbox.zooVM.paused || hypervisor.resumes != 1 {
		test.Fatal("teardown did not resume the actual VM")
	}
}

func TestZooLostReplyIsResolvedByActualState(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	hypervisor.lostReply = true
	state, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, sandbox.zooVM.generation)
	if err != nil || state.State != "paused" {
		test.Fatalf("verified transition rejected: %v", err)
	}
	if err := sandbox.ZooResumeVMForTeardown(context.Background()); err != nil {
		test.Fatal(err)
	}
}

func TestZooOldOperationReplayNeverUndoesANewerResume(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	ctx := context.Background()
	if _, err := sandbox.ZooSetVMState(ctx, "paused", "pause-operation-0001", 0, sandbox.zooVM.generation); err != nil {
		test.Fatal(err)
	}
	if _, err := sandbox.ZooSetVMState(ctx, "running", "resume-operation-0002", 1, sandbox.zooVM.generation); err != nil {
		test.Fatal(err)
	}
	state, err := sandbox.ZooSetVMState(ctx, "paused", "pause-operation-0001", 0, sandbox.zooVM.generation)
	if err != nil || state.State != "running" || state.Revision != 2 || hypervisor.pauses != 1 {
		test.Fatalf("replay changed the VM: %+v %v", state, err)
	}
	if _, err := sandbox.ZooSetVMState(ctx, "running", "pause-operation-0001", 0, sandbox.zooVM.generation); err == nil {
		test.Fatal("reused operation parameters accepted")
	}
	if _, err := sandbox.ZooSetVMState(ctx, "paused", "stale-operation-0003", 0, sandbox.zooVM.generation); err == nil {
		test.Fatal("stale revision accepted")
	}
}

func TestZooPauseDoesNotTrustAnUnverifiedCommand(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	hypervisor.wrongState = true
	if _, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, sandbox.zooVM.generation); err == nil {
		test.Fatal("unverified pause accepted")
	}
	if sandbox.zooVM.revision != 0 || sandbox.zooVM.paused {
		test.Fatal("running VM incorrectly recorded paused")
	}
	hypervisor.wrongState = false
	hypervisor.failVerification = true
	if _, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, sandbox.zooVM.generation); err == nil {
		test.Fatal("missing verification accepted")
	}
	if !sandbox.zooVM.paused || sandbox.zooVM.revision != 0 {
		test.Fatal("unknown transition must keep guest health gated")
	}
}

func TestZooConcurrentMutationsUseOneRevision(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	results := make(chan error, 2)
	var workers sync.WaitGroup
	for _, operation := range []string{"concurrent-operation-0001", "concurrent-operation-0002"} {
		workers.Go(func() {
			_, err := sandbox.ZooSetVMState(context.Background(), "paused", operation, 0, sandbox.zooVM.generation)
			results <- err
		})
	}
	workers.Wait()
	close(results)
	successes := 0
	for err := range results {
		if err == nil {
			successes++
		}
	}
	if successes != 1 || hypervisor.pauses != 1 || sandbox.zooVM.revision != 1 {
		test.Fatal("concurrent stale mutation accepted")
	}
}

func TestZooRejectsUnhealthyAndUnsupportedSandboxes(test *testing.T) {
	sandbox, hypervisor := zooFakeSandbox()
	sandbox.zooVM.failed.Store(true)
	if _, err := sandbox.ZooSetVMState(context.Background(), "paused", "pause-operation-0001", 0, sandbox.zooVM.generation); err == nil {
		test.Fatal("unhealthy VM paused")
	}
	sandbox.config.HypervisorType = FirecrackerHypervisor
	if _, err := sandbox.ZooVMState(context.Background()); err == nil {
		test.Fatal("unsupported VMM accepted")
	}
	if hypervisor.pauses != 0 {
		test.Fatal("unsupported VMM invoked")
	}
}
