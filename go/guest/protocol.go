package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
)

// A frame is one binary websocket message: a 4-byte big-endian header length, a JSON header, then raw payload
// bytes (file contents, screenshots, command output) so nothing large is base64-encoded.

func pack(header any, payload []byte) ([]byte, error) {
	h, err := json.Marshal(header)
	if err != nil {
		return nil, err
	}
	frame := make([]byte, 4, 4+len(h)+len(payload))
	binary.BigEndian.PutUint32(frame, uint32(len(h)))
	return append(append(frame, h...), payload...), nil
}

func unpack(frame []byte) (json.RawMessage, []byte, error) {
	if len(frame) < 4 {
		return nil, nil, errors.New("short frame")
	}
	n := binary.BigEndian.Uint32(frame)
	if uint64(n) > uint64(len(frame)-4) {
		return nil, nil, errors.New("header overruns frame")
	}
	return frame[4 : 4+n], frame[4+n:], nil
}
