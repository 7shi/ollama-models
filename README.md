# ollama-models

A small command-line tool that reads an [Ollama](https://github.com/ollama/ollama)
model store (the `models` directory) directly, without a running server.
It can list models, copy them between stores, and delete them.

It has no dependencies beyond the Python standard library.

## Requirements

- Python 3.10 or higher

## Installation

### As a tool (recommended)

```bash
# Install as a tool
uv tool install https://github.com/7shi/ollama-models.git

# Add ~/.local/bin to PATH if not already added
export PATH="$HOME/.local/bin:$PATH"
```

### From source

```bash
# Source installation with uv
git clone https://github.com/7shi/ollama-models.git
cd ollama-models
uv sync
```

**Note**: When using source installation, prefix all commands with `uv run` (e.g., `uv run ollama-models ls`). `python -m ollama_models` works only after the package is installed (e.g., `pip install -e .`), because the version is read from the package metadata.

## Usage

```
ollama-models ls [DIR]
ollama-models cp SRC_DIR MODEL... [-d DEST_DIR] [-y]
ollama-models merge SRC_DIR [-d DEST_DIR] [-y]
ollama-models rm DIR MODEL... [-y]
```

`DIR`, `SRC_DIR` and `DEST_DIR` are a models directory, or a `.ollama` directory
that contains one (`models` is appended automatically). When `DIR` (for `ls`) or
`DEST_DIR` is omitted, `$OLLAMA_MODELS` is used, falling back to
`~/.ollama/models`.

Model names follow Ollama's rules: `gemma3:4b`, `hf.co/unsloth/gpt-oss-20b-GGUF:latest`,
and so on. Omitted parts default to the `registry.ollama.ai` host, the `library`
namespace and the `latest` tag, and matching is case-insensitive.

### `ls`

Lists the models in a store with the same columns as `ollama list`
(`NAME`, `ID`, `SIZE`, `MODIFIED`), newest first. Both `manifests-v2/` and
`manifests/` are read, and for manifest lists the child for the preferred local
runner is shown. Cloud models show `-` as their size.

### `cp` and `merge`

`cp` copies the given models from another store; `merge` copies every model in
it. Both use the same process: build a plan first, then apply it.

| Destination state | Action |
|---|---|
| Model is missing | Added |
| Identical manifest | Skipped |
| Different content, source is newer | Overwritten, after confirmation |
| Different content, destination is newer or the same age | Skipped |

- All overwrites are listed up front and approved with a single `y/N` prompt.
  Declining skips only the overwrites; new models are still added. `-y` skips
  the prompt.
- The manifest, every blob it references (including the children of a manifest
  list) and the matching `metadata/*.json` cache files are copied. Existing blobs
  of the same size are not copied again.
- A model whose blobs are missing in the source is reported as an error before
  anything is written for it, and the other models are still processed.
- Files are written to a `.partial` file and renamed into place, so an
  interrupted run does not leave a half-written model.
- Each copied model is reported with a summary such as
  `copied gemma2:27b (2/4 blobs, 1 metadata)`: 2 of the 4 blobs the model
  references were copied (the rest already existed in the destination, often
  shared with another model), and 1 metadata cache file was copied.
- When a model is overwritten, its old blobs and metadata are deleted if no other
  model references them. If any manifest in the destination cannot be read, no
  cleanup is done.

### `rm`

Deletes the given models from a store, removing the manifest from both
`manifests/` and `manifests-v2/` when it exists in both. The models are listed
and confirmed with a single `y/N` prompt (`-y` skips it). Blobs and metadata that
no remaining model references are deleted, and directories left empty are
removed. A model with an unreadable manifest can still be deleted; the blob
cleanup is skipped in that case.

## Notes

- The tool never talks to the Ollama server. Avoid modifying a store that a
  running server is using.
- Model data is only read from and written to the store you point it at; the
  default location is used only when you omit the directory.
