# Protected image SQLite runtime repair

The protected image keeps its existing digest-pinned Python 3.12.15 slim Debian
13 runtime and native subscription tools. Its SQLite library is now compiled
from the exact official SQLite 3.53.4 amalgamation in a separate digest-pinned
official Python 3.12.15 Debian 13 compiler stage. Only the shared SQLite library
enters the runtime. Python's standard `sqlite3` module is retained.

Official references checked 2026-10-08:

- [SQLite 3.53.4 release](https://sqlite.org/releaselog/3_53_4.html): exact source
  ID and SHA3-256 of `sqlite3.c`; incorporates the WAL-reset fix and subsequent
  patch fixes. This does not establish the cause of the preserved corruption.
- [Official downloads](https://sqlite.org/download.html): the amalgamation ZIP's
  published SHA3-256 is `628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e`.
- [Compilation options](https://sqlite.org/compile.html): thread-safe native
  compilation. Existing application connection and locking controls still apply.
- The official Docker registry's `python:3.12.15-trixie` amd64 manifest resolved
  to `sha256:3d361d7fea344d55ac7a0f51ed7faa99213808ccc96fc9b9170adb95b6570b96`.

Acquisition verifies both archive SHA256 and the published archive SHA3-256,
then independently verifies the official C-source SHA3-256 and exact release
version/source ID in both C and header. The locally calculated archive SHA256 is
`1e71ddf93849c6a6ecf58b827c0692073d2dd7ee40196158068f7b29f422e87d`.
The Docker build remains `--network none`; only reviewed public source/wheel
acquisition occurs beforehand. An optional `--sqlite-source` archive supports
offline acquisition with identical checks.

Before sealing and again in the final immutable image, the builder verifies
Python 3.12, SQLite version/source ID, thread safety, in-memory integrity, and
the actual mapped `/usr/local/lib/libsqlite3.so.0`. It records the library's
SHA256, compile options, Python version and libc version in the image seal.
Source, probe and compiler identities are retained with the build inputs.
The final image pin binds that seal and the complete image archive.

Verification at the source checkpoint:

```bash
PYTHONPATH=src python -m pytest tests/security/test_sqlite_image_runtime.py \
  tests/security/test_subscription_image_tools.py tests/security/test_deployment_image.py -q
ruff check scripts/build_protected_image.py tests/security/test_sqlite_image_runtime.py
```

The 86 focused tests passed and Ruff passed. Actual official source acquisition
and staging passed the complete configured hash/version checks. Tests reject
changed archives, C-source hashes, release identity, compiler tags, foreign
URLs, linked archives, duplicate/traversing ZIP members, and an unreviewed
loaded SQLite runtime. Actual final-image build, native/runtime compatibility,
and multi-process locking results belong to the separate private deployment
evidence. Source tests alone do not establish those results or authorize an
installation switch, AI resumption, provider spending, or real trading.
