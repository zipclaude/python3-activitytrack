# SPDX-License-Identifier: GPL-3.0-or-later
"""The wire schema this desktop speaks (server docs/SYNC.md §12, app domain/sync/WireVersion.kt)."""

SCHEMA = 1

APP_HEADER = "X-ActivityTrack-App"
SCHEMA_HEADER = "X-ActivityTrack-Schema"

APP_TOO_OLD, SERVER_TOO_OLD = "app_too_old", "server_too_old"
