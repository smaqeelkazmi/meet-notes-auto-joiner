"""Errors the CLI reports without a stack trace."""

from __future__ import annotations


class ConfigError(Exception):
    """The config file or environment variables cannot be used as-is."""


class AlreadyRunning(Exception):
    """Another meet-notes run process holds the lock."""
