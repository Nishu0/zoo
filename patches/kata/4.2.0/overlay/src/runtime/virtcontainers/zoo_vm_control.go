package virtcontainers

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"sync"
	"sync/atomic"
)

const ZooVMProtocol = "zoo-kata-vm-control/1"
const ZooKataVersion = "4.2.0"
const ZooKataRevision = "c7351e797efff8bfc6bd73da0eb1909be12e2cfe"

var zooOperationID = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{15,127}$`)

type ZooVMState struct {
	Protocol     string `json:"protocol"`
	KataVersion  string `json:"kata_version"`
	KataRevision string `json:"kata_revision"`
	SandboxID    string `json:"sandbox_id"`
	Namespace    string `json:"namespace,omitempty"`
	State        string `json:"state"`
	Revision     uint64 `json:"revision"`
	OperationID  string `json:"operation_id,omitempty"`
	Generation   string `json:"generation"`
}

type zooVMOperation struct {
	desired  string
	expected uint64
}

type zooVMControl struct {
	gate       sync.RWMutex
	paused     bool
	failed     atomic.Bool
	revision   uint64
	generation string
	operations map[string]zooVMOperation
	order      []string
}

type zooVMHypervisor interface {
	PauseVM(context.Context) error
	ResumeVM(context.Context) error
	ZooVMStatus(context.Context) (string, error)
}

func (sandbox *Sandbox) ZooVMGuestReady() bool {
	sandbox.zooVM.gate.RLock()
	defer sandbox.zooVM.gate.RUnlock()
	return !sandbox.zooVM.paused && !sandbox.zooVM.failed.Load()
}

func (sandbox *Sandbox) zooVMHypervisor() (zooVMHypervisor, error) {
	if sandbox.config == nil || (sandbox.config.HypervisorType != QemuHypervisor && sandbox.config.HypervisorType != ClhHypervisor) || sandbox.config.HypervisorConfig.Rootless {
		return nil, errors.New("Zoo VM control requires the pinned rootful Go QEMU or CLH runtime")
	}
	hypervisor, supported := sandbox.hypervisor.(zooVMHypervisor)
	if !supported {
		return nil, errors.New("hypervisor has no verified Zoo VM control implementation")
	}
	return hypervisor, nil
}

func (hypervisor *cloudHypervisor) ZooVMStatus(ctx context.Context) (string, error) {
	if hypervisor.APIClient == nil {
		return "", errors.New("live shim-owned CLH client required")
	}
	status, response, err := hypervisor.APIClient.VmInfoGet(ctx)
	if response != nil && response.Body != nil {
		response.Body.Close()
	}
	if err != nil {
		return "", err
	}
	switch status.State {
	case clhStateRunning:
		return "running", nil
	case clhStatePaused:
		return "paused", nil
	default:
		return "", fmt.Errorf("CLH VM is not controllable: %s", status.State)
	}
}

func (hypervisor *qemu) ZooVMStatus(ctx context.Context) (string, error) {
	hypervisor.qmpMonitorCh.Lock()
	connection := hypervisor.qmpMonitorCh.qmp
	hypervisor.qmpMonitorCh.Unlock()
	if connection == nil {
		return "", errors.New("live shim-owned QMP connection required")
	}
	status, err := connection.ExecuteQueryStatus(ctx)
	if err != nil {
		return "", err
	}
	if status.Status != "running" && status.Status != "paused" {
		return "", fmt.Errorf("VM is not controllable: %s", status.Status)
	}
	if status.Running != (status.Status == "running") {
		return "", errors.New("inconsistent QMP run state")
	}
	return status.Status, nil
}

func (sandbox *Sandbox) zooVMState(ctx context.Context) (ZooVMState, error) {
	hypervisor, err := sandbox.zooVMHypervisor()
	if err != nil {
		return ZooVMState{}, err
	}
	state, err := hypervisor.ZooVMStatus(ctx)
	if err != nil {
		return ZooVMState{}, err
	}
	sandbox.zooVM.paused = state == "paused"
	if sandbox.zooVM.generation == "" {
		var token [32]byte
		if _, err := rand.Read(token[:]); err != nil {
			return ZooVMState{}, err
		}
		sandbox.zooVM.generation = hex.EncodeToString(token[:])
	}
	return ZooVMState{
		Protocol: ZooVMProtocol, KataVersion: ZooKataVersion, KataRevision: ZooKataRevision,
		SandboxID: sandbox.id, State: state, Revision: sandbox.zooVM.revision, Generation: sandbox.zooVM.generation,
	}, nil
}

func (sandbox *Sandbox) ZooVMState(ctx context.Context) (ZooVMState, error) {
	sandbox.zooVM.gate.Lock()
	defer sandbox.zooVM.gate.Unlock()
	return sandbox.zooVMState(ctx)
}

func (sandbox *Sandbox) ZooSetVMState(ctx context.Context, desired, operationID string, expected uint64, generation string) (ZooVMState, error) {
	if desired != "running" && desired != "paused" || !zooOperationID.MatchString(operationID) {
		return ZooVMState{}, errors.New("invalid VM control operation")
	}
	sandbox.zooVM.gate.Lock()
	defer sandbox.zooVM.gate.Unlock()
	current, err := sandbox.zooVMState(ctx)
	if generation == "" || generation != sandbox.zooVM.generation {
		return ZooVMState{}, errors.New("native shim generation conflict")
	}
	if err != nil {
		return ZooVMState{}, err
	}
	if previous, exists := sandbox.zooVM.operations[operationID]; exists {
		if previous.desired != desired || previous.expected != expected {
			return ZooVMState{}, errors.New("operation ID was reused with different parameters")
		}
		current.OperationID = operationID
		return current, nil
	}
	if expected != sandbox.zooVM.revision {
		return ZooVMState{}, errors.New("VM control revision conflict")
	}
	if sandbox.zooVM.failed.Load() {
		return ZooVMState{}, errors.New("sandbox health has failed")
	}
	hypervisor, err := sandbox.zooVMHypervisor()
	if err != nil {
		return ZooVMState{}, err
	}
	if current.State != desired {
		sandbox.zooVM.paused = true
		if desired == "paused" {
			err = hypervisor.PauseVM(ctx)
		} else {
			err = hypervisor.ResumeVM(ctx)
		}
		verified, verifyErr := sandbox.zooVMState(ctx)
		if verifyErr != nil {
			return ZooVMState{}, verifyErr
		}
		if verified.State != desired {
			return ZooVMState{}, fmt.Errorf("VM transition not verified: state=%s command_error=%v", verified.State, err)
		}
	}
	if sandbox.zooVM.operations == nil {
		sandbox.zooVM.operations = make(map[string]zooVMOperation)
	}
	sandbox.zooVM.revision++
	sandbox.zooVM.operations[operationID] = zooVMOperation{desired: desired, expected: expected}
	sandbox.zooVM.order = append(sandbox.zooVM.order, operationID)
	if len(sandbox.zooVM.order) > 256 {
		delete(sandbox.zooVM.operations, sandbox.zooVM.order[0])
		sandbox.zooVM.order = sandbox.zooVM.order[1:]
	}
	result, err := sandbox.zooVMState(ctx)
	result.OperationID = operationID
	return result, err
}

func (sandbox *Sandbox) ZooResumeVMForTeardown(ctx context.Context) error {
	sandbox.zooVM.gate.Lock()
	defer sandbox.zooVM.gate.Unlock()
	if !sandbox.zooVM.paused {
		return nil
	}
	hypervisor, err := sandbox.zooVMHypervisor()
	if err != nil {
		return err
	}
	commandErr := hypervisor.ResumeVM(ctx)
	state, err := sandbox.zooVMState(ctx)
	if err != nil {
		return err
	}
	if state.State != "running" {
		return fmt.Errorf("VM teardown resume was not verified: command_error=%v", commandErr)
	}
	sandbox.zooVM.revision++
	return nil
}
