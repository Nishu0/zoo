package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

type nativeControl interface {
	State(context.Context, identity, controlRequest, string) (vmState, error)
}

type shimControl struct{ root string }

func (control *shimControl) State(ctx context.Context, resolved identity, operation controlRequest, method string) (vmState, error) {
	path, err := shimSocket(control.root, resolved.ID)
	if err != nil {
		return vmState{}, err
	}
	client := unixClient(path, true)
	defer client.CloseIdleConnections()
	shimMethod := http.MethodGet
	var body io.Reader
	if method == http.MethodPut {
		shimMethod = http.MethodPut
		data, err := json.Marshal(struct {
			SandboxID          string  `json:"sandbox_id"`
			DesiredState       string  `json:"desired_state"`
			OperationID        string  `json:"operation_id"`
			ExpectedRevision   *uint64 `json:"expected_revision"`
			ExpectedGeneration string  `json:"expected_generation"`
		}{resolved.ID, operation.DesiredState, operation.OperationID, operation.ExpectedRevision, operation.Target.Generation})
		if err != nil {
			return vmState{}, err
		}
		body = bytes.NewReader(data)
	}
	request, err := http.NewRequestWithContext(ctx, shimMethod, "http://shim/zoo/v1/vm", body)
	if err != nil {
		return vmState{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.Do(request)
	if err != nil {
		return vmState{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return vmState{}, fmt.Errorf("shim control returned %d", response.StatusCode)
	}
	var state vmState
	decoder := json.NewDecoder(io.LimitReader(response.Body, 8192))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&state); err != nil {
		return vmState{}, err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return vmState{}, errors.New("invalid shim response framing")
	}
	if state.Protocol != protocol || state.KataVersion != kataVersion || state.KataRevision != kataRevision ||
		!runtimeIDPattern.MatchString(state.Generation) || (operation.Target.Generation != "" && operation.Target.Generation != state.Generation) ||
		state.SandboxID != resolved.ID || state.Namespace != resolved.Namespace ||
		(state.State != "paused" && state.State != "running") ||
		(method == http.MethodPut && state.OperationID != operation.OperationID) {
		return vmState{}, errors.New("shim version, identity, or verified state mismatch")
	}
	return state, nil
}

type broker struct {
	nodeName    string
	nodeUID     string
	frontendUID uint32
	controllers map[string]bool
	inventory   inventory
	control     nativeControl
	memoryPath  string
}

func (service *broker) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	uid, authenticated := request.Context().Value(peerUIDKey{}).(uint32)
	controller := request.Header.Get("X-Zoo-Controller")
	if !authenticated || uid != service.frontendUID || !service.controllers[controller] {
		http.Error(writer, "authenticated controller frontend required", http.StatusForbidden)
		return
	}
	if request.URL.RawQuery != "" {
		http.Error(writer, "query parameters are not accepted", http.StatusBadRequest)
		return
	}
	if request.URL.Path == "/v1/host/memory" && request.Method == http.MethodGet {
		memory, err := readMemory(service.memoryPath)
		if err != nil {
			http.Error(writer, "host memory unavailable", http.StatusServiceUnavailable)
			return
		}
		memory.NodeName, memory.NodeUID = service.nodeName, service.nodeUID
		writeJSON(writer, memory)
		return
	}
	if request.URL.Path != "/v1/vm/state" {
		http.NotFound(writer, request)
		return
	}
	if request.Method != http.MethodPost && request.Method != http.MethodPut {
		http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var operation controlRequest
	if err := decodeRequest(writer, request, &operation); err != nil {
		http.Error(writer, "invalid request", http.StatusBadRequest)
		return
	}
	if operation.Target.NodeName != service.nodeName || operation.Target.NodeUID != service.nodeUID || !uuidPattern.MatchString(operation.Target.SandboxID) {
		http.Error(writer, "node and sandbox identity mismatch", http.StatusConflict)
		return
	}
	if err := validateOperation(operation, request.Method); err != nil {
		http.Error(writer, err.Error(), http.StatusBadRequest)
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 15*time.Second)
	defer cancel()
	resolved, err := service.inventory.Resolve(ctx, operation.Target)
	if err != nil || (operation.Target.NativeID != "" && operation.Target.NativeID != resolved.ID) {
		log.Printf("controller=%s sandbox=%s operation=%s inventory denied", controller, operation.Target.SandboxID, operation.OperationID)
		http.Error(writer, "current local runtime identity unavailable", http.StatusConflict)
		return
	}
	state, err := service.control.State(ctx, resolved, operation, request.Method)
	if err != nil {
		log.Printf("controller=%s sandbox=%s operation=%s control failed: %v", controller, operation.Target.SandboxID, operation.OperationID, err)
		http.Error(writer, "native VM transition unavailable or conflicted", http.StatusConflict)
		return
	}
	log.Printf("controller=%s sandbox=%s operation=%s state=%s revision=%d", controller, operation.Target.SandboxID, operation.OperationID, state.State, state.Revision)
	writeJSON(writer, controlResponse{Target: operation.Target, VM: state})
}

func readMemory(path string) (memoryState, error) {
	file, err := os.Open(path)
	if err != nil {
		return memoryState{}, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 16385))
	if err != nil {
		return memoryState{}, err
	}
	if len(data) > 16384 {
		return memoryState{}, errors.New("host memory report exceeds limit")
	}
	return parseMemory(string(data))
}

func parseMemory(data string) (memoryState, error) {
	values := map[string]uint64{}
	for _, line := range strings.Split(data, "\n") {
		fields := strings.Fields(line)
		if len(fields) == 0 || fields[0] != "MemAvailable:" && fields[0] != "MemTotal:" {
			continue
		}
		if len(fields) != 3 || fields[2] != "kB" {
			return memoryState{}, errors.New("invalid memory units")
		}
		if _, duplicate := values[fields[0]]; duplicate {
			return memoryState{}, errors.New("duplicate memory field")
		}
		value, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil || value > ^uint64(0)/1024 {
			return memoryState{}, errors.New("invalid memory value")
		}
		values[fields[0]] = value * 1024
	}
	available, hasAvailable := values["MemAvailable:"]
	total, hasTotal := values["MemTotal:"]
	if !hasAvailable || !hasTotal || total == 0 || available > total {
		return memoryState{}, errors.New("host memory fields unavailable")
	}
	return memoryState{AvailableBytes: available, TotalBytes: total}, nil
}
