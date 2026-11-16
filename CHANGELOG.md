# Changelog

What changes in behaviour, compatibility and features, release by release. Bug fixes are not
listed one by one (read `git log` or the issues); security fixes are, under "Security", with
their issue.

## 1.0.0 (pre-release: alfa-1, alfa-2, beta-1, beta-2)

The 1.0.0 release is in pre-release. This list is cumulative: it grows with each pre-release
and is final at the release.

### Compatibility

- The local store is migrated, one way, to the activity model of 1.0 when it is opened: a
  to-do becomes an activity in the state "Da fare" (TODO), an absence becomes an activity of
  kind absence with one segment per working day. Older backups are migrated the same way.
- Sync uses wire schema 4 (3 in alfa-1, alfa-2 and beta-1) and needs a server of the same
  pre-release: an older server is not accepted, and a server that finds this PC too old answers
  `upgrade_required`, which every interface says as "update the app". Schema 4 adds the
  activity's `completedUtcMs` and the attachment's `description`.
- The 0.9 API of to-dos and absences is gone (the `todos` module and its functions). Use the
  `Work` commands instead: `start`, `switch`, `resume`, `stop`, `reopen`, `delete`, `restore`,
  `save_todo`, `tick`, `complete`, `uncomplete`, `travel_from_work`, `travel_back`, `add_absence`, `add_segment`,
  `edit_segment`. Suspending is internal (no button): an activity is suspended when another
  one starts.
- The Debian package `Breaks` the 0.9 interfaces (`activitytrack-cli`, `activitytrack-tui`,
  `activitytrack-desktop` before 1.0.0~alfa-1), which need the 1.0 library.
- A PC is never the source of trust: after its migration the first sync is a full
  realignment, in which the server's version wins. Records the migration made that the server
  did not keep (for example a to-do the server absorbed into an activity from another device)
  are dropped, with the changes pending on them, and the user is told with a notice.

### Features

- Displayed states computed in one place, each with a name, an icon and a colour: to do,
  planned, late, work, travel, pause, suspended, done, incomplete, absence, deleted.
- Planned segments: an activity in the state "Da fare" can hold a plan as segments.
- Absence kinds (absence, holiday, leave, sick leave), an optional company, one segment per
  working day of the schedule, or the times given.
- Return trip: stopping an activity that travelled out and worked offers to start the return
  trip, to end it, or to end it adding a return trip as long as the outbound one.
- Closing ahead: a stop or the end of the last segment may lie up to 24 hours ahead of now.
- Saving rounded times (setting `roundStoredTimes`, off by default): the action buttons store
  times rounded to the quantum of each kind of segment, with the rules of the activity model
  (a start rounds down, a stop up, a switch to the nearest of the smaller quantum).
- Priorities are named (none to emergency); the bin replaces "ignored" for to-dos.
- An activity made of travel only can be declared complete: the action "Completa" (and
  "Annulla completamento") of `texts.entry_actions`, `Work.complete` and `Work.uncomplete`;
  it is then done unless a required field is empty, and reopening it clears the completion
  (activitytrack/activitytrack#189).
- Notices API: `Work.take_notices()` returns, translated and cleared, what a command leaves to
  tell the user (an end moved back to the start of a new activity, a planned segment or return
  trip removed); `texts.sync_notices()` and `Store.dismiss_notices()` do the same for the
  notices of the sync (a change that could not be applied, kept for 7 days unless dismissed).
- `i18n.pgettext(context, text)`: a text with another translation in a context (the state "Da fare" of
  an activity, "To do", against the list of the to-dos); the catalogs hold it as `context|text`.
- `i18n.resolve_language(store)`: the language of an interface in one place, the policy's
  `ui.language`, else the user's choice (state `language`), else the system's, else English. A
  `ui.language` that is there but is no language of ours forces English (the rest of the policy
  stays valid); `i18n.policy_language(store)` and `texts.language_note(store)` let an interface
  with a language setting say so.

### Behaviour

- Starting a past activity no longer suspends the activities running.
- A new activity that starts before the end ahead of an earlier one moves that end back to its
  start, with a notice; a planned segment wholly ahead is removed, with a notice, instead of
  being cut to nothing.
- A paused activity is suspended, not stopped, when another one starts.
- The language of an interface that does not know the system's is English (it was Italian when the
  system said nothing, and `LANGUAGE` lists and `C`/`POSIX` were not read as gettext does).
- The displayed states "Fatta" (done) and "Incompleta" (incomplete) are named as in the app and the web.
- The displayed states of a running activity are named as in the app: "Lavoro", "Viaggio", "Pausa"
  (English "Working", "Travelling", "Paused"), no longer "In lavoro", "In viaggio", "In pausa";
  `texts.MODES` says "In trasferta" for travel, as the app's notification does.
- `Work.segments` reads the segments once and again only when the store has changed: the lists of
  a few hundred activities no longer read the whole table for every row.

Various bug fixes.

Earlier versions: see the release notes of each tag.
