"""Shared gate for dashboard and automatic LAN discovery."""

import threading

scan_lock = threading.Lock()
