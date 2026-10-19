package main

import (
	"bytes"
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

var kmsFixtureTime = time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)

const (
	kmsFixtureBlob   = "Zml4dHVyZQ=="
	kmsFixtureGCPKey = "projects/project/locations/global/keyRings/ring/cryptoKeys/key"
)

type kmsFakeTransport struct {
	base     http.RoundTripper
	endpoint *url.URL
}

func (transport *kmsFakeTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	forwarded := request.Clone(request.Context())
	address := *request.URL
	address.Scheme, address.Host = transport.endpoint.Scheme, transport.endpoint.Host
	forwarded.URL = &address
	if forwarded.Host == "" {
		forwarded.Host = request.URL.Host
	}
	return transport.base.RoundTrip(forwarded)
}

func newKMSFixture(test *testing.T, environment map[string]string, handler http.HandlerFunc) *kmsClient {
	test.Helper()
	server := httptest.NewTLSServer(handler)
	test.Cleanup(server.Close)
	endpoint, err := url.Parse(server.URL)
	if err != nil {
		test.Fatal(err)
	}
	transport := server.Client().Transport
	return &kmsClient{
		http: &http.Client{Transport: &kmsFakeTransport{base: transport, endpoint: endpoint}, Timeout: kmsTimeout, CheckRedirect: func(request *http.Request, previous []*http.Request) error { return http.ErrUseLastResponse }},
		env:  func(name string) string { return environment[name] }, now: func() time.Time { return kmsFixtureTime },
	}
}

func kmsWriteJSON(test *testing.T, writer http.ResponseWriter, value any) {
	test.Helper()
	writer.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(writer).Encode(value); err != nil {
		test.Error(err)
	}
}

func kmsReadJSON(test *testing.T, request *http.Request) map[string]string {
	test.Helper()
	var body map[string]string
	if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
		test.Error(err)
	}
	return body
}

func kmsIdentityFile(test *testing.T, body []byte) string {
	test.Helper()
	path := filepath.Join(test.TempDir(), "credential")
	if err := os.WriteFile(path, body, 0600); err != nil {
		test.Fatal(err)
	}
	return path
}

func TestKMSLocalWrapUnwrapAndNoJWTFallback(test *testing.T) {
	test.Setenv("ZOO_KMS", "")
	test.Setenv("ZOO_SECRETS_KEY", "gateway-test-secret")
	test.Setenv("JWT_SECRET", "not-a-wrapping-key")
	wrapped, err := wrapDataKey(context.Background(), []byte(fixtureDataKey))
	if err != nil || !strings.HasPrefix(wrapped, "local:") {
		test.Fatalf("local wrap failed: %v", err)
	}
	key, err := unwrapDataKey(context.Background(), wrapped, []string{"rotated-secret", "gateway-test-secret"})
	if err != nil || !bytes.Equal(key, []byte(fixtureDataKey)) {
		test.Fatalf("local unwrap did not preserve the encoded Fernet key: %v", err)
	}
	key, err = unwrapDataKey(context.Background(), fixtureWrappedKey, []string{"gateway-test-secret"})
	if err != nil || string(key) != fixtureDataKey {
		test.Fatalf("Python local wrapper was rejected: %v", err)
	}
	test.Setenv("ZOO_SECRETS_KEY", "")
	if _, err := wrapDataKey(context.Background(), []byte(fixtureDataKey)); err == nil {
		test.Fatal("JWT_SECRET silently replaced the required local wrapping material")
	}
	if _, err := unwrapDataKey(context.Background(), wrapped, nil); err == nil {
		test.Fatal("local unwrap used implicit environment keys")
	}
}

func TestKMSSigV4MatchesPythonFixture(test *testing.T) {
	headers := map[string]string{"content-type": "application/x-amz-json-1.1", "host": "kms.us-east-1.amazonaws.com", "x-amz-date": "20261013T120000Z", "x-amz-target": "TrentService.Decrypt", "x-amz-security-token": "fixture-session"}
	actual := kmsSigV4("POST", headers["host"], "/", headers, []byte(`{"CiphertextBlob":"Zml4dHVyZQ=="}`), "us-east-1", "kms", "fixture-access", "fixture-secret", headers["x-amz-date"])
	expected := "AWS4-HMAC-SHA256 Credential=fixture-access/20261013/us-east-1/kms/aws4_request, SignedHeaders=content-type;host;x-amz-date;x-amz-security-token;x-amz-target, Signature=404ec48e31977965adf0396b006ff96bc234da091acba4ed070a71d6130e74b5"
	if actual != expected {
		test.Fatal("SigV4 differs from the Python-produced fixture")
	}
}

func kmsAWSHandler(test *testing.T, writer http.ResponseWriter, request *http.Request, key, region, access, secret, token string) {
	test.Helper()
	if request.Host != "kms."+region+".amazonaws.com" || request.Method != http.MethodPost || request.URL.Path != "/" {
		test.Error("AWS request used the wrong endpoint")
	}
	payload, err := io.ReadAll(request.Body)
	if err != nil {
		test.Error(err)
	}
	headers := map[string]string{"content-type": request.Header.Get("Content-Type"), "host": request.Host, "x-amz-date": request.Header.Get("X-Amz-Date"), "x-amz-target": request.Header.Get("X-Amz-Target")}
	if token != "" {
		headers["x-amz-security-token"] = token
	}
	if request.Header.Get("X-Amz-Security-Token") != token || request.Header.Get("Authorization") != kmsSigV4(http.MethodPost, request.Host, "/", headers, payload, region, "kms", access, secret, headers["x-amz-date"]) {
		test.Error("AWS authorization did not sign the exact payload and session token")
	}
	var body map[string]string
	if json.Unmarshal(payload, &body) != nil {
		test.Error("invalid AWS request body")
	}
	switch request.Header.Get("X-Amz-Target") {
	case "TrentService.Encrypt":
		if body["KeyId"] != key || body["Plaintext"] != base64.StdEncoding.EncodeToString([]byte(fixtureDataKey)) {
			test.Error("AWS Encrypt changed the data key or key identifier")
		}
		kmsWriteJSON(test, writer, map[string]string{"CiphertextBlob": kmsFixtureBlob})
	case "TrentService.Decrypt":
		if body["CiphertextBlob"] != kmsFixtureBlob || len(body) != 1 {
			test.Error("AWS Decrypt changed ciphertext or inferred a current wrapping key")
		}
		kmsWriteJSON(test, writer, map[string]string{"Plaintext": base64.StdEncoding.EncodeToString([]byte(fixtureDataKey))})
	default:
		test.Error("unexpected AWS operation")
		writer.WriteHeader(http.StatusBadRequest)
	}
}

func TestKMSAWSExplicitCredentialsSymmetry(test *testing.T) {
	for _, setting := range []string{"aws:arn:aws:kms:eu-west-1:123456789012:key/fixture", "aws:alias/fixture"} {
		test.Run(setting, func(test *testing.T) {
			environment := map[string]string{"ZOO_KMS": setting, "AWS_ACCESS_KEY_ID": "fixture-access", "AWS_SECRET_ACCESS_KEY": "fixture-secret", "AWS_SESSION_TOKEN": "fixture-session", "AWS_DEFAULT_REGION": "eu-west-1"}
			if strings.Contains(setting, "arn:") {
				environment["AWS_REGION"] = "us-east-1"
			}
			keyID := strings.TrimPrefix(setting, "aws:")
			var calls atomic.Int64
			client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
				calls.Add(1)
				kmsAWSHandler(test, writer, request, keyID, "eu-west-1", "fixture-access", "fixture-secret", "fixture-session")
			})
			wrapped, err := client.wrap(context.Background(), []byte(fixtureDataKey))
			if err != nil || wrapped != "aws:eu-west-1:"+kmsFixtureBlob {
				test.Fatalf("AWS wrap format differs from Python: %v", err)
			}
			environment["ZOO_KMS"] = "vault:transit/current"
			key, err := client.unwrap(context.Background(), wrapped, []string{"irrelevant-local-material"})
			if err != nil || string(key) != fixtureDataKey || calls.Load() != 2 {
				test.Fatalf("AWS unwrap did not dispatch by the stored prefix: %v", err)
			}
		})
	}
}

func TestKMSAWSWebIdentityAndCredentialCache(test *testing.T) {
	for _, region := range []string{"", "us-east-1"} {
		test.Run("sts-"+region, func(test *testing.T) {
			identity := kmsIdentityFile(test, []byte("fixture-web-token\n"))
			keyID := "arn:aws:kms:us-east-1:123456789012:key/fixture"
			environment := map[string]string{"ZOO_KMS": "aws:" + keyID, "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/fixture", "AWS_WEB_IDENTITY_TOKEN_FILE": identity, "AWS_ROLE_SESSION_NAME": "fixture-session-name", "AWS_REGION": region, "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://container.invalid/never-used"}
			var stsCalls, kmsCalls atomic.Int64
			client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
				if strings.HasPrefix(request.Host, "sts.") {
					stsCalls.Add(1)
					expectedHost := "sts.amazonaws.com"
					if region != "" {
						expectedHost = "sts." + region + ".amazonaws.com"
					}
					if err := request.ParseForm(); err != nil {
						test.Error(err)
					}
					if request.Host != expectedHost || request.Form.Get("Action") != "AssumeRoleWithWebIdentity" || request.Form.Get("Version") != "2011-06-15" || request.Form.Get("RoleArn") != environment["AWS_ROLE_ARN"] || request.Form.Get("RoleSessionName") != "fixture-session-name" || request.Form.Get("WebIdentityToken") != "fixture-web-token" {
						test.Error("web identity protocol fields differ from Python")
					}
					writer.Header().Set("Content-Type", "text/xml")
					fmt.Fprintf(writer, `<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleWithWebIdentityResult><Credentials><AccessKeyId>temporary-access</AccessKeyId><SecretAccessKey>temporary-secret</SecretAccessKey><SessionToken>temporary-session</SessionToken><Expiration>%s</Expiration></Credentials></AssumeRoleWithWebIdentityResult></AssumeRoleWithWebIdentityResponse>`, kmsFixtureTime.Add(time.Hour).Format(time.RFC3339))
					return
				}
				if request.Host == "container.invalid" {
					test.Error("web identity fell back to container credentials")
					writer.WriteHeader(http.StatusForbidden)
					return
				}
				kmsCalls.Add(1)
				kmsAWSHandler(test, writer, request, keyID, "us-east-1", "temporary-access", "temporary-secret", "temporary-session")
			})
			wrapped, err := client.wrap(context.Background(), []byte(fixtureDataKey))
			if err != nil {
				test.Fatal(err)
			}
			if key, err := client.unwrap(context.Background(), wrapped, nil); err != nil || string(key) != fixtureDataKey {
				test.Fatalf("temporary AWS credentials failed: %v", err)
			}
			if stsCalls.Load() != 1 || kmsCalls.Load() != 2 {
				test.Fatal("unexpired web identity credentials were not cached")
			}
		})
	}
}

func TestKMSAWSContainerIdentityAndAuthorizationFile(test *testing.T) {
	path := kmsIdentityFile(test, []byte("fixture-container-authorization\n"))
	keyID := "alias/fixture"
	environment := map[string]string{"ZOO_KMS": "aws:" + keyID, "AWS_REGION": "us-east-1", "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.23/v1/credentials", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": path}
	var credentialCalls atomic.Int64
	client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
		if request.Host == "169.254.170.23" {
			credentialCalls.Add(1)
			if request.Method != http.MethodGet || request.URL.Path != "/v1/credentials" || request.Header.Get("Authorization") != "fixture-container-authorization" {
				test.Error("container credential request did not use the configured token file")
			}
			kmsWriteJSON(test, writer, map[string]string{"AccessKeyId": "container-access", "SecretAccessKey": "container-secret", "Token": "container-session", "Expiration": kmsFixtureTime.Add(time.Hour).Format(time.RFC3339)})
			return
		}
		kmsAWSHandler(test, writer, request, keyID, "us-east-1", "container-access", "container-secret", "container-session")
	})
	wrapped, err := client.wrap(context.Background(), []byte(fixtureDataKey))
	if err != nil {
		test.Fatal(err)
	}
	if key, err := client.unwrap(context.Background(), wrapped, nil); err != nil || string(key) != fixtureDataKey {
		test.Fatalf("container AWS credentials failed: %v", err)
	}
	if credentialCalls.Load() != 1 {
		test.Fatal("container credentials were not cached")
	}
}

func TestKMSAWSCredentialFailuresNeverTryAnotherMethod(test *testing.T) {
	for _, method := range []string{"partial-env", "partial-web", "failed-sts", "expired-container", "relative-container", "direct-container-token"} {
		test.Run(method, func(test *testing.T) {
			environment := map[string]string{"AWS_REGION": "us-east-1", "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://container.invalid/credentials", "ZOO_SECRETS_KEY": "gateway-test-secret"}
			var calls atomic.Int64
			switch method {
			case "partial-env":
				environment["AWS_ACCESS_KEY_ID"] = "partial"
			case "partial-web":
				environment["AWS_ROLE_ARN"] = "configured-role"
			case "failed-sts":
				environment["AWS_ROLE_ARN"] = "configured-role"
				environment["AWS_WEB_IDENTITY_TOKEN_FILE"] = kmsIdentityFile(test, []byte("web-token"))
			case "relative-container":
				delete(environment, "AWS_CONTAINER_CREDENTIALS_FULL_URI")
				environment["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"] = "/unsupported"
			case "direct-container-token":
				environment["AWS_CONTAINER_AUTHORIZATION_TOKEN"] = "unsupported-direct-token"
			}
			client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
				calls.Add(1)
				if method == "expired-container" {
					kmsWriteJSON(test, writer, map[string]string{"AccessKeyId": "expired", "SecretAccessKey": "expired-secret", "Expiration": kmsFixtureTime.Add(-time.Minute).Format(time.RFC3339)})
					return
				}
				if method == "failed-sts" && !strings.HasPrefix(request.Host, "sts.") {
					test.Error("failed STS request switched credential methods")
				}
				writer.WriteHeader(http.StatusForbidden)
				writer.Write([]byte("sensitive-provider-message"))
			})
			key, err := client.unwrap(context.Background(), "aws:us-east-1:"+kmsFixtureBlob, []string{"gateway-test-secret"})
			if err == nil || key != nil || strings.Contains(err.Error(), "sensitive-provider-message") {
				test.Fatal("credential failure succeeded, leaked provider details, or fell back locally")
			}
			expected := int64(0)
			if method == "failed-sts" || method == "expired-container" {
				expected = 1
			}
			if calls.Load() != expected {
				test.Fatalf("unexpected credential provider calls: %d", calls.Load())
			}
		})
	}
}

func kmsServiceAccountFile(test *testing.T, key *rsa.PrivateKey, format string) string {
	test.Helper()
	var encoded []byte
	if format == "PKCS1" {
		encoded = pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	} else {
		der, err := x509.MarshalPKCS8PrivateKey(key)
		if err != nil {
			test.Fatal(err)
		}
		encoded = pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	}
	body, err := json.Marshal(kmsServiceAccount{Type: "service_account", Email: "fixture@project.iam.gserviceaccount.com", PrivateKey: string(encoded), TokenURI: "https://oauth2.googleapis.com/token"})
	if err != nil {
		test.Fatal(err)
	}
	return kmsIdentityFile(test, body)
}

func kmsGCPHandler(test *testing.T, writer http.ResponseWriter, request *http.Request) {
	test.Helper()
	if request.Host != "cloudkms.googleapis.com" || request.Method != http.MethodPost || request.Header.Get("Authorization") != "Bearer fixture-google-token" {
		test.Error("Google KMS used the wrong endpoint or bearer token")
	}
	body := kmsReadJSON(test, request)
	switch request.URL.Path {
	case "/v1/" + kmsFixtureGCPKey + ":encrypt":
		if body["plaintext"] != base64.StdEncoding.EncodeToString([]byte(fixtureDataKey)) {
			test.Error("Google Encrypt changed the data key")
		}
		kmsWriteJSON(test, writer, map[string]string{"ciphertext": kmsFixtureBlob})
	case "/v1/" + kmsFixtureGCPKey + ":decrypt":
		if body["ciphertext"] != kmsFixtureBlob {
			test.Error("Google Decrypt changed the ciphertext")
		}
		kmsWriteJSON(test, writer, map[string]string{"plaintext": base64.StdEncoding.EncodeToString([]byte(fixtureDataKey))})
	default:
		test.Error("unexpected Google KMS path")
		writer.WriteHeader(http.StatusBadRequest)
	}
}

func TestKMSGCPServiceAccountJWTAndSymmetry(test *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		test.Fatal(err)
	}
	for _, format := range []string{"PKCS8", "PKCS1"} {
		test.Run(format, func(test *testing.T) {
			path := kmsServiceAccountFile(test, key, format)
			environment := map[string]string{"ZOO_KMS": "gcp:" + kmsFixtureGCPKey, "GOOGLE_APPLICATION_CREDENTIALS": path}
			var tokenCalls atomic.Int64
			client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
				if request.Host == "oauth2.googleapis.com" {
					tokenCalls.Add(1)
					if err := request.ParseForm(); err != nil {
						test.Error(err)
					}
					parts := strings.Split(request.Form.Get("assertion"), ".")
					if len(parts) != 3 || request.Form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer" {
						test.Error("invalid service account JWT exchange")
						writer.WriteHeader(http.StatusBadRequest)
						return
					}
					signature, err := base64.RawURLEncoding.DecodeString(parts[2])
					digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
					if err != nil || rsa.VerifyPKCS1v15(&key.PublicKey, crypto.SHA256, digest[:], signature) != nil {
						test.Error("service account JWT signature was invalid")
					}
					encoded, err := base64.RawURLEncoding.DecodeString(parts[1])
					var claims map[string]any
					if err != nil || json.Unmarshal(encoded, &claims) != nil || claims["iss"] != "fixture@project.iam.gserviceaccount.com" || claims["scope"] != gcpKMSScope || claims["aud"] != "https://oauth2.googleapis.com/token" || claims["iat"] != float64(kmsFixtureTime.Unix()) || claims["exp"] != float64(kmsFixtureTime.Unix()+3600) {
						test.Error("service account JWT claims differ from Python")
					}
					kmsWriteJSON(test, writer, map[string]any{"access_token": "fixture-google-token", "expires_in": 3600})
					return
				}
				if request.Host == "metadata.google.internal" {
					test.Error("service account credentials fell back to metadata")
					writer.WriteHeader(http.StatusForbidden)
					return
				}
				kmsGCPHandler(test, writer, request)
			})
			wrapped, err := client.wrap(context.Background(), []byte(fixtureDataKey))
			if err != nil || wrapped != "gcp:"+kmsFixtureGCPKey+":"+kmsFixtureBlob {
				test.Fatalf("Google wrap format differs from Python: %v", err)
			}
			if key, err := client.unwrap(context.Background(), wrapped, nil); err != nil || string(key) != fixtureDataKey {
				test.Fatalf("Google unwrap failed: %v", err)
			}
			if tokenCalls.Load() != 1 {
				test.Fatal("service account token was not cached")
			}
		})
	}
}

func TestKMSGCPMetadataTokensRefreshBeforeExpiry(test *testing.T) {
	environment := map[string]string{"ZOO_KMS": "gcp:" + kmsFixtureGCPKey}
	var tokenCalls atomic.Int64
	client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
		if request.Host == "metadata.google.internal" {
			tokenCalls.Add(1)
			if request.Method != http.MethodGet || request.URL.Path != "/computeMetadata/v1/instance/service-accounts/default/token" || request.Header.Get("Metadata-Flavor") != "Google" {
				test.Error("Google metadata request differs from Python")
			}
			kmsWriteJSON(test, writer, map[string]any{"access_token": "fixture-google-token", "expires_in": 3600})
			return
		}
		kmsGCPHandler(test, writer, request)
	})
	wrapped, err := client.wrap(context.Background(), []byte(fixtureDataKey))
	if err != nil {
		test.Fatal(err)
	}
	if _, err := client.unwrap(context.Background(), wrapped, nil); err != nil {
		test.Fatal(err)
	}
	if tokenCalls.Load() != 1 {
		test.Fatal("metadata token was not cached")
	}
	client.now = func() time.Time { return kmsFixtureTime.Add(3541 * time.Second) }
	if _, err := client.unwrap(context.Background(), wrapped, nil); err != nil || tokenCalls.Load() != 2 {
		test.Fatalf("metadata token did not refresh before expiry: %v", err)
	}
}

func TestKMSGCPUnsupportedCredentialsNeverUseMetadata(test *testing.T) {
	for _, credentialType := range []string{"authorized_user", "external_account", "impersonated_service_account", "service_account"} {
		test.Run(credentialType, func(test *testing.T) {
			body, err := json.Marshal(map[string]string{"type": credentialType, "client_email": "fixture@example.com", "private_key": "invalid-private-key", "token_uri": "https://oauth2.googleapis.com/token"})
			if err != nil {
				test.Fatal(err)
			}
			environment := map[string]string{"GOOGLE_APPLICATION_CREDENTIALS": kmsIdentityFile(test, body)}
			var calls atomic.Int64
			client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
				calls.Add(1)
				writer.WriteHeader(http.StatusForbidden)
			})
			key, err := client.unwrap(context.Background(), "gcp:"+kmsFixtureGCPKey+":"+kmsFixtureBlob, []string{"gateway-test-secret"})
			if err == nil || key != nil || calls.Load() != 0 {
				test.Fatal("unsupported credentials were accepted or fell back to metadata/local keys")
			}
		})
	}
}

func TestKMSGCPRejectsNonRSAServiceAccountKey(test *testing.T) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		test.Fatal(err)
	}
	account := kmsServiceAccount{Type: "service_account", Email: "fixture@example.com", PrivateKey: string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})), TokenURI: "https://oauth2.googleapis.com/token"}
	client := newKMSFixture(test, nil, func(writer http.ResponseWriter, request *http.Request) {
		test.Error("non-RSA key made a network request")
	})
	if _, err := client.gcpAssertion(account); err == nil {
		test.Fatal("non-RSA key signed an RS256 JWT")
	}
}

func TestKMSVaultTransitSymmetryPreservesNamespaceAndCiphertextColons(test *testing.T) {
	environment := map[string]string{"ZOO_KMS": "vault:tenant/transit/data-key", "VAULT_ADDR": "https://vault.invalid/base/", "VAULT_TOKEN": "fixture-vault-token", "VAULT_NAMESPACE": "tenant/department/"}
	client := newKMSFixture(test, environment, func(writer http.ResponseWriter, request *http.Request) {
		if request.Host != "vault.invalid" || request.Method != http.MethodPost || request.Header.Get("X-Vault-Token") != "fixture-vault-token" || request.Header.Get("X-Vault-Namespace") != "tenant/department/" {
			test.Error("Vault did not use the exact token, namespace, and configured address")
		}
		body := kmsReadJSON(test, request)
		switch request.URL.Path {
		case "/base/v1/tenant/transit/encrypt/data-key":
			if body["plaintext"] != base64.StdEncoding.EncodeToString([]byte(fixtureDataKey)) {
				test.Error("Vault Encrypt changed the data key")
			}
			kmsWriteJSON(test, writer, map[string]any{"data": map[string]string{"ciphertext": "vault:v7:" + kmsFixtureBlob}})
		case "/base/v1/tenant/transit/decrypt/data-key":
			if body["ciphertext"] != "vault:v7:"+kmsFixtureBlob {
				test.Error("Vault ciphertext colon/version bytes changed")
			}
			kmsWriteJSON(test, writer, map[string]any{"data": map[string]string{"plaintext": base64.StdEncoding.EncodeToString([]byte(fixtureDataKey))}})
		default:
			test.Error("unexpected Vault transit path")
			writer.WriteHeader(http.StatusBadRequest)
		}
	})
	wrapped, err := client.wrap(context.Background(), []byte(fixtureDataKey))
	if err != nil || wrapped != "vault:tenant/transit/data-key:vault:v7:"+kmsFixtureBlob {
		test.Fatalf("Vault wrap format differs from Python: %v", err)
	}
	environment["ZOO_KMS"] = "aws:alias/current"
	if key, err := client.unwrap(context.Background(), wrapped, nil); err != nil || string(key) != fixtureDataKey {
		test.Fatalf("Vault unwrap did not dispatch by its stored prefix: %v", err)
	}
}

func TestKMSRejectsMalformedProvidersAndConfiguration(test *testing.T) {
	for _, wrapped := range []string{"", "unprefixed", "local:", "aws:", "aws:us-east-1:", "aws:invalid/host:blob", "gcp:projects/p/key:blob", "vault:transit:blob", "vault:transit/../key:blob", "unknown:where:blob"} {
		test.Run("unwrap-"+wrapped, func(test *testing.T) {
			client := newKMSFixture(test, map[string]string{"VAULT_ADDR": "https://vault.invalid", "VAULT_TOKEN": "configured"}, func(writer http.ResponseWriter, request *http.Request) {
				test.Error("invalid wrapped key made a network request")
			})
			if key, err := client.unwrap(context.Background(), wrapped, nil); err == nil || key != nil {
				test.Fatal("invalid wrapping provider/format was accepted")
			}
		})
	}
	for _, setting := range []string{"local:configured", "unknown:key", "aws:", "aws:alias/no-region", "gcp:invalid", "vault:transit/key"} {
		test.Run("wrap-"+setting, func(test *testing.T) {
			client := newKMSFixture(test, map[string]string{"ZOO_KMS": setting, "ZOO_SECRETS_KEY": "gateway-test-secret"}, func(writer http.ResponseWriter, request *http.Request) {
				test.Error("invalid wrapping setting made a network request")
			})
			if _, err := client.wrap(context.Background(), []byte(fixtureDataKey)); err == nil {
				test.Fatal("invalid cloud wrapping configuration fell back locally")
			}
		})
	}
	client := newKMSFixture(test, nil, func(writer http.ResponseWriter, request *http.Request) {
		test.Error("invalid data key made a network request")
	})
	for _, invalid := range [][]byte{nil, make([]byte, 32), []byte(strings.Repeat("!", 44))} {
		if _, err := client.wrap(context.Background(), invalid); err == nil {
			test.Fatal("a non-Fernet data key was wrapped")
		}
	}
}

func TestKMSProviderFailuresAreBoundedSanitizedAndNeverFallback(test *testing.T) {
	for _, failure := range []string{"status", "redirect", "oversized", "malformed", "invalid-key", "missing-data"} {
		test.Run(failure, func(test *testing.T) {
			var calls atomic.Int64
			client := newKMSFixture(test, map[string]string{"VAULT_ADDR": "https://vault.invalid", "VAULT_TOKEN": "private-token", "ZOO_SECRETS_KEY": "gateway-test-secret"}, func(writer http.ResponseWriter, request *http.Request) {
				calls.Add(1)
				switch failure {
				case "status":
					writer.WriteHeader(http.StatusForbidden)
					writer.Write([]byte("private-token sensitive-ciphertext sensitive-provider-message"))
				case "redirect":
					writer.Header().Set("Location", "https://must-not-follow.invalid/")
					writer.WriteHeader(http.StatusTemporaryRedirect)
				case "oversized":
					writer.Write([]byte(strings.Repeat("x", kmsResponseLimit+1)))
				case "malformed":
					writer.Write([]byte("invalid JSON private-token"))
				case "invalid-key":
					kmsWriteJSON(test, writer, map[string]any{"data": map[string]string{"plaintext": base64.StdEncoding.EncodeToString([]byte("not-a-Fernet-key"))}})
				case "missing-data":
					kmsWriteJSON(test, writer, map[string]string{"plaintext": "incorrect-shape"})
				}
			})
			key, err := client.unwrap(context.Background(), "vault:transit/key:vault:v1:sensitive-ciphertext", []string{"gateway-test-secret"})
			if err == nil || key != nil || calls.Load() != 1 {
				test.Fatal("provider failure succeeded, retried, redirected, or fell back")
			}
			for _, sensitive := range []string{"private-token", "sensitive-ciphertext", "sensitive-provider-message", "vault.invalid"} {
				if strings.Contains(err.Error(), sensitive) {
					test.Fatal("provider failure exposed sensitive details")
				}
			}
		})
	}
}

func TestKMSHonorsCancellationAndDeadline(test *testing.T) {
	var calls atomic.Int64
	client := newKMSFixture(test, map[string]string{"VAULT_ADDR": "https://vault.invalid", "VAULT_TOKEN": "fixture-token"}, func(writer http.ResponseWriter, request *http.Request) {
		calls.Add(1)
		select {
		case <-request.Context().Done():
		case <-time.After(time.Second):
		}
	})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.unwrap(ctx, "vault:transit/key:vault:v1:blob", nil); !errors.Is(err, context.Canceled) || calls.Load() != 0 {
		test.Fatalf("pre-canceled context was ignored: %v", err)
	}
	ctx, cancel = context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	started := time.Now()
	if _, err := client.unwrap(ctx, "vault:transit/key:vault:v1:blob", nil); !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > time.Second {
		test.Fatalf("caller deadline was ignored: %v", err)
	}
}
