## What and why

<!-- The diff says what changed. Say why. -->

## How was this verified?

<!--
Tests are necessary, not sufficient. If this touches how we talk to Audiobookshelf, say
what you ran it against and what you saw. Its docs are wrong in several places that matter.
-->

- [ ] `ruff check custom_components tests` and `ruff format --check`
- [ ] `mypy custom_components/audiobookshelf_plus --strict`
- [ ] `pytest --cov-fail-under=95`
- [ ] Exercised against a real Audiobookshelf server (version: ______)

## If this touches playback

Progress is a user's place in a book. Overwriting it is not recoverable.

- [ ] No position is written to the server that a speaker did not actually report
- [ ] Sessions are closed without sync data when the position is unknown
