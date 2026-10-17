# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `cp` / `merge` / `rm` no longer fail on manifest lists whose children for other runners (e.g. `mlx` on Windows) were never pulled.

## [0.1.1] - 2026-10-18

### Added

- `--version` option.

### Fixed

- `cp`/`merge` crashed with `TypeError` on cloud models (`*-cloud` tags),
  whose manifests have `"layers": null`.

## [0.1.0] - 2026-10-18

### Added

- Initial release: list (`ls`), copy (`cp`, `merge`) and delete (`rm`) models
  in an Ollama model store directly, without a running server.
