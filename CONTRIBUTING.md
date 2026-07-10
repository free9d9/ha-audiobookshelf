# Contributing

## Getting a dev environment

Home Assistant needs Python 3.14 and cannot be imported on Windows (it imports `fcntl`).
On Windows, run the suite in a container:

```bash
docker run -d --name abs-test -v "$PWD:/w" -w /w python:3.14 sleep infinity
docker exec abs-test pip install -r requirements-test.txt
docker exec abs-test pytest
```

On Linux or macOS:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
pytest
```

`requirements-test.txt` pins `pytest-homeassistant-custom-component` to the exact Home
Assistant release this integration targets. Bump both together.

## Before you open a pull request

```bash
ruff check custom_components tests
ruff format --check custom_components tests
mypy custom_components/audiobookshelf_plus --strict
pytest --cov-fail-under=95
```

All four run in CI. Coverage is currently 99% with every module at or above 98%; please
do not regress it. New behaviour needs a test.

## What this project cares about

**Verify against a real server, not the docs.** The Audiobookshelf API documentation is
wrong in several places that matter, and every one of them was found by trying it:

- The documented cover endpoint 404s. The real one is `/api/items/{id}/cover`, and it needs
  no auth.
- There is no `scan_start` / `scan_complete` socket event, despite the docs' table of
  contents. Scans surface as `task_started` / `task_finished` with
  `data.action === 'library-scan'`.
- An API key is rejected on the Socket.IO handshake, even though it works everywhere else.
- `closeSession()` emits `user_stream_update` *before* `removeSession()`, so a close is
  byte-identical to a start.
- Sessions are never closed, so `/api/sessions/open` is full of sessions that are days old.

If you are changing behaviour that depends on the server, say in the PR how you checked.

**Two bugs in this repository once rewound people's audiobooks.** Both were found by
playing a real book on a real speaker, and neither would have been caught by reading the
code:

1. A player does not advertise `SEEK` until it has media loaded, so checking capabilities
   before `play_media` silently skipped the seek - and the sync then wrote the start of the
   track over the listener's saved position.
2. A stopped player reports no position at all, and `0.0` was written to the server.

Both have regression tests. **Never write a position to Audiobookshelf that a speaker did
not actually report.** When in doubt, close the session without sync data; the server then
keeps whatever it had.

## Commit messages

Explain *why*, not *what* - the diff already says what. If a decision turned on something
surprising about the Audiobookshelf API, put it in the message; the next person will
otherwise assume it was arbitrary and "fix" it.

## Upstream

Some work belongs in [`aioaudiobookshelf`](https://github.com/music-assistant/aioaudiobookshelf)
rather than here. See [`upstream/README.md`](upstream/README.md).
