package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
)

const protocol = "zoo-kata-vm-control/1"
const kataVersion = "4.2.0"
const kataRevision = "c7351e797efff8bfc6bd73da0eb1909be12e2cfe"

var runtimeIDPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var uuidPattern = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)
var operationPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{15,127}$`)
var nodePattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$`)

type target struct {
	NodeName     string `json:"node_name"`
	NodeUID      string `json:"node_uid"`
	SandboxID    string `json:"sandbox_id"`
	PodNamespace string `json:"pod_namespace,omitempty"`
	PodName      string `json:"pod_name,omitempty"`
	PodUID       string `json:"pod_uid,omitempty"`
	ContainerID  string `json:"container_id,omitempty"`
	NativeID     string `json:"native_id,omitempty"`
	Generation   string `json:"generation,omitempty"`
}

type controlRequest struct {
	Target           target  `json:"target"`
	DesiredState     string  `json:"desired_state,omitempty"`
	OperationID      string  `json:"operation_id,omitempty"`
	ExpectedRevision *uint64 `json:"expected_revision,omitempty"`
}

type vmState struct {
	Protocol     string `json:"protocol"`
	KataVersion  string `json:"kata_version"`
	KataRevision string `json:"kata_revision"`
	SandboxID    string `json:"sandbox_id"`
	Namespace    string `json:"namespace"`
	State        string `json:"state"`
	Revision     uint64 `json:"revision"`
	OperationID  string `json:"operation_id,omitempty"`
	Generation   string `json:"generation"`
}

type controlResponse struct {
	Target target  `json:"target"`
	VM     vmState `json:"vm"`
}

type memoryState struct {
	NodeName       string `json:"node_name"`
	NodeUID        string `json:"node_uid"`
	AvailableBytes uint64 `json:"available_bytes"`
	TotalBytes     uint64 `json:"total_bytes"`
}

func decodeRequest(writer http.ResponseWriter, request *http.Request, destination any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(writer, request.Body, 8192))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return errors.New("request must contain one JSON object")
	}
	return nil
}

func writeJSON(writer http.ResponseWriter, value any) {
	writer.Header().Set("Content-Type", "application/json")
	json.NewEncoder(writer).Encode(value)
}

func validateOperation(operation controlRequest, method string) error {
	if operation.Target.Generation != "" && !runtimeIDPattern.MatchString(operation.Target.Generation) {
		return errors.New("generation must identify the exact native shim epoch")
	}
	if operation.Target.NativeID != "" && !runtimeIDPattern.MatchString(operation.Target.NativeID) {
		return errors.New("native_id must identify the exact Kata VM generation")
	}
	if method == http.MethodPost {
		if operation.DesiredState != "" || operation.OperationID != "" || operation.ExpectedRevision != nil {
			return errors.New("state query cannot carry a mutation")
		}
		return nil
	}
	if operation.DesiredState != "paused" && operation.DesiredState != "running" {
		return errors.New("desired_state must be paused or running")
	}
	if operation.ExpectedRevision == nil || !operationPattern.MatchString(operation.OperationID) || operation.Target.NativeID == "" || operation.Target.Generation == "" {
		return errors.New("native_id, generation, operation_id and expected_revision are required")
	}
	return nil
}
