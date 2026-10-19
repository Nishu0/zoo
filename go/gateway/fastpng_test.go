package main

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"hash/crc32"
	"image"
	"image/color"
	"image/png"
	"io"
	"testing"
)

func TestFastPNGSubFilterAndRoundTrip(test *testing.T) {
	frame := image.NewRGBA(image.Rect(2, 3, 5, 5))
	for vertical := frame.Rect.Min.Y; vertical < frame.Rect.Max.Y; vertical++ {
		for horizontal := frame.Rect.Min.X; horizontal < frame.Rect.Max.X; horizontal++ {
			frame.SetRGBA(horizontal, vertical, color.RGBA{R: byte(horizontal * 30), G: byte(vertical * 20), B: byte(horizontal + vertical), A: 255})
		}
	}
	encoded, err := fastPNG(frame)
	if err != nil {
		test.Fatal(err)
	}
	decoded, err := png.Decode(bytes.NewReader(encoded))
	if err != nil {
		test.Fatal(err)
	}
	for vertical := range frame.Rect.Dy() {
		for horizontal := range frame.Rect.Dx() {
			expected := frame.RGBAAt(frame.Rect.Min.X+horizontal, frame.Rect.Min.Y+vertical)
			red, green, blue, alpha := decoded.At(horizontal, vertical).RGBA()
			if red != uint32(expected.R)*257 || green != uint32(expected.G)*257 || blue != uint32(expected.B)*257 || alpha != 65535 {
				test.Fatalf("decoded pixel %d,%d does not match", horizontal, vertical)
			}
		}
	}
	var compressed bytes.Buffer
	for offset := 8; offset < len(encoded); {
		length := int(binary.BigEndian.Uint32(encoded[offset:]))
		kind := encoded[offset+4 : offset+8]
		data := encoded[offset+8 : offset+8+length]
		checksum := crc32.ChecksumIEEE(encoded[offset+4 : offset+8+length])
		if checksum != binary.BigEndian.Uint32(encoded[offset+8+length:]) {
			test.Fatal("invalid PNG chunk checksum")
		}
		if string(kind) == "IDAT" {
			compressed.Write(data)
		}
		offset += length + 12
	}
	reader, err := zlib.NewReader(&compressed)
	if err != nil {
		test.Fatal(err)
	}
	defer reader.Close()
	rows, err := io.ReadAll(reader)
	if err != nil {
		test.Fatal(err)
	}
	stride := 1 + frame.Rect.Dx()*3
	if len(rows) != stride*frame.Rect.Dy() {
		test.Fatalf("PNG decompressed length = %d", len(rows))
	}
	for vertical := range frame.Rect.Dy() {
		if rows[vertical*stride] != 1 {
			test.Fatalf("row %d does not use Sub filter", vertical)
		}
		previous := [3]byte{}
		for horizontal := range frame.Rect.Dx() {
			pixel := frame.PixOffset(frame.Rect.Min.X+horizontal, frame.Rect.Min.Y+vertical)
			for component := range 3 {
				actual := rows[vertical*stride+1+horizontal*3+component] + previous[component]
				if actual != frame.Pix[pixel+component] {
					test.Fatal("invalid Sub predictor")
				}
				previous[component] = actual
			}
		}
	}
}

func TestFastPNGRejectsEmptyFrame(test *testing.T) {
	for _, frame := range []*image.RGBA{nil, image.NewRGBA(image.Rectangle{})} {
		if _, err := fastPNG(frame); err == nil {
			test.Fatal("accepted empty framebuffer")
		}
	}
}
