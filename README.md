# python3-activitytrack

The library of [ActivityTrack](https://github.com/zipclaude/activitytrack) on the PC: what its
interfaces share.

- [activitytrack-desktop](https://github.com/zipclaude/activitytrack-desktop): the tray application;
- [activitytrack-cli](https://github.com/zipclaude/activitytrack-cli): the command line;
- [activitytrack-tui](https://github.com/zipclaude/activitytrack-tui): the text interface.

One application, whatever the interface: they share the data folder
(`~/.local/share/activitytrack`, `%APPDATA%\activitytrack`), the pairing with
[ActivityTrack Web](https://github.com/zipclaude/activitytrack-web) and the token.

## Contents

| Module | What |
|---|---|
| `store` | the local copy (SQLite): records as the server sent them, the local changes field by field |
| `sync`, `net`, `wire` | the sync as a paired device (server `docs/SYNC.md`), HTTPS with the server's key pinned |
| `pairing`, `session`, `vault`, `paths` | the pairing link, who the PC is paired with, the token in the system's keyring, the data folder |
| `policy` | the user's policy, the keys the PC uses |
| `work`, `todos` | the rules of activities and to-dos, as the app's |
| `reminders` | the reminders' planner, as the app's |
| `updates` | newer releases on GitHub |
| `i18n`, `texts`, `locale/` | the texts in it, en, de, es |

No graphical toolkit: it runs on a server without a display too.

## Packages

Debian 13 / 14 and Ubuntu 24.04 / 26.04: `python3-activitytrack`, from the
[releases](https://github.com/zipclaude/python3-activitytrack/releases) (source package
`python-activitytrack`, built with pybuild). Elsewhere:
`pip install git+https://github.com/zipclaude/python3-activitytrack@v<version>`.

## Development

```sh
apt install python3-cryptography python3-keyring flake8
python3 -m unittest discover -s tests -t .
python3 scripts/strings.py --check     # every text translated
flake8 activitytrack tests scripts
```

## License

GPL-3.0-or-later.
