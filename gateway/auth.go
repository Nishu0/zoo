package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func (settings config) signingKey() []byte {
	if settings.secretsKey != "" {
		return []byte(settings.secretsKey)
	}
	return []byte(settings.jwtSecret)
}

func (settings config) token(scope string) string {
	digest := hmac.New(sha256.New, settings.signingKey())
	digest.Write([]byte(scope))
	return hex.EncodeToString(digest.Sum(nil))
}

func (settings config) authorized(ctx context.Context) bool {
	headers, found := metadata.FromIncomingContext(ctx)
	if !found {
		return false
	}
	values := headers.Get("authorization")
	return len(values) == 1 && hmac.Equal([]byte(values[0]), []byte("Bearer "+settings.token("zoo-gateway")))
}

func (settings config) unaryAuth(
	ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler,
) (any, error) {
	if !settings.authorized(ctx) {
		return nil, status.Error(codes.Unauthenticated, "invalid gateway token")
	}
	return handler(ctx, request)
}

func (settings config) streamAuth(
	service any, stream grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler,
) error {
	if !settings.authorized(stream.Context()) {
		return status.Error(codes.Unauthenticated, "invalid gateway token")
	}
	return handler(service, stream)
}
