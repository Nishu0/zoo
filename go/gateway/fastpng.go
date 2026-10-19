package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"image"
	"time"

	"github.com/klauspost/compress/zlib"
)

func fastPNG(frame *image.RGBA) ([]byte, error) {
	if frame == nil || frame.Bounds().Empty() {
		return nil, errors.New("PNG requires a nonempty framebuffer")
	}
	started := time.Now()
	defer func() { observeScreenEncode(time.Since(started)) }()
	var compressed bytes.Buffer
	writer, err := zlib.NewWriterLevel(&compressed, 1)
	if err != nil {
		return nil, err
	}
	width, height := frame.Bounds().Dx(), frame.Bounds().Dy()
	row := make([]byte, 1+width*3)
	row[0] = 1
	for vertical := range height {
		previous := [3]byte{}
		for horizontal := range width {
			pixel := frame.PixOffset(frame.Rect.Min.X+horizontal, frame.Rect.Min.Y+vertical)
			for component := range 3 {
				value := frame.Pix[pixel+component]
				row[1+horizontal*3+component] = value - previous[component]
				previous[component] = value
			}
		}
		if _, err := writer.Write(row); err != nil {
			writer.Close()
			return nil, err
		}
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}
	var result bytes.Buffer
	result.WriteString("\x89PNG\r\n\x1a\n")
	header := make([]byte, 13)
	binary.BigEndian.PutUint32(header, uint32(width))
	binary.BigEndian.PutUint32(header[4:], uint32(height))
	header[8], header[9] = 8, 2
	writePNGChunk(&result, "IHDR", header)
	writePNGChunk(&result, "IDAT", compressed.Bytes())
	writePNGChunk(&result, "IEND", nil)
	return result.Bytes(), nil
}

func writePNGChunk(destination *bytes.Buffer, kind string, data []byte) {
	var encoded [4]byte
	binary.BigEndian.PutUint32(encoded[:], uint32(len(data)))
	destination.Write(encoded[:])
	destination.WriteString(kind)
	destination.Write(data)
	checksum := crc32.Update(0, crc32.IEEETable, []byte(kind))
	checksum = crc32.Update(checksum, crc32.IEEETable, data)
	binary.BigEndian.PutUint32(encoded[:], checksum)
	destination.Write(encoded[:])
}
