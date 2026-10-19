package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"strings"
	"time"
)

func envelopeParts(value string) (string, string, error) {
	parts := strings.SplitN(value, ":", 3)
	if len(parts) != 3 || parts[0] != "zk1" || parts[1] == "" || parts[2] == "" {
		return "", "", errors.New("invalid secret envelope")
	}
	return parts[1], parts[2], nil
}

func encryptFernet(plaintext, encodedKey []byte) (string, error) {
	key, err := base64.URLEncoding.DecodeString(string(encodedKey))
	if err != nil || len(key) != 32 {
		return "", errors.New("invalid Fernet key")
	}
	padding := aes.BlockSize - len(plaintext)%aes.BlockSize
	padded := make([]byte, len(plaintext)+padding)
	copy(padded, plaintext)
	for index := len(plaintext); index < len(padded); index++ {
		padded[index] = byte(padding)
	}
	frame := make([]byte, 25+len(padded)+sha256.Size)
	frame[0] = 0x80
	binary.BigEndian.PutUint64(frame[1:9], uint64(time.Now().Unix()))
	if _, err := rand.Read(frame[9:25]); err != nil {
		return "", err
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return "", errors.New("invalid Fernet key")
	}
	cipher.NewCBCEncrypter(block, frame[9:25]).CryptBlocks(frame[25:len(frame)-sha256.Size], padded)
	digest := hmac.New(sha256.New, key[:16])
	digest.Write(frame[:len(frame)-sha256.Size])
	copy(frame[len(frame)-sha256.Size:], digest.Sum(nil))
	return base64.URLEncoding.EncodeToString(frame), nil
}

func decryptFernet(token string, encodedKey []byte) ([]byte, error) {
	key, err := base64.URLEncoding.DecodeString(string(encodedKey))
	if err != nil || len(key) != 32 {
		return nil, errors.New("invalid Fernet key")
	}
	frame, err := base64.URLEncoding.DecodeString(token)
	if err != nil || len(frame) < 73 || frame[0] != 0x80 {
		return nil, errors.New("invalid Fernet token")
	}
	signed, signature := frame[:len(frame)-sha256.Size], frame[len(frame)-sha256.Size:]
	digest := hmac.New(sha256.New, key[:16])
	digest.Write(signed)
	if !hmac.Equal(digest.Sum(nil), signature) {
		return nil, errors.New("invalid Fernet token")
	}
	encrypted := signed[25:]
	if len(encrypted) == 0 || len(encrypted)%aes.BlockSize != 0 {
		return nil, errors.New("invalid Fernet token")
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return nil, errors.New("invalid Fernet key")
	}
	plaintext := make([]byte, len(encrypted))
	cipher.NewCBCDecrypter(block, signed[9:25]).CryptBlocks(plaintext, encrypted)
	padding := int(plaintext[len(plaintext)-1])
	if padding < 1 || padding > aes.BlockSize || padding > len(plaintext) {
		return nil, errors.New("invalid Fernet token")
	}
	for _, value := range plaintext[len(plaintext)-padding:] {
		if int(value) != padding {
			return nil, errors.New("invalid Fernet token")
		}
	}
	return plaintext[:len(plaintext)-padding], nil
}

func unwrapLocalKey(wrapped string, materials []string) ([]byte, error) {
	if !strings.HasPrefix(wrapped, "local:") {
		return nil, errors.New("data key is not locally wrapped")
	}
	for _, material := range materials {
		if material == "" {
			continue
		}
		digest := sha256.Sum256([]byte(material))
		encoded := []byte(base64.URLEncoding.EncodeToString(digest[:]))
		if key, err := decryptFernet(strings.TrimPrefix(wrapped, "local:"), encoded); err == nil {
			decoded, failure := base64.URLEncoding.DecodeString(string(key))
			if failure != nil || len(decoded) != 32 {
				return nil, errors.New("invalid unwrapped data key")
			}
			return key, nil
		}
	}
	return nil, errors.New("cannot unwrap data key with the configured secrets keys")
}
