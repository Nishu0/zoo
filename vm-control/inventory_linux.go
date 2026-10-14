package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	cri "k8s.io/cri-api/pkg/apis/runtime/v1"
)

type identity struct {
	ID        string
	Namespace string
}

type inventory interface {
	Resolve(context.Context, target) (identity, error)
}

type criInventory struct {
	client    cri.RuntimeServiceClient
	namespace string
	handler   string
}

func connectCRI(socket, namespace, handler string) (*criInventory, *grpc.ClientConn, error) {
	connection, err := grpc.NewClient("passthrough:///local-cri",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(ctx context.Context, address string) (net.Conn, error) {
			connection, err := (&net.Dialer{}).DialContext(ctx, "unix", socket)
			if err != nil {
				return nil, err
			}
			uid, err := peerUID(connection)
			if err != nil || uid != 0 {
				connection.Close()
				return nil, errors.New("CRI peer must be root")
			}
			return connection, nil
		}),
	)
	if err != nil {
		return nil, nil, err
	}
	return &criInventory{client: cri.NewRuntimeServiceClient(connection), namespace: namespace, handler: handler}, connection, nil
}

func (source *criInventory) Resolve(ctx context.Context, requested target) (identity, error) {
	if requested.PodNamespace != source.namespace || requested.PodName != "zoo-sandbox-"+requested.SandboxID ||
		!uuidPattern.MatchString(requested.PodUID) || requested.ContainerID != "" {
		return identity{}, errors.New("installation Pod identity required")
	}
	response, err := source.client.ListPodSandbox(ctx, &cri.ListPodSandboxRequest{
		Filter: &cri.PodSandboxFilter{LabelSelector: map[string]string{
			"zoo.dev/sandbox": requested.SandboxID, "app.kubernetes.io/managed-by": "zoo",
		}},
	})
	if err != nil {
		return identity{}, err
	}
	var resolved identity
	for _, pod := range response.Items {
		metadata := pod.Metadata
		if metadata == nil || metadata.Uid != requested.PodUID || metadata.Namespace != source.namespace || metadata.Name != requested.PodName {
			continue
		}
		if pod.Labels["zoo.dev/sandbox"] != requested.SandboxID || pod.Labels["app.kubernetes.io/managed-by"] != "zoo" ||
			pod.RuntimeHandler != source.handler || pod.State != cri.PodSandboxState_SANDBOX_READY || !runtimeIDPattern.MatchString(pod.Id) {
			return identity{}, errors.New("Pod is not a live installation Kata sandbox")
		}
		if resolved.ID != "" {
			return identity{}, errors.New("Pod sandbox generation is ambiguous")
		}
		resolved = identity{ID: pod.Id, Namespace: "k8s.io"}
	}
	if resolved.ID == "" {
		return identity{}, errors.New("current local Pod sandbox not found")
	}
	return resolved, nil
}

type dockerInventory struct {
	client    *http.Client
	namespace string
}

func (source *dockerInventory) Resolve(ctx context.Context, requested target) (identity, error) {
	if !runtimeIDPattern.MatchString(requested.ContainerID) || requested.PodUID != "" || requested.PodName != "" || requested.PodNamespace != "" {
		return identity{}, errors.New("full Docker container ID required")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker/containers/"+requested.ContainerID+"/json", nil)
	if err != nil {
		return identity{}, err
	}
	response, err := source.client.Do(request)
	if err != nil {
		return identity{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return identity{}, fmt.Errorf("Docker inventory returned %d", response.StatusCode)
	}
	var container struct {
		ID         string                             `json:"Id"`
		Name       string                             `json:"Name"`
		Config     struct{ Labels map[string]string } `json:"Config"`
		HostConfig struct{ Runtime string }           `json:"HostConfig"`
		State      struct {
			Running    bool
			Paused     bool
			Restarting bool
			Dead       bool
		} `json:"State"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&container); err != nil {
		return identity{}, err
	}
	if container.ID != requested.ContainerID || container.Name != "/zoo-sandbox-"+requested.SandboxID ||
		container.Config.Labels["zoo.sandbox"] != requested.SandboxID || container.HostConfig.Runtime != "io.containerd.kata.v2" ||
		!container.State.Running || container.State.Paused || container.State.Restarting || container.State.Dead {
		return identity{}, errors.New("container is not a live installation Kata sandbox")
	}
	return identity{ID: container.ID, Namespace: source.namespace}, nil
}
