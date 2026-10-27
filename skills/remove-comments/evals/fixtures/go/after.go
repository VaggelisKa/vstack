// Copyright 2025 Acme Corp. All rights reserved.
// Use of this source code is governed by a BSD-style license.

//go:build linux

package cache

import (
	"sync"
	"time"
)

type Cache struct {
	mu    sync.Mutex // protects items
	items map[string]entry
	ttl   time.Duration
}

type entry struct {
	value   any
	expires time.Time // zero means never
}

func New(ttl time.Duration) *Cache {
	return &Cache{items: make(map[string]entry), ttl: ttl}
}

// Callers must not modify the returned value: it is shared between goroutines.
func (c *Cache) Get(key string) (any, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.items[key]
	if !ok || (!e.expires.IsZero() && time.Now().After(e.expires)) {
		return nil, false
	}
	return e.value, true
}

func (c *Cache) Set(key string, v any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.items[key] = entry{value: v, expires: time.Now().Add(c.ttl)} //nolint:exhaustruct
}
