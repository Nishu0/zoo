VM_CONTROL_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
VM_CONTROL_OUT ?= /tmp/zoo-vm-control-artifacts
VM_CONTROL_ARCH ?= amd64

.PHONY: vm-control-build vm-control-static kata-vm-control

vm-control-build:
	mkdir -p $(VM_CONTROL_OUT)
	GOOS=linux GOARCH=$(VM_CONTROL_ARCH) CGO_ENABLED=0 go build -C $(VM_CONTROL_ROOT)/vm-control -trimpath -o $(VM_CONTROL_OUT)/zoo-vm-control-linux-$(VM_CONTROL_ARCH) .

vm-control-static: vm-control-build
	GOOS=linux GOARCH=$(VM_CONTROL_ARCH) CGO_ENABLED=0 go vet -C $(VM_CONTROL_ROOT)/vm-control ./...
	GOOS=linux GOARCH=$(VM_CONTROL_ARCH) CGO_ENABLED=0 go test -C $(VM_CONTROL_ROOT)/vm-control -c -o $(VM_CONTROL_OUT)/vm-control-$(VM_CONTROL_ARCH).test .

kata-vm-control:
	bash $(VM_CONTROL_ROOT)/scripts/build-kata-vm-control.sh $(VM_CONTROL_OUT) $(VM_CONTROL_ARCH)
