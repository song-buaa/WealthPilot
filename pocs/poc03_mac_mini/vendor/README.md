# Fixed third-party wheel

`tigeropen-3.5.8-py3-none-any.whl` is the unmodified pure Python wheel already built and hashed in PoC 03 Stage 1. PyPI publishes this version as a source distribution only. Keeping this one 353 KiB asset in Git avoids an unpinned source build on Prod; other wheels are downloaded from their exact PyPI versions/filenames and verified against committed hashes.

Upstream package declares Apache-2.0. The Apache license is supplied in `LICENSE`; original source notices remain in the wheel. This is a third-party dependency, not WealthPilot business code. Provenance and source SHA-256: [arm-wheel-assets.json](../evidence/arm-wheel-assets.json), `tigeropen_source`; wheel hash: the matching `wheels` entry and `locks/business.txt`.
