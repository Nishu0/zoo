package main

import (
	"flag"
	"log"
	"os"
)

func main() {
	directory := flag.String("dir", "/run/zoo-egress", "directory containing sandbox network policies")
	logPath := flag.String("log", "", "append logs to this file instead of stderr")
	flag.Parse()
	if *logPath != "" {
		file, err := os.OpenFile(*logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
		if err != nil {
			log.Fatal(err)
		}
		defer file.Close()
		log.SetOutput(file)
	}
	runEgress(*directory)
}
