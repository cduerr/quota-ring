# Releasing Quota Ring

1. Confirm the launch branch is green on GitHub Actions.
2. Update the version in `pyproject.toml` and `src/quota_ring/__init__.py`.
3. Move the relevant entries in `CHANGELOG.md` from **Unreleased** to a dated
   version heading.
4. Run the complete local validation suite documented in `CONTRIBUTING.md`.
5. Merge the release change to `main`.
6. Create and push a matching version tag, for example:

   ```sh
   git tag -s v0.1.0 -m "Quota Ring 0.1.0"
   git push origin v0.1.0
   ```

The release workflow verifies that the tag and both package version declarations
match. It builds the Python distributions and an installer-ready source archive,
generates SHA-256 checksums, and creates a draft GitHub release. Review the draft
and its generated notes before publishing it.
