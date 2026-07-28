# Third-party notices

The portal source currently has no third-party Python runtime dependencies and contains no vendored third-party source code.

It uses documented interfaces from:

- Python 3 standard library, distributed under the Python Software Foundation License. Python is not bundled by this repository.
- SQLite through Python's standard-library `sqlite3` module. SQLite is dedicated to the public domain and is not bundled by this repository.

The portal reads database files produced by `mcp-observatory`; it does not copy source code from that repository.

Review this file whenever a dependency, generated asset, copied schema fixture, font, icon, JavaScript library, stylesheet framework or deployment image is added.
