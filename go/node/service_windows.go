package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const serviceName = "zoo-node"

// installService runs zoo-node as a Windows service (LocalSystem), restarted on failure and after updates.
func installService(home string) error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("the service manager needs an administrator: %w", err)
	}
	defer m.Disconnect()
	if s, err := m.OpenService(serviceName); err == nil {
		s.Control(svc.Stop)
		for i := 0; i < 30; i++ {
			if st, err := s.Query(); err != nil || st.State == svc.Stopped {
				break
			}
			time.Sleep(time.Second)
		}
		s.Delete()
		s.Close()
		time.Sleep(2 * time.Second)
	}
	bin := filepath.Join(os.Getenv("ProgramData"), "zoo-node", "zoo-node.exe")
	if err := installBinary(bin); err != nil {
		return err
	}
	s, err := m.CreateService(serviceName, bin, mgr.Config{
		DisplayName: "Zoo node",
		Description: "Links this host to the Zoo control plane",
		StartType:   mgr.StartAutomatic,
	}, "run", "--dir", home)
	if err != nil {
		return err
	}
	defer s.Close()
	restart := mgr.RecoveryAction{Type: mgr.ServiceRestart, Delay: 5 * time.Second}
	if err := s.SetRecoveryActions([]mgr.RecoveryAction{restart, restart, restart}, 86400); err != nil {
		return err
	}
	if err := s.SetRecoveryActionsOnNonCrashFailures(true); err != nil {
		return err
	}
	return s.Start()
}

type handler struct{ home string }

func (h handler) Execute(_ []string, requests <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- serve(ctx, h.home) }()
	status <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case err := <-done:
			// a non-zero exit makes the service manager restart it: on the new build after an update
			if errors.Is(err, errUpdated) {
				return true, exitRestart
			}
			return true, 1
		case r := <-requests:
			switch r.Cmd {
			case svc.Interrogate:
				status <- r.CurrentStatus
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				cancel()
				<-done
				return false, 0
			}
		}
	}
}

func runAsService(home string) bool {
	if is, err := svc.IsWindowsService(); err != nil || !is {
		return false
	}
	svc.Run(serviceName, handler{home})
	return true
}
