# python3-activitytrack

The library of [ActivityTrack](https://gitlab.com/activitytrack/activitytrack) on the PC: what its
interfaces share.

- [activitytrack-desktop](https://gitlab.com/activitytrack/activitytrack-desktop): the tray application;
- [activitytrack-cli](https://gitlab.com/activitytrack/activitytrack-cli): the command line;
- [activitytrack-tui](https://gitlab.com/activitytrack/activitytrack-tui): the text interface.

One application, whatever the interface: they share the data folder
(`~/.local/share/activitytrack`, `%APPDATA%\activitytrack`), the pairing with
[ActivityTrack Web](https://gitlab.com/activitytrack/activitytrack-web) and the token.

## Contents

| Module | What |
|---|---|
| `store` | the local copy (SQLite): records as the server sent them, the local changes field by field |
| `sync`, `net`, `wire` | the sync as a paired device (server `docs/SYNC.md`), HTTPS with the server's key pinned |
| `pairing`, `session`, `vault`, `paths` | the pairing link, who the PC is paired with, the token in the system's keyring, the data folder |
| `policy` | the user's policy, the keys the PC uses |
| `model`, `work` | the activity model 1.0 (to-do, running, closed, absence): the displayed state, the plan, the rounding of instants, and the commands (start, switch, resume, stop, tick, reopen, delete, absences), as the app's (`docs/ACTIVITY-MODEL.md`) |
| `migration` | the migration of a store of the schemas 1 and 2 to the activity model (reference of the app's repository, run when the store is opened) |
| `todos` | the to-do list of the interfaces over the activities (a compatibility layer until the interfaces use `work`) |
| `reminders` | the reminders' planner, as the app's |
| `updates`, `versions` | the updates of the PC from the paired server (its signed manifest, the pinned key of the pairing link), the order of versions |
| `manifest` | the signed update manifest: JWS check against pinned signers, channels, sha256 of the package |
| `i18n`, `texts`, `locale/` | the texts in it, en, de, es |

No graphical toolkit: it runs on a server without a display too.

## Packages

Debian 13 / 14 and Ubuntu 24.04 / 26.04: `python3-activitytrack`, from the
[releases](https://gitlab.com/activitytrack/python3-activitytrack/-/releases) (source package
`python-activitytrack`, built with pybuild). Elsewhere:
`pip install git+https://gitlab.com/activitytrack/python3-activitytrack@v<version>`.

## Development

```sh
apt install python3-cryptography python3-keyring flake8
python3 -m unittest discover -s tests -t .
python3 scripts/strings.py --check     # every text translated
flake8 activitytrack tests scripts
```

## License

GPL-3.0-or-later.
