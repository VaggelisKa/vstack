// Copyright 2025 Acme Corp. All rights reserved.
// Use of this source code is governed by a BSD-style license.

//go:build linux

// Package cache provides an in-memory cache.
package cache

import (
	"sync"
	"time"
)

// Cache is a cache.
type Cache struct {
	mu    sync.Mutex // protects items
	items map[string]entry
	// ttl is the time to live
	ttl   time.Duration
}

// entry holds a value and its expiry.
type entry struct {
	value   any
	expires time.Time // zero means never
}

// New creates a new Cache.
func New(ttl time.Duration) *Cache {
	// Initialize the map
	return &Cache{items: make(map[string]entry), ttl: ttl}
}

// Get returns the value for key.
// Callers must not modify the returned value: it is shared between goroutines.
func (c *Cache) Get(key string) (any, bool) {
	c.mu.Lock()
	defer c.mu.Unlock() // unlock when done
	e, ok := c.items[key]
	// Check if expired
	if !ok || (!e.expires.IsZero() && time.Now().After(e.expires)) {
		return nil, false
	}
	return e.value, true
}

// Updated: now uses the configured TTL
func (c *Cache) Set(key string, v any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.items[key] = entry{value: v, expires: time.Now().Add(c.ttl)} //nolint:exhaustruct
}
