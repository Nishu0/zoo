package main

import (
	"bytes"
	"context"
	"crypto"
	"crypto/hmac"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	kmsTimeout       = 10 * time.Second
	kmsResponseLimit = 1 << 20
	kmsIdentityLimit = 64 << 10
	kmsWrappedLimit  = 128 << 10
	gcpKMSScope      = "https://www.googleapis.com/auth/cloudkms"
	gcpMetadataToken = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)

var (
	kmsAWSRegion    = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}[a-z0-9]$`)
	kmsResourceName = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
	nativeKMS       = &kmsClient{
		http: &http.Client{Timeout: kmsTimeout, CheckRedirect: func(request *http.Request, previous []*http.Request) error { return http.ErrUseLastResponse }},
		env:  os.Getenv, now: time.Now,
	}
)

type kmsAWSCredentials struct {
	access, secret, token string
	expires               time.Time
}

type kmsClient struct {
	http      *http.Client
	env       func(string) string
	now       func() time.Time
	mu        sync.Mutex
	awsSource string
	awsCached kmsAWSCredentials
	gcpSource string
	gcpAccess string
	gcpExpiry time.Time
}

func unwrapDataKey(ctx context.Context, wrapped string, materials []string) ([]byte, error) {
	return nativeKMS.unwrap(ctx, wrapped, materials)
}

func wrapDataKey(ctx context.Context, dataKey []byte) (string, error) {
	return nativeKMS.wrap(ctx, dataKey)
}

func validateKMSDataKey(key []byte) error {
	if len(key) != 44 {
		return errors.New("invalid Fernet data key")
	}
	decoded, err := base64.URLEncoding.Strict().DecodeString(string(key))
	if err != nil || len(decoded) != 32 {
		return errors.New("invalid Fernet data key")
	}
	return nil
}

func (client *kmsClient) wrap(ctx context.Context, dataKey []byte) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if err := validateKMSDataKey(dataKey); err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, kmsTimeout)
	defer cancel()
	setting := strings.TrimSpace(client.env("ZOO_KMS"))
	if len(setting) > kmsWrappedLimit {
		return "", errors.New("KMS wrapping configuration exceeds limit")
	}
	if setting == "" {
		material := client.env("ZOO_SECRETS_KEY")
		if material == "" {
			return "", errors.New("local key wrapping requires ZOO_SECRETS_KEY")
		}
		digest := sha256.Sum256([]byte(material))
		encoded, err := encryptFernet(dataKey, []byte(base64.URLEncoding.EncodeToString(digest[:])))
		if err != nil {
			return "", errors.New("local data key wrapping failed")
		}
		return "local:" + encoded, nil
	}
	provider, key, found := strings.Cut(setting, ":")
	if !found || key == "" {
		return "", errors.New("invalid KMS wrapping configuration")
	}
	plaintext := base64.StdEncoding.EncodeToString(dataKey)
	switch provider {
	case "aws":
		region, err := client.awsRegion(key)
		if err != nil {
			return "", err
		}
		response, err := client.aws(ctx, "Encrypt", region, map[string]string{"KeyId": key, "Plaintext": plaintext})
		if err != nil {
			return "", err
		}
		blob, err := kmsBase64Field(response, "CiphertextBlob")
		if err != nil {
			return "", err
		}
		return "aws:" + region + ":" + blob, nil
	case "gcp":
		response, err := client.gcp(ctx, key, "encrypt", map[string]string{"plaintext": plaintext})
		if err != nil {
			return "", err
		}
		blob, err := kmsBase64Field(response, "ciphertext")
		if err != nil {
			return "", err
		}
		return "gcp:" + key + ":" + blob, nil
	case "vault":
		response, err := client.vault(ctx, key, "encrypt", map[string]string{"plaintext": plaintext})
		if err != nil {
			return "", err
		}
		blob, err := kmsStringField(response, "ciphertext")
		if err != nil || !strings.HasPrefix(blob, "vault:v") {
			return "", errors.New("invalid Vault ciphertext response")
		}
		return "vault:" + key + ":" + blob, nil
	default:
		return "", errors.New("unsupported KMS wrapping provider")
	}
}

func (client *kmsClient) unwrap(ctx context.Context, wrapped string, materials []string) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if len(wrapped) == 0 || len(wrapped) > kmsWrappedLimit {
		return nil, errors.New("invalid wrapped data key")
	}
	provider, rest, found := strings.Cut(wrapped, ":")
	if !found || rest == "" {
		return nil, errors.New("invalid wrapped data key")
	}
	if provider == "local" {
		key, err := unwrapLocalKey(wrapped, materials)
		if err != nil {
			return nil, err
		}
		if err := validateKMSDataKey(key); err != nil {
			return nil, err
		}
		return key, nil
	}
	where, ciphertext, found := strings.Cut(rest, ":")
	if !found || where == "" || ciphertext == "" {
		return nil, errors.New("invalid wrapped data key")
	}
	ctx, cancel := context.WithTimeout(ctx, kmsTimeout)
	defer cancel()
	var response map[string]json.RawMessage
	var err error
	field := "plaintext"
	switch provider {
	case "aws":
		if !kmsAWSRegion.MatchString(where) {
			return nil, errors.New("invalid AWS KMS region")
		}
		response, err = client.aws(ctx, "Decrypt", where, map[string]string{"CiphertextBlob": ciphertext})
		field = "Plaintext"
	case "gcp":
		response, err = client.gcp(ctx, where, "decrypt", map[string]string{"ciphertext": ciphertext})
	case "vault":
		response, err = client.vault(ctx, where, "decrypt", map[string]string{"ciphertext": ciphertext})
	default:
		return nil, errors.New("unsupported data key wrapping provider")
	}
	if err != nil {
		return nil, err
	}
	encoded, err := kmsBase64Field(response, field)
	if err != nil {
		return nil, err
	}
	key, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || validateKMSDataKey(key) != nil {
		return nil, errors.New("KMS returned an invalid Fernet data key")
	}
	return key, nil
}

func kmsStringField(response map[string]json.RawMessage, name string) (string, error) {
	var value string
	if json.Unmarshal(response[name], &value) != nil || value == "" || len(value) > kmsWrappedLimit {
		return "", errors.New("invalid KMS response")
	}
	return value, nil
}

func kmsBase64Field(response map[string]json.RawMessage, name string) (string, error) {
	value, err := kmsStringField(response, name)
	if err != nil {
		return "", err
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(value)
	if err != nil || len(decoded) == 0 {
		return "", errors.New("invalid KMS base64 response")
	}
	return value, nil
}

func (client *kmsClient) request(ctx context.Context, method, address, operation string, payload []byte, headers map[string]string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, method, address, bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("invalid %s request configuration", operation)
	}
	for name, value := range headers {
		if len(value) > kmsIdentityLimit || strings.ContainsAny(value, "\r\n") {
			return nil, fmt.Errorf("invalid %s request headers", operation)
		}
		if strings.EqualFold(name, "host") {
			request.Host = value
		} else {
			request.Header.Set(name, value)
		}
	}
	response, err := client.http.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, fmt.Errorf("%s request failed", operation)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s request failed (HTTP %d)", operation, response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, kmsResponseLimit+1))
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, fmt.Errorf("%s response could not be read", operation)
	}
	if len(body) > kmsResponseLimit {
		return nil, fmt.Errorf("%s response exceeds limit", operation)
	}
	return body, nil
}

func (client *kmsClient) requestJSON(ctx context.Context, address, operation string, body map[string]string, headers map[string]string) (map[string]json.RawMessage, error) {
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, errors.New("invalid KMS request")
	}
	response, err := client.request(ctx, http.MethodPost, address, operation, payload, headers)
	if err != nil {
		return nil, err
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(response, &fields) != nil || fields == nil {
		return nil, fmt.Errorf("invalid %s JSON response", operation)
	}
	return fields, nil
}

func kmsReadIdentity(path, operation string) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("%s credential file could not be read", operation)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > kmsIdentityLimit {
		return nil, fmt.Errorf("invalid %s credential file", operation)
	}
	body, err := io.ReadAll(io.LimitReader(file, kmsIdentityLimit+1))
	if err != nil || len(body) == 0 || len(body) > kmsIdentityLimit {
		return nil, fmt.Errorf("invalid %s credential file", operation)
	}
	return body, nil
}

func kmsURL(address string, requireTLS bool) (*url.URL, error) {
	parsed, err := url.Parse(address)
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" ||
		(parsed.Scheme != "https" && (requireTLS || parsed.Scheme != "http")) {
		return nil, errors.New("invalid KMS credential endpoint")
	}
	return parsed, nil
}

func (client *kmsClient) awsRegion(key string) (string, error) {
	region := client.env("AWS_REGION")
	if region == "" {
		region = client.env("AWS_DEFAULT_REGION")
	}
	if strings.HasPrefix(key, "arn:") {
		parts := strings.SplitN(key, ":", 6)
		if len(parts) != 6 || parts[2] != "kms" {
			return "", errors.New("invalid AWS KMS key ARN")
		}
		region = parts[3]
	}
	if !kmsAWSRegion.MatchString(region) {
		return "", errors.New("AWS KMS requires a valid ARN region or AWS_REGION/AWS_DEFAULT_REGION")
	}
	return region, nil
}

func kmsSigV4(method, host, path string, headers map[string]string, payload []byte, region, service, access, secret, date string) string {
	canonicalHeaders := make(map[string]string, len(headers))
	for name, value := range headers {
		canonicalHeaders[strings.ToLower(name)] = strings.TrimSpace(value)
	}
	names := make([]string, 0, len(canonicalHeaders))
	for name := range canonicalHeaders {
		names = append(names, name)
	}
	sort.Strings(names)
	var block strings.Builder
	for _, name := range names {
		block.WriteString(name + ":" + canonicalHeaders[name] + "\n")
	}
	signed := strings.Join(names, ";")
	payloadHash := sha256.Sum256(payload)
	canonical := strings.Join([]string{method, path, "", block.String(), signed, hex.EncodeToString(payloadHash[:])}, "\n")
	scope := date[:8] + "/" + region + "/" + service + "/aws4_request"
	canonicalHash := sha256.Sum256([]byte(canonical))
	toSign := strings.Join([]string{"AWS4-HMAC-SHA256", date, scope, hex.EncodeToString(canonicalHash[:])}, "\n")
	key := []byte("AWS4" + secret)
	for _, part := range []string{date[:8], region, service, "aws4_request"} {
		digest := hmac.New(sha256.New, key)
		digest.Write([]byte(part))
		key = digest.Sum(nil)
	}
	digest := hmac.New(sha256.New, key)
	digest.Write([]byte(toSign))
	return "AWS4-HMAC-SHA256 Credential=" + access + "/" + scope + ", SignedHeaders=" + signed + ", Signature=" + hex.EncodeToString(digest.Sum(nil))
}

func (client *kmsClient) aws(ctx context.Context, action, region string, body map[string]string) (map[string]json.RawMessage, error) {
	credentials, err := client.awsCredentials(ctx)
	if err != nil {
		return nil, err
	}
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, errors.New("invalid AWS KMS request")
	}
	host := "kms." + region + ".amazonaws.com"
	date := client.now().UTC().Format("20060102T150405Z")
	headers := map[string]string{"content-type": "application/x-amz-json-1.1", "host": host, "x-amz-date": date, "x-amz-target": "TrentService." + action}
	if credentials.token != "" {
		headers["x-amz-security-token"] = credentials.token
	}
	headers["authorization"] = kmsSigV4(http.MethodPost, host, "/", headers, payload, region, "kms", credentials.access, credentials.secret, date)
	response, err := client.request(ctx, http.MethodPost, "https://"+host+"/", "AWS KMS "+action, payload, headers)
	if err != nil {
		return nil, err
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(response, &fields) != nil || fields == nil {
		return nil, errors.New("invalid AWS KMS JSON response")
	}
	return fields, nil
}

func (client *kmsClient) awsCredentials(ctx context.Context) (kmsAWSCredentials, error) {
	access, secret := client.env("AWS_ACCESS_KEY_ID"), client.env("AWS_SECRET_ACCESS_KEY")
	if access != "" || secret != "" {
		if access == "" || secret == "" {
			return kmsAWSCredentials{}, errors.New("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be configured")
		}
		return kmsAWSCredentials{access: access, secret: secret, token: client.env("AWS_SESSION_TOKEN")}, nil
	}
	role, identity := client.env("AWS_ROLE_ARN"), client.env("AWS_WEB_IDENTITY_TOKEN_FILE")
	container := client.env("AWS_CONTAINER_CREDENTIALS_FULL_URI")
	var source string
	if role != "" || identity != "" {
		if role == "" || identity == "" {
			return kmsAWSCredentials{}, errors.New("AWS_ROLE_ARN and AWS_WEB_IDENTITY_TOKEN_FILE must both be configured")
		}
		source = strings.Join([]string{"web", role, identity, client.env("AWS_ROLE_SESSION_NAME"), client.env("AWS_REGION"), client.env("AWS_DEFAULT_REGION")}, "\x00")
	} else if container != "" {
		if _, err := kmsURL(container, false); err != nil {
			return kmsAWSCredentials{}, errors.New("invalid AWS container credential endpoint")
		}
		if client.env("AWS_CONTAINER_AUTHORIZATION_TOKEN") != "" {
			return kmsAWSCredentials{}, errors.New("AWS container authorization requires AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE")
		}
		source = "container\x00" + container + "\x00" + client.env("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE")
	} else {
		return kmsAWSCredentials{}, errors.New("AWS KMS requires explicit AWS credentials, web identity, or a full container credential URI")
	}
	client.mu.Lock()
	if client.awsSource == source && client.now().Before(client.awsCached.expires) {
		cached := client.awsCached
		client.mu.Unlock()
		return cached, nil
	}
	client.mu.Unlock()
	var credentials kmsAWSCredentials
	var err error
	if role != "" {
		credentials, err = client.awsWebIdentity(ctx, role, identity)
	} else {
		credentials, err = client.awsContainer(ctx, container)
	}
	if err != nil {
		return kmsAWSCredentials{}, err
	}
	client.mu.Lock()
	client.awsSource, client.awsCached = source, credentials
	client.mu.Unlock()
	return credentials, nil
}

func (client *kmsClient) awsWebIdentity(ctx context.Context, role, path string) (kmsAWSCredentials, error) {
	identity, err := kmsReadIdentity(path, "AWS web identity")
	if err != nil {
		return kmsAWSCredentials{}, err
	}
	region := client.env("AWS_REGION")
	if region == "" {
		region = client.env("AWS_DEFAULT_REGION")
	}
	host := "sts.amazonaws.com"
	if region != "" {
		if !kmsAWSRegion.MatchString(region) {
			return kmsAWSCredentials{}, errors.New("invalid AWS STS region")
		}
		host = "sts." + region + ".amazonaws.com"
	}
	session := client.env("AWS_ROLE_SESSION_NAME")
	if session == "" {
		session = "zoo"
	}
	params := url.Values{"Action": {"AssumeRoleWithWebIdentity"}, "Version": {"2011-06-15"}, "RoleArn": {role}, "RoleSessionName": {session}, "WebIdentityToken": {strings.TrimSpace(string(identity))}}
	if params.Get("WebIdentityToken") == "" {
		return kmsAWSCredentials{}, errors.New("empty AWS web identity token")
	}
	body, err := client.request(ctx, http.MethodPost, "https://"+host+"/", "AWS STS", []byte(params.Encode()), map[string]string{"Content-Type": "application/x-www-form-urlencoded"})
	if err != nil {
		return kmsAWSCredentials{}, err
	}
	var response struct {
		Credentials struct {
			AccessKeyID string `xml:"AccessKeyId"`
			Secret      string `xml:"SecretAccessKey"`
			Token       string `xml:"SessionToken"`
			Expiration  string `xml:"Expiration"`
		} `xml:"AssumeRoleWithWebIdentityResult>Credentials"`
	}
	if xml.Unmarshal(body, &response) != nil {
		return kmsAWSCredentials{}, errors.New("invalid AWS STS response")
	}
	values := response.Credentials
	if values.Token == "" {
		return kmsAWSCredentials{}, errors.New("AWS STS returned incomplete credentials")
	}
	return client.awsTemporary(values.AccessKeyID, values.Secret, values.Token, values.Expiration)
}

func (client *kmsClient) awsContainer(ctx context.Context, address string) (kmsAWSCredentials, error) {
	headers := make(map[string]string)
	if path := client.env("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"); path != "" {
		value, err := kmsReadIdentity(path, "AWS container authorization")
		if err != nil {
			return kmsAWSCredentials{}, err
		}
		headers["Authorization"] = strings.TrimSpace(string(value))
		if headers["Authorization"] == "" {
			return kmsAWSCredentials{}, errors.New("empty AWS container authorization token")
		}
	}
	body, err := client.request(ctx, http.MethodGet, address, "AWS container credentials", nil, headers)
	if err != nil {
		return kmsAWSCredentials{}, err
	}
	var response struct {
		Access     string `json:"AccessKeyId"`
		Secret     string `json:"SecretAccessKey"`
		Token      string `json:"Token"`
		Expiration string `json:"Expiration"`
	}
	if json.Unmarshal(body, &response) != nil {
		return kmsAWSCredentials{}, errors.New("invalid AWS container credential response")
	}
	return client.awsTemporary(response.Access, response.Secret, response.Token, response.Expiration)
}

func (client *kmsClient) awsTemporary(access, secret, token, expiry string) (kmsAWSCredentials, error) {
	expires, err := time.Parse(time.RFC3339Nano, expiry)
	if err != nil || access == "" || secret == "" || !client.now().Before(expires) {
		return kmsAWSCredentials{}, errors.New("AWS returned invalid or expired temporary credentials")
	}
	return kmsAWSCredentials{access: access, secret: secret, token: token, expires: expires.Add(-5 * time.Minute)}, nil
}

func kmsValidResource(value string) bool {
	return value != "." && value != ".." && kmsResourceName.MatchString(value)
}

func kmsGCPResource(key string) bool {
	parts := strings.Split(key, "/")
	if len(parts) != 8 || parts[0] != "projects" || parts[2] != "locations" || parts[4] != "keyRings" || parts[6] != "cryptoKeys" {
		return false
	}
	for _, name := range []string{parts[1], parts[3], parts[5], parts[7]} {
		if !kmsValidResource(name) {
			return false
		}
	}
	return true
}

func (client *kmsClient) gcp(ctx context.Context, key, verb string, body map[string]string) (map[string]json.RawMessage, error) {
	if !kmsGCPResource(key) {
		return nil, errors.New("invalid Google Cloud KMS resource")
	}
	token, err := client.gcpToken(ctx)
	if err != nil {
		return nil, err
	}
	return client.requestJSON(ctx, "https://cloudkms.googleapis.com/v1/"+key+":"+verb, "Google Cloud KMS "+verb, body, map[string]string{"Authorization": "Bearer " + token, "Content-Type": "application/json"})
}

type kmsServiceAccount struct {
	Type       string `json:"type"`
	Email      string `json:"client_email"`
	PrivateKey string `json:"private_key"`
	TokenURI   string `json:"token_uri"`
}

func (client *kmsClient) gcpToken(ctx context.Context) (string, error) {
	path := client.env("GOOGLE_APPLICATION_CREDENTIALS")
	source := "metadata"
	var account kmsServiceAccount
	if path != "" {
		body, err := kmsReadIdentity(path, "Google service account")
		if err != nil {
			return "", err
		}
		if json.Unmarshal(body, &account) != nil {
			return "", errors.New("invalid Google credential file")
		}
		if account.Type != "" && account.Type != "service_account" {
			return "", errors.New("unsupported Google credential type; a service account key is required")
		}
		if account.Email == "" || account.PrivateKey == "" || account.TokenURI == "" {
			return "", errors.New("incomplete Google service account credentials")
		}
		if _, err := kmsURL(account.TokenURI, true); err != nil {
			return "", errors.New("invalid Google service account token URI")
		}
		digest := sha256.Sum256(body)
		source = "service-account\x00" + path + "\x00" + hex.EncodeToString(digest[:])
	}
	client.mu.Lock()
	if client.gcpSource == source && client.now().Before(client.gcpExpiry) {
		cached := client.gcpAccess
		client.mu.Unlock()
		return cached, nil
	}
	client.mu.Unlock()
	var body []byte
	var err error
	if path == "" {
		body, err = client.request(ctx, http.MethodGet, gcpMetadataToken, "Google metadata token", nil, map[string]string{"Metadata-Flavor": "Google"})
	} else {
		assertion, failure := client.gcpAssertion(account)
		if failure != nil {
			return "", failure
		}
		params := url.Values{"grant_type": {"urn:ietf:params:oauth:grant-type:jwt-bearer"}, "assertion": {assertion}}
		body, err = client.request(ctx, http.MethodPost, account.TokenURI, "Google service account token", []byte(params.Encode()), map[string]string{"Content-Type": "application/x-www-form-urlencoded"})
	}
	if err != nil {
		return "", err
	}
	var response struct {
		Token   string      `json:"access_token"`
		Expires json.Number `json:"expires_in"`
	}
	if json.Unmarshal(body, &response) != nil {
		return "", errors.New("invalid Google token response")
	}
	lifetime, err := response.Expires.Int64()
	if err != nil || lifetime <= 0 || lifetime > 24*60*60 || response.Token == "" {
		return "", errors.New("Google returned an invalid token or expiry")
	}
	client.mu.Lock()
	client.gcpSource, client.gcpAccess = source, response.Token
	client.gcpExpiry = client.now().Add(time.Duration(lifetime)*time.Second - time.Minute)
	client.mu.Unlock()
	return response.Token, nil
}

func (client *kmsClient) gcpAssertion(account kmsServiceAccount) (string, error) {
	block, trailing := pem.Decode([]byte(account.PrivateKey))
	if block == nil || len(bytes.TrimSpace(trailing)) != 0 {
		return "", errors.New("invalid Google service account private key")
	}
	var key *rsa.PrivateKey
	if block.Type == "RSA PRIVATE KEY" {
		parsed, err := x509.ParsePKCS1PrivateKey(block.Bytes)
		if err != nil {
			return "", errors.New("invalid Google service account RSA private key")
		}
		key = parsed
	} else if block.Type == "PRIVATE KEY" {
		parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
		if err != nil {
			return "", errors.New("invalid Google service account private key")
		}
		var ok bool
		key, ok = parsed.(*rsa.PrivateKey)
		if !ok {
			return "", errors.New("Google service account JWT signing requires an RSA key")
		}
	} else {
		return "", errors.New("unsupported Google service account private key format")
	}
	now := client.now().Unix()
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256","typ":"JWT"}`))
	claims, err := json.Marshal(map[string]any{"iss": account.Email, "scope": gcpKMSScope, "aud": account.TokenURI, "iat": now, "exp": now + 3600})
	if err != nil {
		return "", errors.New("invalid Google service account JWT")
	}
	unsigned := header + "." + base64.RawURLEncoding.EncodeToString(claims)
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		return "", errors.New("Google service account JWT signing failed")
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}

func (client *kmsClient) vault(ctx context.Context, key, verb string, body map[string]string) (map[string]json.RawMessage, error) {
	address, token := client.env("VAULT_ADDR"), client.env("VAULT_TOKEN")
	if address == "" || token == "" {
		return nil, errors.New("Vault requires VAULT_ADDR and VAULT_TOKEN")
	}
	if _, err := kmsURL(address, false); err != nil {
		return nil, errors.New("invalid Vault address")
	}
	parts := strings.Split(key, "/")
	if len(parts) < 2 {
		return nil, errors.New("Vault requires a transit mount and key name")
	}
	for _, name := range parts {
		if !kmsValidResource(name) {
			return nil, errors.New("invalid Vault transit mount or key name")
		}
	}
	mount, name := strings.Join(parts[:len(parts)-1], "/"), parts[len(parts)-1]
	headers := map[string]string{"X-Vault-Token": token, "Content-Type": "application/json"}
	if namespace := client.env("VAULT_NAMESPACE"); namespace != "" {
		headers["X-Vault-Namespace"] = namespace
	}
	response, err := client.requestJSON(ctx, strings.TrimRight(address, "/")+"/v1/"+mount+"/"+verb+"/"+name, "Vault transit "+verb, body, headers)
	if err != nil {
		return nil, err
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(response["data"], &fields) != nil || fields == nil {
		return nil, errors.New("invalid Vault transit response")
	}
	return fields, nil
}
