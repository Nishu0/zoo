MIGRATION_DIR=./db/migrations

VERSION ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)

.PHONY: help setup create up down status reset generate run rotate-secrets guest-darwin guest-windows egress-darwin egress-windows egress-dist proto gateway-proto node-dist dev devdown devkube devkubedown dev-down dev-kube dev-kube-down

help:
	@echo "Available commands:        "
	@echo "make setup                 --install Python deps (uv sync) and Go module deps (first run)"
	@echo "make create name=<name>    --create a new migrations file"
	@echo "make up                    --Run pending migrations"
	@echo "make down                  --Rollback the last migrations"
	@echo "make status                --Check which migrations have been applied"
	@echo "make reset                 --rollback all the db (also wipes out the schema)"
	@echo "make generate              --run sqlc generate"
	@echo "make rotate-secrets        --rewrap secret keys with ZOO_SECRETS_KEY or ZOO_KMS"
	@echo "make guest-darwin          --build zoo-guest for macOS VMs (guest/dist/)"
	@echo "make guest-windows         --build zoo-guest for Windows VMs (guest/dist/)"
	@echo "make egress-dist           --build zoo-egress for macOS and Windows hosts (egress/dist/)"
	@echo "make dev / devdown         --source API/dashboard with local Docker/Kata dependencies"
	@echo "make devkube / devkubedown --source API/dashboard with a local k3s/Kata development release"

# Python environment (uv sync, including the dev group) and the Go dependencies of every
# module in the repo; run once after cloning and after dependency changes
setup:
	uv sync
	for module in egress gateway guest node vm-control; do \
		echo "--> $$module: go mod download"; \
		(cd $$module && go mod download) || exit 1; \
	done

dev: node-dist
	VERSION="$(VERSION)" uv run python scripts/dev.py docker

devdown:
	uv run python scripts/dev.py docker-down

devkube: node-dist
	VERSION="$(VERSION)" uv run python scripts/dev.py kube

devkubedown:
	uv run python scripts/dev.py kube-down

dev-down: devdown
dev-kube: devkube
dev-kube-down: devkubedown

create:
	@if [ -z "$(name)" ]; then echo "Error: 'name' variable is required. Example: make create name=add_users"; exit 1; fi
	goose -dir ${MIGRATION_DIR} create ${name} sql

up:
	goose -dir ${MIGRATION_DIR} postgres "$$DATABASE_URL" up
	sqlc generate

down:
	goose -dir ${MIGRATION_DIR} postgres "$$DATABASE_URL" down
	sqlc generate

status:
	goose -dir ${MIGRATION_DIR} postgres "$$DATABASE_URL" status

reset:
	goose -dir ${MIGRATION_DIR} postgres "$$DATABASE_URL" reset
	sqlc generate

generate:
	sqlc generate

rotate-secrets:
	uv run python -m server.rotate_secrets

guest-darwin:
	cd guest && CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -ldflags="-s -w" -o dist/zoo-guest-darwin-arm64 .

# windowsgui: the guest runs in the desktop session, where a console program would open a window
guest-windows:
	cd guest && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="-s -w -H windowsgui" -o dist/zoo-guest-windows-amd64.exe .

egress-darwin:
	cd egress && CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -ldflags="-s -w" -o dist/zoo-egress-darwin-arm64 .

egress-windows:
	cd egress && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="-s -w -H windowsgui" -o dist/zoo-egress-windows-amd64.exe .

egress-dist: egress-darwin egress-windows

# after changing node/proto/node.proto (needs protoc, protoc-gen-go and protoc-gen-go-grpc)
proto:
	cd node && protoc -I proto --go_out=nodepb --go_opt=paths=source_relative --go-grpc_out=nodepb --go-grpc_opt=paths=source_relative proto/node.proto
	uv run python -m grpc_tools.protoc -Iserver/nodepb=node/proto --python_out=. --pyi_out=. --grpc_python_out=. server/nodepb/node.proto

gateway-proto:
	protoc -I gateway/proto --go_out=gateway --go_opt=module=zoo/gateway --go-grpc_out=gateway --go-grpc_opt=module=zoo/gateway gateway/proto/gateway.proto
	uv run python -m grpc_tools.protoc -Iserver/gatewaypb=gateway/proto --python_out=. --pyi_out=. --grpc_python_out=. server/gatewaypb/gateway.proto

# zoo-node for Linux, macOS and Windows, with the API's version, for installers and auto-update (node/dist/)
node-dist:
	cd node && for t in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64; do \
		os=$${t%/*}; arch=$${t#*/}; ext=$$( [ $$os = windows ] && echo .exe ); \
		CGO_ENABLED=0 GOOS=$$os GOARCH=$$arch go build -trimpath -ldflags="-s -w -X main.version=$(VERSION)" -o dist/zoo-node-$$os-$$arch$$ext . || exit 1; \
	done && echo $(VERSION) > dist/VERSION
