package main

import (
	"bytes"
	"compress/zlib"
	"context"
	"crypto/des"
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"io"
	"net"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

type fakeRFB struct {
	connection net.Conn
	writes     sync.Mutex
	commands   chan []byte
	done       chan error
}

func startFakeRFB(test *testing.T, width, height int, password string) (*VNCSession, *fakeRFB) {
	test.Helper()
	client, server := net.Pipe()
	fake := &fakeRFB{connection: server, commands: make(chan []byte, 256), done: make(chan error, 1)}
	go func() {
		fake.done <- fake.serve(width, height, password)
		server.Close()
	}()
	session, err := NewVNCSession(context.Background(), client, password)
	if err != nil {
		client.Close()
		server.Close()
		test.Fatal(err)
	}
	test.Cleanup(func() { session.Close(); server.Close(); <-fake.done })
	return session, fake
}

func (fake *fakeRFB) serve(width, height int, password string) error {
	if _, err := fake.connection.Write([]byte("RFB 003.008\n")); err != nil {
		return err
	}
	version := make([]byte, 12)
	if _, err := io.ReadFull(fake.connection, version); err != nil {
		return err
	}
	if string(version) != "RFB 003.008\n" {
		return errors.New("unexpected client version")
	}
	if _, err := fake.connection.Write([]byte{1, 2}); err != nil {
		return err
	}
	selected := make([]byte, 1)
	if _, err := io.ReadFull(fake.connection, selected); err != nil {
		return err
	}
	if selected[0] != 2 {
		return errors.New("client did not select VNC auth")
	}
	challenge := []byte{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
	if _, err := fake.connection.Write(challenge); err != nil {
		return err
	}
	response := make([]byte, 16)
	if _, err := io.ReadFull(fake.connection, response); err != nil {
		return err
	}
	expected, err := fakeVNCResponse(password, challenge)
	if err != nil {
		return err
	}
	if !bytes.Equal(response, expected) {
		rejection := []byte{0, 0, 0, 1, 0, 0, 0, 14}
		rejection = append(rejection, []byte("wrong password")...)
		_, err := fake.connection.Write(rejection)
		return err
	}
	if _, err := fake.connection.Write([]byte{0, 0, 0, 0}); err != nil {
		return err
	}
	if _, err := io.ReadFull(fake.connection, selected); err != nil {
		return err
	}
	if selected[0] != 1 {
		return errors.New("client did not request shared desktop")
	}
	initial := make([]byte, 24)
	binary.BigEndian.PutUint16(initial, uint16(width))
	binary.BigEndian.PutUint16(initial[2:], uint16(height))
	copy(initial[4:], []byte{32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0})
	if _, err := fake.connection.Write(initial); err != nil {
		return err
	}
	for {
		typeByte := make([]byte, 1)
		if _, err := io.ReadFull(fake.connection, typeByte); err != nil {
			return err
		}
		var length int
		switch typeByte[0] {
		case 0:
			length = 19
		case 2:
			length = 3
		case 3:
			length = 9
		case 4:
			length = 7
		case 5:
			length = 5
		case 6, 251:
			length = 7
		default:
			return fmt.Errorf("unexpected client message %d", typeByte[0])
		}
		body := make([]byte, length)
		if _, err := io.ReadFull(fake.connection, body); err != nil {
			return err
		}
		message := append(typeByte, body...)
		if typeByte[0] == 2 {
			encodings := make([]byte, int(binary.BigEndian.Uint16(body[1:]))*4)
			if _, err := io.ReadFull(fake.connection, encodings); err != nil {
				return err
			}
			message = append(message, encodings...)
		}
		if typeByte[0] == 6 || typeByte[0] == 251 {
			length := int(binary.BigEndian.Uint32(message[4:]))
			if typeByte[0] == 251 {
				length = int(message[6]) * 16
			}
			payload := make([]byte, length)
			if _, err := io.ReadFull(fake.connection, payload); err != nil {
				return err
			}
			message = append(message, payload...)
		}
		select {
		case fake.commands <- message:
		default:
			return errors.New("fake client message queue is full")
		}
	}
}

func fakeVNCResponse(password string, challenge []byte) ([]byte, error) {
	var key [8]byte
	copy(key[:], password)
	for index, value := range key {
		var reversed byte
		for shift := range 8 {
			reversed |= ((value >> shift) & 1) << uint(7-shift)
		}
		key[index] = reversed
	}
	cipher, err := des.NewCipher(key[:])
	if err != nil {
		return nil, err
	}
	response := make([]byte, 16)
	cipher.Encrypt(response[:8], challenge[:8])
	cipher.Encrypt(response[8:], challenge[8:])
	return response, nil
}

func fakeRFBSend(test *testing.T, fake *fakeRFB, data []byte) {
	test.Helper()
	fake.writes.Lock()
	defer fake.writes.Unlock()
	if err := fake.connection.SetWriteDeadline(time.Now().Add(2 * time.Second)); err != nil {
		test.Fatal(err)
	}
	if _, err := fake.connection.Write(data); err != nil {
		test.Fatal(err)
	}
	if err := fake.connection.SetWriteDeadline(time.Time{}); err != nil {
		test.Fatal(err)
	}
}

func fakeRFBRectangle(rectangle image.Rectangle, encoding int32, payload []byte) []byte {
	message := make([]byte, 16)
	message[3] = 1
	binary.BigEndian.PutUint16(message[4:], uint16(rectangle.Min.X))
	binary.BigEndian.PutUint16(message[6:], uint16(rectangle.Min.Y))
	binary.BigEndian.PutUint16(message[8:], uint16(rectangle.Dx()))
	binary.BigEndian.PutUint16(message[10:], uint16(rectangle.Dy()))
	binary.BigEndian.PutUint32(message[12:], uint32(encoding))
	return append(message, payload...)
}

func fakeRFBPixels(width, height int, pixel color.RGBA) []byte {
	pixels := make([]byte, width*height*4)
	for index := 0; index < len(pixels); index += 4 {
		pixels[index], pixels[index+1], pixels[index+2] = pixel.B, pixel.G, pixel.R
	}
	return pixels
}

func awaitRFBCommand(test *testing.T, fake *fakeRFB, kind byte) []byte {
	test.Helper()
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	for {
		select {
		case message := <-fake.commands:
			if message[0] == kind {
				return message
			}
		case <-deadline.C:
			test.Fatalf("no client message %d", kind)
			return nil
		}
	}
}

func awaitVNCVersion(test *testing.T, session *VNCSession, minimum uint64) (*image.RGBA, uint64) {
	test.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	for {
		changed := session.Changed()
		frame, version, err := session.Frame(ctx)
		if err != nil {
			test.Fatal(err)
		}
		if version >= minimum {
			return frame, version
		}
		select {
		case <-changed:
		case <-ctx.Done():
			test.Fatal(ctx.Err())
		}
	}
}

func TestVNCHandshakeAndAuth(test *testing.T) {
	session, fake := startFakeRFB(test, 65, 33, "password")
	pixelFormat := awaitRFBCommand(test, fake, 0)
	if !bytes.Equal(pixelFormat[4:], []byte{32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0}) {
		test.Fatalf("pixel format = %v", pixelFormat)
	}
	encodings := awaitRFBCommand(test, fake, 2)
	for _, expected := range []int32{vncRaw, vncCopyRect, vncZlib, vncTight, vncDesktopSize, vncExtendedDesktopSize} {
		found := false
		for offset := 4; offset < len(encodings); offset += 4 {
			found = found || int32(binary.BigEndian.Uint32(encodings[offset:])) == expected
		}
		if !found {
			test.Fatalf("encoding %d was not advertised", expected)
		}
	}
	request := awaitRFBCommand(test, fake, 3)
	if request[1] != 0 || binary.BigEndian.Uint16(request[6:]) != 65 || binary.BigEndian.Uint16(request[8:]) != 33 {
		test.Fatalf("initial update request = %v", request)
	}
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 65, 33), vncRaw, fakeRFBPixels(65, 33, color.RGBA{R: 21, G: 42, B: 84, A: 255})))
	frame, _ := awaitVNCVersion(test, session, 1)
	if pixel := frame.RGBAAt(64, 32); pixel != (color.RGBA{R: 21, G: 42, B: 84, A: 255}) {
		test.Fatalf("raw pixel = %v", pixel)
	}
	if request := awaitRFBCommand(test, fake, 3); request[1] != 1 {
		test.Fatal("subsequent request is not incremental")
	}
}

func TestVNCAuthTruncatesPasswordAndReversesKey(test *testing.T) {
	challenge := []byte{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
	cipher, err := des.NewCipher([]byte{0x0e, 0x86, 0xce, 0xce, 0xee, 0xf6, 0x4e, 0x26})
	if err != nil {
		test.Fatal(err)
	}
	expected := make([]byte, 16)
	cipher.Encrypt(expected[:8], challenge[:8])
	cipher.Encrypt(expected[8:], challenge[8:])
	response, err := vncAuthResponse("password-extra", challenge)
	if err != nil || !bytes.Equal(response, expected) {
		test.Fatalf("auth response = %x, %v", response, err)
	}
	if _, err := vncAuthResponse("password", challenge[:8]); err == nil {
		test.Fatal("accepted short challenge")
	}
}

func TestVNCAuthenticationRejectsWrongPassword(test *testing.T) {
	client, server := net.Pipe()
	defer server.Close()
	fake := &fakeRFB{connection: server, commands: make(chan []byte, 1)}
	done := make(chan error, 1)
	go func() { done <- fake.serve(1, 1, "correct"); server.Close() }()
	if session, err := NewVNCSession(context.Background(), client, "wrong"); err == nil {
		session.Close()
		test.Fatal("accepted wrong password")
	} else if !strings.Contains(err.Error(), "wrong password") {
		test.Fatalf("auth failure = %v", err)
	}
	<-done
}

func TestVNCNoAuthenticationDowngrade(test *testing.T) {
	client, server := net.Pipe()
	defer server.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		server.Write([]byte("RFB 003.008\n"))
		version := make([]byte, 12)
		io.ReadFull(server, version)
		server.Write([]byte{1, 1})
	}()
	if session, err := NewVNCSession(context.Background(), client, "required"); err == nil {
		session.Close()
		test.Fatal("downgraded configured authentication")
	}
	<-done
}

func TestVNCPNGCacheAndVersionedTileDiff(test *testing.T) {
	session, fake := startFakeRFB(test, 65, 33, "password")
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 65, 33), vncRaw, fakeRFBPixels(65, 33, color.RGBA{A: 255})))
	_, initialVersion := awaitVNCVersion(test, session, 1)
	first, version, err := session.Screenshot(context.Background())
	if err != nil || version != initialVersion {
		test.Fatalf("screenshot = %d, %v", version, err)
	}
	second, secondVersion, err := session.Screenshot(context.Background())
	if err != nil || secondVersion != version || !bytes.Equal(first, second) {
		test.Fatal("PNG cache did not preserve frame")
	}
	first[0] = 0
	third, _, err := session.Screenshot(context.Background())
	if err != nil || third[0] != 0x89 {
		test.Fatal("caller mutated PNG cache")
	}
	initialDiff, err := session.Diff(context.Background(), 0)
	if err != nil || len(initialDiff.ChangedTiles) != 6 || len(initialDiff.Boxes) != 6 {
		test.Fatalf("initial diff = %+v, %v", initialDiff, err)
	}
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(64, 32, 65, 33), vncRaw, fakeRFBPixels(1, 1, color.RGBA{R: 255, A: 255})))
	_, updatedVersion := awaitVNCVersion(test, session, version+1)
	diff, err := session.Diff(context.Background(), version)
	if err != nil || diff.Version != updatedVersion || !slices.Equal(diff.ChangedTiles, []uint32{5}) || !slices.Equal(diff.Boxes, []image.Rectangle{image.Rect(64, 32, 65, 33)}) {
		test.Fatalf("changed diff = %+v, %v", diff, err)
	}
	if pixel := diff.Frame.RGBAAt(64, 32); pixel.R != 255 {
		test.Fatalf("diff snapshot pixel = %v", pixel)
	}
	diff.Frame.Pix[0] = 255
	frame, _ := awaitVNCVersion(test, session, diff.Version)
	if frame.Pix[0] != 0 {
		test.Fatal("diff snapshot aliases session framebuffer")
	}
	updatedPNG, pngVersion, err := session.Screenshot(context.Background())
	if err != nil || pngVersion != updatedVersion || bytes.Equal(third, updatedPNG) {
		test.Fatal("PNG cache was not invalidated")
	}
	decoded, err := png.Decode(bytes.NewReader(updatedPNG))
	if err != nil {
		test.Fatal(err)
	}
	red, _, _, _ := decoded.At(64, 32).RGBA()
	if red != 65535 {
		test.Fatal("PNG does not match latest framebuffer")
	}
	unchanged, err := session.Diff(context.Background(), updatedVersion)
	if err != nil || len(unchanged.ChangedTiles) != 0 {
		test.Fatalf("unchanged diff = %+v, %v", unchanged, err)
	}
	if _, err := session.Diff(context.Background(), updatedVersion+1); err == nil {
		test.Fatal("accepted future diff version")
	}
}

func TestVNCCopyRectHandlesOverlap(test *testing.T) {
	session, fake := startFakeRFB(test, 4, 4, "password")
	pixels := make([]byte, 4*4*4)
	for row := range 4 {
		for column := range 4 {
			pixels[(row*4+column)*4+2] = byte(row + 1)
		}
	}
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 4, 4), vncRaw, pixels))
	_, version := awaitVNCVersion(test, session, 1)
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 1, 4, 4), vncCopyRect, []byte{0, 0, 0, 0}))
	frame, _ := awaitVNCVersion(test, session, version+1)
	for row, expected := range []byte{1, 1, 2, 3} {
		if pixel := frame.RGBAAt(0, row); pixel.R != expected {
			test.Fatalf("copied row %d = %v, want red %d", row, pixel, expected)
		}
	}
}

type fakeRFBCompressor struct {
	buffer bytes.Buffer
	writer *zlib.Writer
}

func (compressor *fakeRFBCompressor) chunk(test *testing.T, pixels []byte) []byte {
	test.Helper()
	if compressor.writer == nil {
		writer, err := zlib.NewWriterLevel(&compressor.buffer, 1)
		if err != nil {
			test.Fatal(err)
		}
		compressor.writer = writer
		test.Cleanup(func() { compressor.writer.Close() })
	}
	if _, err := compressor.writer.Write(pixels); err != nil {
		test.Fatal(err)
	}
	if err := compressor.writer.Flush(); err != nil {
		test.Fatal(err)
	}
	compressed := bytes.Clone(compressor.buffer.Bytes())
	compressor.buffer.Reset()
	return compressed
}

func fakeCompactLength(length int) []byte {
	first := byte(length & 0x7f)
	if length < 128 {
		return []byte{first}
	}
	second := byte((length >> 7) & 0x7f)
	if length < 16384 {
		return []byte{first | 0x80, second}
	}
	return []byte{first | 0x80, second | 0x80, byte(length >> 14)}
}

func TestVNCZlibStreamPersistsAcrossRectangles(test *testing.T) {
	session, fake := startFakeRFB(test, 32, 32, "password")
	var compressor fakeRFBCompressor
	for index := range 3 {
		compressed := compressor.chunk(test, fakeRFBPixels(32, 32, color.RGBA{R: byte(index + 1), G: 77, B: 99, A: 255}))
		payload := make([]byte, 4)
		binary.BigEndian.PutUint32(payload, uint32(len(compressed)))
		payload = append(payload, compressed...)
		fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 32, 32), vncZlib, payload))
		frame, _ := awaitVNCVersion(test, session, uint64(index+1))
		if pixel := frame.RGBAAt(31, 31); pixel != (color.RGBA{R: byte(index + 1), G: 77, B: 99, A: 255}) {
			test.Fatalf("zlib pixel = %v", pixel)
		}
	}
}

func TestVNCTightStreamsPersistAndReset(test *testing.T) {
	session, fake := startFakeRFB(test, 4, 1, "password")
	var compressors [4]fakeRFBCompressor
	version := uint64(0)
	for iteration := range 2 {
		for streamID := range 4 {
			colorValue := byte(iteration*4 + streamID + 1)
			compressed := compressors[streamID].chunk(test, bytes.Repeat([]byte{colorValue, 42, 84}, 4))
			control := byte(streamID << 4)
			if iteration == 0 {
				control |= byte(1 << streamID)
			}
			payload := append([]byte{control}, fakeCompactLength(len(compressed))...)
			payload = append(payload, compressed...)
			fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 4, 1), vncTight, payload))
			frame, updated := awaitVNCVersion(test, session, version+1)
			version = updated
			if pixel := frame.RGBAAt(3, 0); pixel.R != colorValue || pixel.G != 42 || pixel.B != 84 {
				test.Fatalf("Tight stream %d = %v", streamID, pixel)
			}
		}
	}
	var reset fakeRFBCompressor
	compressed := reset.chunk(test, bytes.Repeat([]byte{255, 128, 64}, 4))
	payload := append([]byte{1}, fakeCompactLength(len(compressed))...)
	payload = append(payload, compressed...)
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 4, 1), vncTight, payload))
	frame, _ := awaitVNCVersion(test, session, version+1)
	if pixel := frame.RGBAAt(0, 0); pixel.R != 255 {
		test.Fatalf("reset stream pixel = %v", pixel)
	}
}

func TestVNCTightFillPaletteGradientAndJPEG(test *testing.T) {
	for _, encoding := range []struct {
		name     string
		width    int
		payload  []byte
		expected color.RGBA
	}{
		{name: "fill", width: 9, payload: []byte{0x80, 17, 34, 51}, expected: color.RGBA{R: 17, G: 34, B: 51, A: 255}},
		{name: "palette", width: 9, payload: []byte{0x40, 1, 1, 255, 0, 0, 0, 255, 0, 0x80, 0x80}, expected: color.RGBA{G: 255, A: 255}},
		{name: "gradient", width: 2, payload: []byte{0x40, 2, 10, 20, 30, 5, 5, 5}, expected: color.RGBA{R: 15, G: 25, B: 35, A: 255}},
	} {
		test.Run(encoding.name, func(test *testing.T) {
			session, fake := startFakeRFB(test, encoding.width, 1, "password")
			fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, encoding.width, 1), vncTight, encoding.payload))
			frame, _ := awaitVNCVersion(test, session, 1)
			if pixel := frame.RGBAAt(encoding.width-1, 0); pixel != encoding.expected {
				test.Fatalf("Tight pixel = %v, want %v", pixel, encoding.expected)
			}
		})
	}
	test.Run("JPEG", func(test *testing.T) {
		session, fake := startFakeRFB(test, 4, 4, "password")
		frame := image.NewRGBA(image.Rect(0, 0, 4, 4))
		for vertical := range 4 {
			for horizontal := range 4 {
				frame.SetRGBA(horizontal, vertical, color.RGBA{R: 80, G: 160, B: 240, A: 255})
			}
		}
		var encoded bytes.Buffer
		if err := jpeg.Encode(&encoded, frame, &jpeg.Options{Quality: 100}); err != nil {
			test.Fatal(err)
		}
		payload := append([]byte{0x90}, fakeCompactLength(encoded.Len())...)
		payload = append(payload, encoded.Bytes()...)
		fakeRFBSend(test, fake, fakeRFBRectangle(frame.Rect, vncTight, payload))
		decoded, _ := awaitVNCVersion(test, session, 1)
		if pixel := decoded.RGBAAt(0, 0); pixel.R < 78 || pixel.R > 82 || pixel.G < 158 || pixel.G > 162 || pixel.B < 238 || pixel.B > 242 {
			test.Fatalf("JPEG pixel = %v", pixel)
		}
	})
}

func TestVNCWaitStableUsesPixelChanges(test *testing.T) {
	session, fake := startFakeRFB(test, 1, 1, "password")
	payload := fakeRFBPixels(1, 1, color.RGBA{R: 1, A: 255})
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 1, 1), vncRaw, payload))
	_, version := awaitVNCVersion(test, session, 1)
	if _, err := session.WaitStable(context.Background(), time.Hour, 10*time.Millisecond); !errors.Is(err, context.DeadlineExceeded) {
		test.Fatalf("stable timeout = %v", err)
	}
	stableVersion, err := session.WaitStable(context.Background(), 20*time.Millisecond, time.Second)
	if err != nil || stableVersion != version {
		test.Fatalf("stable version = %d, %v", stableVersion, err)
	}
	session.mu.Lock()
	lastChange := session.lastChange
	session.mu.Unlock()
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 1, 1), vncRaw, payload))
	awaitVNCVersion(test, session, version+1)
	session.mu.Lock()
	updated := session.lastChange
	session.mu.Unlock()
	if !updated.Equal(lastChange) {
		test.Fatal("identical pixel update restarted quiet interval")
	}
	if _, err := session.WaitStable(context.Background(), -time.Second, time.Second); err == nil {
		test.Fatal("accepted negative quiet interval")
	}
}

func TestVNCInputsAndUnicodeKeysyms(test *testing.T) {
	session, fake := startFakeRFB(test, 64, 32, "password")
	if err := session.Pointer(context.Background(), 63, 31, 5); err != nil {
		test.Fatal(err)
	}
	if message := awaitRFBCommand(test, fake, 5); !bytes.Equal(message, []byte{5, 5, 0, 63, 0, 31}) {
		test.Fatalf("pointer bytes = %v", message)
	}
	if err := session.Pointer(context.Background(), 64, 31, 0); err == nil {
		test.Fatal("accepted out-of-bounds pointer")
	}
	if err := session.Type(context.Background(), "Aé猫\n\t"); err != nil {
		test.Fatal(err)
	}
	for _, keysym := range []uint32{0x41, 0xe9, 0x0100732b, 0xff0d, 0xff09} {
		for _, down := range []byte{1, 0} {
			message := awaitRFBCommand(test, fake, 4)
			if message[1] != down || binary.BigEndian.Uint32(message[4:]) != keysym {
				test.Fatalf("key bytes = %v, want %x down %d", message, keysym, down)
			}
		}
	}
	if err := session.Key(context.Background(), 0xffe3, true); err != nil {
		test.Fatal(err)
	}
	if message := awaitRFBCommand(test, fake, 4); message[1] != 1 || binary.BigEndian.Uint32(message[4:]) != 0xffe3 {
		test.Fatalf("explicit key = %v", message)
	}
	if err := session.Type(context.Background(), string([]byte{0xff})); err == nil {
		test.Fatal("accepted invalid UTF-8")
	}
	if err := session.Type(context.Background(), strings.Repeat("x", vncMaxText+1)); err == nil {
		test.Fatal("accepted oversized typed text")
	}
}

func TestVNCResizeInvalidatesFullDiff(test *testing.T) {
	session, fake := startFakeRFB(test, 32, 32, "password")
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 32, 32), vncRaw, fakeRFBPixels(32, 32, color.RGBA{R: 1, A: 255})))
	_, version := awaitVNCVersion(test, session, 1)
	awaitRFBCommand(test, fake, 0)
	awaitRFBCommand(test, fake, 2)
	awaitRFBCommand(test, fake, 3)
	awaitRFBCommand(test, fake, 3)
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 33, 33), vncExtendedDesktopSize, []byte{1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 33, 0, 33, 0, 0, 0, 0}))
	request := awaitRFBCommand(test, fake, 3)
	if request[1] != 0 || binary.BigEndian.Uint16(request[6:]) != 33 || binary.BigEndian.Uint16(request[8:]) != 33 {
		test.Fatalf("resize request = %v", request)
	}
	fakeRFBSend(test, fake, fakeRFBRectangle(image.Rect(0, 0, 33, 33), vncRaw, fakeRFBPixels(33, 33, color.RGBA{A: 255})))
	frame, _ := awaitVNCVersion(test, session, version+2)
	if frame.Rect != image.Rect(0, 0, 33, 33) {
		test.Fatalf("resized framebuffer = %v", frame.Rect)
	}
	diff, err := session.Diff(context.Background(), version)
	if err != nil || len(diff.ChangedTiles) != 4 {
		test.Fatalf("resize diff = %+v, %v", diff, err)
	}
}

func TestVNCRejectsMalformedUpdates(test *testing.T) {
	for _, invalid := range []struct {
		name string
		data []byte
	}{
		{name: "out-of-bounds", data: fakeRFBRectangle(image.Rect(1, 0, 2, 1), vncRaw, nil)},
		{name: "unknown encoding", data: fakeRFBRectangle(image.Rect(0, 0, 1, 1), 99, nil)},
		{name: "oversized zlib", data: fakeRFBRectangle(image.Rect(0, 0, 1, 1), vncZlib, []byte{0xff, 0xff, 0xff, 0xff})},
		{name: "unknown Tight mode", data: fakeRFBRectangle(image.Rect(0, 0, 1, 1), vncTight, []byte{0xa0})},
		{name: "unknown Tight filter", data: fakeRFBRectangle(image.Rect(0, 0, 1, 1), vncTight, []byte{0x40, 3})},
		{name: "unknown message", data: []byte{99}},
	} {
		test.Run(invalid.name, func(test *testing.T) {
			session, fake := startFakeRFB(test, 1, 1, "password")
			fakeRFBSend(test, fake, invalid.data)
			select {
			case <-session.Done():
			case <-time.After(time.Second):
				test.Fatal("malformed stream was not closed")
			}
			if session.Err() == nil {
				test.Fatal("malformed stream did not record error")
			}
		})
	}
}

func TestVNCLifetimeAndIdleCloseTransport(test *testing.T) {
	test.Run("handshake cancellation", func(test *testing.T) {
		client, server := net.Pipe()
		defer server.Close()
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if session, err := NewVNCSession(ctx, client, "password"); err == nil {
			session.Close()
			test.Fatal("canceled handshake succeeded")
		}
		if _, err := server.Write([]byte{1}); err == nil {
			test.Fatal("cancellation left transport open")
		}
	})
	test.Run("idle expiration", func(test *testing.T) {
		session, _ := startFakeRFB(test, 1, 1, "password")
		session.mu.Lock()
		session.lastUse = time.Now().Add(-vncIdleTimeout - time.Second)
		session.mu.Unlock()
		session.expireIdle()
		if !errors.Is(session.Err(), errVNCIdle) {
			test.Fatalf("idle failure = %v", session.Err())
		}
		if err := session.Touch(); !errors.Is(err, errVNCIdle) {
			test.Fatalf("expired Touch = %v", err)
		}
		select {
		case <-session.readerDone:
		case <-time.After(time.Second):
			test.Fatal("idle close stranded reader")
		}
	})
	test.Run("active use postpones expiration", func(test *testing.T) {
		session, _ := startFakeRFB(test, 1, 1, "password")
		if err := session.Touch(); err != nil {
			test.Fatal(err)
		}
		session.expireIdle()
		if err := session.Err(); err != nil {
			test.Fatalf("active session closed: %v", err)
		}
	})
}

func TestVNCViewerControlFraming(test *testing.T) {
	session, fake := startFakeRFB(test, 32, 32, "password")
	clipboard := append([]byte{6, 0, 0, 0, 0, 0, 0, 5}, []byte("hello")...)
	if err := session.InputMessage(context.Background(), clipboard); err != nil {
		test.Fatal(err)
	}
	if message := awaitRFBCommand(test, fake, 6); !bytes.Equal(message, clipboard) {
		test.Fatalf("clipboard = %v", message)
	}
	layout := []byte{251, 0, 0, 32, 0, 32, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 32, 0, 32, 0, 0, 0, 0}
	if err := session.InputMessage(context.Background(), layout); err != nil {
		test.Fatal(err)
	}
	if message := awaitRFBCommand(test, fake, 251); !bytes.Equal(message, layout) {
		test.Fatalf("layout = %v", message)
	}
	for _, invalid := range [][]byte{{}, clipboard[:8], {6, 0, 0, 0, 0xff, 0xff, 0xff, 0xff}, {0, 0, 0, 0, 0, 0, 0, 0}, {250, 0, 1, 2}, layout[:8], {251, 0, 0, 32, 0, 32, 0, 0}} {
		if err := session.InputMessage(context.Background(), invalid); err == nil {
			test.Fatalf("accepted malformed control %v", invalid)
		}
	}
}
