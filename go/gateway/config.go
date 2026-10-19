package main

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
)

type config struct {
	databaseURL    string
	secretsKey     string
	previousKeys   []string
	jwtSecret      string
	apiURL         string
	nodeHost       string
	nodeDist       string
	peerName       string
	peerIP         string
	peerHTTPPort   int
	nodePort       int
	nodeListenPort int
	grpcPort       int
	httpPort       int
	macosBase      string
	windowsBase    string
}

func readConfig() (config, error) {
	settings := config{
		databaseURL: os.Getenv("DATABASE_URL"),
		secretsKey:  os.Getenv("ZOO_SECRETS_KEY"),
		jwtSecret:   os.Getenv("JWT_SECRET"),
		apiURL:      os.Getenv("ZOO_API_URL"),
		nodeHost:    os.Getenv("ZOO_NODE_HOST"),
		nodeDist:    os.Getenv("ZOO_NODE_DIST"),
		peerName:    os.Getenv("ZOO_GATEWAY_PEERS"),
		peerIP:      os.Getenv("ZOO_GATEWAY_IP"),
		macosBase:   environment("ZOO_MACOS_BASE", "zoo-macos-base"),
		windowsBase: environment("ZOO_WINDOWS_BASE", "zoo-windows-base"),
	}
	database, err := url.Parse(settings.databaseURL)
	if err != nil || database.Host == "" || database.Scheme != "postgres" && database.Scheme != "postgresql" {
		return config{}, fmt.Errorf("DATABASE_URL must be a PostgreSQL connection URL")
	}
	if settings.secretsKey == "" && settings.jwtSecret == "" {
		return config{}, fmt.Errorf("ZOO_SECRETS_KEY or JWT_SECRET is required for gateway authentication")
	}
	publicURL, err := url.Parse(settings.apiURL)
	if err != nil || publicURL.Hostname() == "" || publicURL.Scheme != "http" && publicURL.Scheme != "https" || publicURL.User != nil || publicURL.RawQuery != "" || publicURL.Fragment != "" {
		return config{}, fmt.Errorf("ZOO_API_URL must be the public HTTP(S) API URL")
	}
	if settings.nodeHost == "" {
		settings.nodeHost = publicURL.Hostname()
	}
	if net.ParseIP(settings.nodeHost) == nil {
		parsed, err := url.Parse("https://" + settings.nodeHost)
		if err != nil || parsed.Hostname() != settings.nodeHost || parsed.Port() != "" || parsed.Path != "" || parsed.User != nil {
			return config{}, fmt.Errorf("ZOO_NODE_HOST must be a hostname or IP address without a port")
		}
	}
	if settings.nodeDist == "" {
		settings.nodeDist = "node/dist"
	}
	if settings.peerName == "" {
		settings.peerName = "gateway"
	}
	settings.previousKeys = strings.Split(os.Getenv("ZOO_SECRETS_KEY_PREVIOUS"), ",")
	settings.nodePort, err = port("ZOO_NODE_PORT", 7443)
	if err != nil {
		return config{}, err
	}
	settings.nodeListenPort, err = port("ZOO_GATEWAY_NODE_PORT", 7443)
	if err != nil {
		return config{}, err
	}
	settings.grpcPort, err = port("ZOO_GATEWAY_GRPC_PORT", 7444)
	if err != nil {
		return config{}, err
	}
	settings.httpPort, err = port("ZOO_GATEWAY_HTTP_PORT", 8001)
	if err != nil {
		return config{}, err
	}
	settings.peerHTTPPort, err = port("ZOO_GATEWAY_PEER_HTTP_PORT", 8002)
	if err != nil {
		return config{}, err
	}
	return settings, nil
}

func port(name string, defaultPort int) (int, error) {
	value, exists := os.LookupEnv(name)
	if !exists {
		return defaultPort, nil
	}
	number, err := strconv.Atoi(value)
	if err != nil || number < 1 || number > 65535 {
		return 0, fmt.Errorf("%s must be a port between 1 and 65535", name)
	}
	return number, nil
}

func environment(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
