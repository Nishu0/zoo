// zoo-node runs on Linux, macOS and Windows sandbox hosts. It joins the control plane once with a token from the
// dashboard, then dials out to the API over gRPC with mTLS and keeps the link up: it reports the host's capacity,
// health and running sandboxes, carries the API's connections to the host's SSH server, and updates
// itself to the API's version. Hosts need no inbound ports, so they can sit behind NAT.
//
//	zoo-node join <token> [--service] [--ssh-user USER] [--dir DIR]
//	zoo-node run [--dir DIR]
//	zoo-node version
//
// `zoo node <command>` works too, for the zoo CLI.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
)

// set at build time (make node-dist); "dev" builds are never updated by the API
var version = "dev"

// exitRestart asks the service manager to start the (new) binary again
const exitRestart = 3

func main() {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "node" {
		args = args[1:]
	}
	if len(args) == 0 {
		usage()
	}
	var err error
	switch args[0] {
	case "join":
		err = joinCommand(args[1:])
	case "run":
		err = runCommand(args[1:])
	case "version":
		fmt.Println(version)
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "zoo-node:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `usage:
  zoo-node join <token> [--service] [--ssh-user USER] [--dir DIR]
  zoo-node run [--dir DIR]
  zoo-node version
`)
	os.Exit(2)
}

func flagSet(name string) *flag.FlagSet {
	return flag.NewFlagSet(name, flag.ContinueOnError)
}

// parse handles flags before or after the positional arguments.
func parse(fs *flag.FlagSet, args []string) ([]string, error) {
	var positional []string
	for {
		if err := fs.Parse(args); err != nil {
			return nil, err
		}
		args = fs.Args()
		if len(args) == 0 {
			return positional, nil
		}
		positional = append(positional, args[0])
		args = args[1:]
	}
}

func joinCommand(args []string) error {
	fs := flagSet("join")
	dir := fs.String("dir", "", "where the node keeps its key, certificates and config")
	service := fs.Bool("service", false, "install and start zoo-node as a service")
	sshUser := fs.String("ssh-user", "", "the SSH account the API signs in as (default: current account on Linux and macOS)")
	rest, err := parse(fs, args)
	if err != nil {
		return err
	}
	if len(rest) != 1 {
		return errors.New("join takes one token, from the dashboard (Servers > Add server)")
	}
	home := configDir(*dir)
	cfg, err := join(context.Background(), rest[0], home, *sshUser)
	if err != nil {
		return err
	}
	fmt.Printf("joined as node %s (server %s); config in %s\n", cfg.NodeID, cfg.ServerID, home)
	if !*service {
		fmt.Printf("start it with: zoo-node run --dir %s\n", home)
		return nil
	}
	if err := installService(home); err != nil {
		return fmt.Errorf("joined, but the service didn't install: %w", err)
	}
	fmt.Println("zoo-node is running as a service")
	return nil
}

func runCommand(args []string) error {
	fs := flagSet("run")
	dir := fs.String("dir", "", "where the node keeps its key, certificates and config")
	if _, err := parse(fs, args); err != nil {
		return err
	}
	home := configDir(*dir)
	if runAsService(home) {
		return nil
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	err := serve(ctx, home)
	if errors.Is(err, errUpdated) {
		// the service manager starts the new binary
		os.Exit(exitRestart)
	}
	return err
}

// serve runs the node until ctx ends; errUpdated means a new build is in place and the node should restart.
func serve(ctx context.Context, home string) error {
	cfg, err := loadConfig(home)
	if err != nil {
		return err
	}
	return newNode(cfg, home, slog.New(slog.NewTextHandler(os.Stderr, nil))).run(ctx)
}
