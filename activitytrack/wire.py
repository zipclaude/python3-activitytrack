# SPDX-License-Identifier: GPL-3.0-or-later
"""
The wire schema this desktop speaks (server docs/SYNC.md §12, app domain/sync/WireVersion.kt).

Schema 3 is the activity model 1.0 (app docs/ACTIVITY-MODEL.md §6.2). The record types the
store keeps are the server's; the two that changed:

- `entry` (the activity): contactUid, companyUid, clientUid, roleUid, activity, status
  (TODO, RUNNING, CLOSED), mode, locality, locationKind, notes, zoneId, plannedEndUtcMs,
  pluginFields, needsReview, createdUtcMs, updatedUtcMs, tags, and since 3: kind (ACTIVITY,
  ABSENCE), deletedUtcMs, closedUtcMs, externalKey, dueDate (yyyy-mm-dd), dueTimeMin, planDate
  (yyyy-mm-dd), planStartMin, durationMin, priority (0-5), absenceKind (ABSENCE, HOLIDAY, LEAVE,
  SICK). `todoUid` is gone.
- `segment`: entryUid, kind (WORK, TRAVEL, PAUSE, ABSENCE), startUtcMs, endUtcMs, zoneId, source,
  kmEstimated, kmDeclared, note, and since 3: planned.

The types `todo` and `absence` do not exist any more.
"""

SCHEMA = 3

APP_HEADER = "X-ActivityTrack-App"
SCHEMA_HEADER = "X-ActivityTrack-Schema"

APP_TOO_OLD, SERVER_TOO_OLD = "app_too_old", "server_too_old"
# the server refuses a client of a schema before 3 (ACTIVITY-MODEL.md §6)
UPGRADE_REQUIRED = "upgrade_required"
# what a server may answer when it does not accept this client, and says which side must update
INCOMPATIBLE_CODES = (APP_TOO_OLD, SERVER_TOO_OLD, UPGRADE_REQUIRED)

# record types of the schemas before 3, migrated by migration.py and not synced any more
LEGACY_TYPES = ("todo", "absence")
