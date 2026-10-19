//go:build !windows

package main

import "syscall"

// disk is the size and use of the filesystem holding path.
func disk(path string) (total, used uint64, ok bool) {
	var fs syscall.Statfs_t
	if syscall.Statfs(path, &fs) != nil {
		return 0, 0, false
	}
	return uint64(fs.Blocks) * uint64(fs.Bsize), uint64(fs.Blocks-fs.Bfree) * uint64(fs.Bsize), true
}
