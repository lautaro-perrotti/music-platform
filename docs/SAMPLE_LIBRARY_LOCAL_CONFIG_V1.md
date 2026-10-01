# Local sample-library configuration V1

Sample indexing is read-only and scans only roots explicitly registered by the
user. The CLI never searches arbitrary volumes or parent directories.

```powershell
python -m copilot.cli sample-library add "D:\\Audio\\My Samples"
python -m copilot.cli sample-library list
python -m copilot.cli sample-library index
python -m copilot.cli sample-library remove "D:\\Audio\\My Samples"
```

The config and generated index are stored outside the repository in the
platform-native per-user config directory. Set `MUSIC_PLATFORM_CONFIG_DIR` to
an explicit directory to override that location. The config filename is
`sample-library.json`; the index filename is `sample-library-index.json`.
`add` requires an existing directory and is idempotent. `index` fails closed
if no roots are configured or any configured root is unavailable. Duplicate
files are deduplicated by SHA-256; folder names are preserved as tags and are
not treated as authoritative semantic labels.

Supported formats are the existing local indexer's WAV, AIFF and FLAC inputs.
MP3 reference stems are not silently treated as library samples. Configured
paths and the index remain machine-local and must not be committed.
