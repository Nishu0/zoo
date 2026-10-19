package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

type fileArgs struct {
	Path string `json:"path"`
	Mode uint32 `json:"mode"`
}

func resolve(path string) (string, error) {
	if path == "" {
		return "", errors.New("path is required")
	}
	if filepath.IsAbs(path) {
		return path, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, path), nil
}

func readOp(c call) (any, []byte, error) {
	var a fileArgs
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	path, err := resolve(a.Path)
	if err != nil {
		return nil, nil, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, err
	}
	return map[string]any{"path": path, "size": len(data)}, data, nil
}

func writeOp(c call) (any, []byte, error) {
	var a fileArgs
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	path, err := resolve(a.Path)
	if err != nil {
		return nil, nil, err
	}
	mode := os.FileMode(a.Mode)
	if mode == 0 {
		mode = 0o644
	}
	if err := os.WriteFile(path, c.payload, mode); err != nil {
		return nil, nil, err
	}
	return map[string]any{"path": path, "size": len(c.payload)}, nil, nil
}
