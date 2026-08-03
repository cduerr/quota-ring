# Contributing

Thank you for helping improve Quota Ring.

## Development setup

Quota Ring targets Python 3.10 or newer on Linux. On Ubuntu, install the desktop
dependencies first:

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1
```

Run the automated checks from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src
ruff check .
shellcheck scripts/*.sh packaging/quota-ring.in
```

Run the development build with the system Python so it can use the system GI
bindings:

```sh
PYTHONPATH=src /usr/bin/python3 -m quota_ring.app
```

## Pull requests

Keep changes focused, add tests for behavioral changes, and update the README or
changelog when user-visible behavior changes. Provider parsers should include a
fixture representing the CLI output that motivated the change.
