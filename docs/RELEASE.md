# Release Process

This document describes the release process for the `transcoder` addon.

## Tag Nomenclature

Releases are triggered by pushing a git tag to the repository. The tag should follow semantic versioning and be prefixed with a `v`.

Example tags:
- `v1.0.0`
- `v1.1.0`
- `v2.0.1`

## How to Trigger a Release

1. Ensure all changes are committed and pushed to the main branch.
2. Create a new tag locally:
   ```bash
   git tag v1.0.0
   ```
3. Push the tag to GitHub:
   ```bash
   git push origin v1.0.0
   ```

## Workflow Details

Once a tag matching `v*` is pushed, a GitHub Actions workflow (`release.yml`) is automatically triggered.

The workflow performs the following steps:
1. **Checkout**: Downloads the repository content.
2. **Package**: Creates a zip file named `transcoder.zip`.
   - The zip file contains a root directory named `transcoder/`.
   - All addon files (Python files, `docs/`, `LICENSE`, `README.md`) are placed inside this directory.
   - Developer-only files (like `docs/RELEASE.md`), configuration files (`config.json`), and git-related files are excluded.
3. **Release**: Creates a new GitHub Release.
   - The release is named after the tag (e.g., `v1.0.0`).
   - The `transcoder.zip` file is uploaded as a release asset.
   - **Note**: The release is initially created as a **Draft**. You must manually review and publish it on GitHub.

## Expected Output

The primary output of the release process is a `transcoder.zip` file attached to the GitHub Release. This zip file is ready for distribution to users of USDB_Syncer.

### Important Note on GitHub Assets

GitHub automatically generates two files for every release:
- `Source code (zip)`
- `Source code (tar.gz)`

**These files should be ignored.** They are not compatible with USDB_Syncer because they contain the entire repository structure (including developer files) and are named based on the repository name rather than the addon name.

Always use the **`transcoder.zip`** asset created by the workflow, as it has the specific directory structure required for the addon to be loaded correctly by USDB_Syncer.
