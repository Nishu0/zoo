package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"image/jpeg"
	"io"

	"github.com/klauspost/compress/zlib"
)

type vncInflater struct {
	source bytes.Buffer
	reader io.ReadCloser
}

func (stream *vncInflater) Decode(compressed []byte, length int) ([]byte, error) {
	if len(compressed) == 0 || len(compressed) > vncMaxPayload || length <= 0 || length > vncMaxPayload || stream.source.Len()+len(compressed) > vncMaxPayload {
		return nil, errors.New("VNC compressed payload exceeds limit or is empty")
	}
	stream.source.Write(compressed)
	if stream.reader == nil {
		reader, err := zlib.NewReader(&stream.source)
		if err != nil {
			return nil, fmt.Errorf("VNC zlib header: %w", err)
		}
		stream.reader = reader
	}
	decoded := make([]byte, length)
	if _, err := io.ReadFull(stream.reader, decoded); err != nil {
		return nil, fmt.Errorf("VNC zlib pixels: %w", err)
	}
	remaining := bytes.Clone(stream.source.Bytes())
	stream.source = *bytes.NewBuffer(remaining)
	return decoded, nil
}

func (stream *vncInflater) Close() {
	if stream.reader != nil {
		stream.reader.Close()
		stream.reader = nil
	}
	stream.source = bytes.Buffer{}
}

func (session *VNCSession) decodeRectangle(rectangle image.Rectangle, encoding int32) error {
	switch encoding {
	case vncRaw:
		pixels, err := session.readBytes(rectangle.Dx() * rectangle.Dy() * 4)
		if err != nil {
			return err
		}
		vncBGRToRGBA(pixels)
		session.applyPixels(rectangle, pixels)
	case vncCopyRect:
		coordinates, err := session.readBytes(4)
		if err != nil {
			return err
		}
		return session.copyRectangle(rectangle, int(binary.BigEndian.Uint16(coordinates)), int(binary.BigEndian.Uint16(coordinates[2:])))
	case vncZlib:
		header, err := session.readBytes(4)
		if err != nil {
			return err
		}
		length := binary.BigEndian.Uint32(header)
		if length > vncMaxPayload {
			return errors.New("VNC zlib payload exceeds limit")
		}
		compressed, err := session.readBytes(int(length))
		if err != nil {
			return err
		}
		pixels, err := session.zlib.Decode(compressed, rectangle.Dx()*rectangle.Dy()*4)
		if err != nil {
			return err
		}
		vncBGRToRGBA(pixels)
		session.applyPixels(rectangle, pixels)
	case vncTight:
		pixels, err := session.decodeTight(rectangle)
		if err != nil {
			return err
		}
		session.applyPixels(rectangle, pixels)
	default:
		return fmt.Errorf("unsupported VNC encoding %d", encoding)
	}
	return nil
}

func vncBGRToRGBA(pixels []byte) {
	for index := 0; index < len(pixels); index += 4 {
		pixels[index], pixels[index+2], pixels[index+3] = pixels[index+2], pixels[index], 255
	}
}

func (session *VNCSession) copyRectangle(destination image.Rectangle, horizontal, vertical int) error {
	session.mu.Lock()
	defer session.mu.Unlock()
	source := image.Rect(horizontal, vertical, horizontal+destination.Dx(), vertical+destination.Dy())
	if !source.In(session.frame.Rect) {
		return errors.New("VNC CopyRect source outside framebuffer")
	}
	first, last, step := 0, destination.Dy(), 1
	if destination.Min.Y > source.Min.Y {
		first, last, step = destination.Dy()-1, -1, -1
	}
	for row := first; row != last; row += step {
		from := session.frame.PixOffset(source.Min.X, source.Min.Y+row)
		to := session.frame.PixOffset(destination.Min.X, destination.Min.Y+row)
		copy(session.frame.Pix[to:to+destination.Dx()*4], session.frame.Pix[from:from+source.Dx()*4])
	}
	session.updateTilesLocked(destination)
	return nil
}

func (session *VNCSession) readCompactLength() (int, error) {
	first, err := session.readBytes(1)
	if err != nil {
		return 0, err
	}
	length := int(first[0] & 0x7f)
	if first[0]&0x80 == 0 {
		return length, nil
	}
	second, err := session.readBytes(1)
	if err != nil {
		return 0, err
	}
	length |= int(second[0]&0x7f) << 7
	if second[0]&0x80 == 0 {
		return length, nil
	}
	third, err := session.readBytes(1)
	if err != nil {
		return 0, err
	}
	return length | int(third[0])<<14, nil
}

func (session *VNCSession) decodeTight(rectangle image.Rectangle) ([]byte, error) {
	if rectangle.Dx() > 2048 {
		return nil, errors.New("VNC Tight rectangle width exceeds 2048")
	}
	control, err := session.readBytes(1)
	if err != nil {
		return nil, err
	}
	for index := range session.tight {
		if control[0]&(1<<index) != 0 {
			session.tight[index].Close()
		}
	}
	mode := control[0] >> 4
	count := rectangle.Dx() * rectangle.Dy()
	if mode == 8 {
		color, err := session.readBytes(3)
		if err != nil {
			return nil, err
		}
		pixels := make([]byte, count*4)
		for index := 0; index < len(pixels); index += 4 {
			copy(pixels[index:index+3], color)
			pixels[index+3] = 255
		}
		return pixels, nil
	}
	if mode == 9 {
		return session.decodeTightJPEG(rectangle)
	}
	if mode > 9 {
		return nil, fmt.Errorf("unsupported VNC Tight mode %d", mode)
	}
	filter := byte(0)
	if mode&4 != 0 {
		value, err := session.readBytes(1)
		if err != nil {
			return nil, err
		}
		filter = value[0]
	}
	length := count * 3
	var palette []byte
	paletteSize := 0
	if filter == 1 {
		colors, err := session.readBytes(1)
		if err != nil {
			return nil, err
		}
		paletteSize = int(colors[0]) + 1
		palette, err = session.readBytes(paletteSize * 3)
		if err != nil {
			return nil, err
		}
		length = count
		if paletteSize == 2 {
			length = ((rectangle.Dx() + 7) / 8) * rectangle.Dy()
		}
	} else if filter > 2 {
		return nil, fmt.Errorf("unsupported VNC Tight filter %d", filter)
	}
	var decoded []byte
	if length < 12 {
		decoded, err = session.readBytes(length)
	} else {
		var compressedLength int
		compressedLength, err = session.readCompactLength()
		if err != nil {
			return nil, err
		}
		var compressed []byte
		compressed, err = session.readBytes(compressedLength)
		if err != nil {
			return nil, err
		}
		decoded, err = session.tight[mode&3].Decode(compressed, length)
	}
	if err != nil {
		return nil, err
	}
	pixels := make([]byte, count*4)
	if filter == 1 {
		for vertical := range rectangle.Dy() {
			for horizontal := range rectangle.Dx() {
				index := vertical*rectangle.Dx() + horizontal
				var colorIndex int
				if paletteSize == 2 {
					colorIndex = int(decoded[vertical*((rectangle.Dx()+7)/8)+horizontal/8]>>uint(7-horizontal%8)) & 1
				} else {
					colorIndex = int(decoded[index])
				}
				if colorIndex >= paletteSize {
					return nil, errors.New("VNC Tight palette index outside palette")
				}
				copy(pixels[index*4:index*4+3], palette[colorIndex*3:colorIndex*3+3])
				pixels[index*4+3] = 255
			}
		}
		return pixels, nil
	}
	for index := range count {
		copy(pixels[index*4:index*4+3], decoded[index*3:index*3+3])
		pixels[index*4+3] = 255
	}
	if filter == 2 {
		for vertical := range rectangle.Dy() {
			for horizontal := range rectangle.Dx() {
				pixel := (vertical*rectangle.Dx() + horizontal) * 4
				for component := range 3 {
					left, above, diagonal := 0, 0, 0
					if horizontal > 0 {
						left = int(pixels[pixel-4+component])
					}
					if vertical > 0 {
						above = int(pixels[pixel-rectangle.Dx()*4+component])
					}
					if horizontal > 0 && vertical > 0 {
						diagonal = int(pixels[pixel-rectangle.Dx()*4-4+component])
					}
					prediction := min(255, max(0, left+above-diagonal))
					pixels[pixel+component] += byte(prediction)
				}
			}
		}
	}
	return pixels, nil
}

func (session *VNCSession) decodeTightJPEG(rectangle image.Rectangle) ([]byte, error) {
	length, err := session.readCompactLength()
	if err != nil {
		return nil, err
	}
	encoded, err := session.readBytes(length)
	if err != nil {
		return nil, err
	}
	configuration, err := jpeg.DecodeConfig(bytes.NewReader(encoded))
	if err != nil {
		return nil, err
	}
	if configuration.Width != rectangle.Dx() || configuration.Height != rectangle.Dy() {
		return nil, errors.New("VNC Tight JPEG dimensions differ from rectangle")
	}
	decoded, err := jpeg.Decode(bytes.NewReader(encoded))
	if err != nil {
		return nil, err
	}
	pixels := make([]byte, rectangle.Dx()*rectangle.Dy()*4)
	for vertical := range rectangle.Dy() {
		for horizontal := range rectangle.Dx() {
			red, green, blue, _ := decoded.At(decoded.Bounds().Min.X+horizontal, decoded.Bounds().Min.Y+vertical).RGBA()
			pixel := (vertical*rectangle.Dx() + horizontal) * 4
			pixels[pixel], pixels[pixel+1], pixels[pixel+2], pixels[pixel+3] = byte(red>>8), byte(green>>8), byte(blue>>8), 255
		}
	}
	return pixels, nil
}
