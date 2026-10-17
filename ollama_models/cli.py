"""Inspect and manage Ollama model stores directly from the models directory.

Usage:
  ollama-models ls [DIR]                             list models (like `ollama list`)
  ollama-models cp SRC_DIR MODEL... [-d DEST_DIR] [-y]  copy the given models from another store
  ollama-models merge SRC_DIR [-d DEST_DIR] [-y]        copy every model from another store
  ollama-models rm DIR MODEL... [-y]                    delete models and their orphaned blobs

cp / merge add models missing from the destination and skip identical ones.
A model with the same name but different content is overwritten only when the
source is newer, after listing the candidates and asking for y/N confirmation
(-y skips the prompt). Models that are newer than, or as old as, the source are
skipped. Blobs and metadata replaced by an update are deleted once no model
references them any more.

DIR is a models directory, or a .ollama directory containing one. When omitted,
$OLLAMA_MODELS is used, falling back to ~/.ollama/models.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__

DEFAULT_HOST = "registry.ollama.ai"
DEFAULT_NAMESPACE = "library"
PUBLIC_HOSTS = {"registry.ollama.ai", "ollama.com"}
MEDIA_TYPE_LIST = "application/vnd.ollama.manifest.list.v2+json"
RUNNER_PREFERENCES = ["llamacpp", "ggml", "mlx"]
if sys.platform == "darwin" and os.uname().machine == "arm64":
    RUNNER_PREFERENCES = ["mlx", "llamacpp", "ggml"]


def human_bytes(b):
    for limit, unit in ((1000**4, "TB"), (1000**3, "GB"), (1000**2, "MB"), (1000, "KB")):
        if b >= limit:
            v = b / limit
            if v >= 10 or v == int(v):
                return f"{int(v)} {unit}"
            return f"{v:.1f} {unit}"
    return f"{b} B"


def human_duration(seconds):
    seconds = int(seconds)
    if seconds < 1:
        return "Less than a second"
    if seconds == 1:
        return "1 second"
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes == 1:
        return "About a minute"
    if minutes < 60:
        return f"{minutes} minutes"
    hours = round(seconds / 3600)
    if hours == 1:
        return "About an hour"
    if hours < 48:
        return f"{hours} hours"
    if hours < 24 * 7 * 2:
        return f"{hours // 24} days"
    if hours < 24 * 30 * 2:
        return f"{hours // 24 // 7} weeks"
    if hours < 24 * 365 * 2:
        return f"{hours // 24 // 30} months"
    return f"{seconds // 3600 // 24 // 365} years"


def human_time(mtime):
    delta = time.time() - mtime
    if delta < 0:
        return human_duration(-delta) + " from now"
    return human_duration(delta) + " ago"


def display_name(host, namespace, model, tag):
    name = f"{model}:{tag}"
    if host.lower() != DEFAULT_HOST:
        return f"{host}/{namespace}/{name}"
    if namespace.lower() != DEFAULT_NAMESPACE:
        return f"{namespace}/{name}"
    return name


def default_models_dir():
    return os.environ.get("OLLAMA_MODELS") or os.path.join(os.path.expanduser("~"), ".ollama", "models")


def resolve_models_dir(path):
    """Append models when given a directory that contains one, such as .ollama."""
    path = Path(path).expanduser()
    if (path / "models").is_dir() and not (path / "manifests").is_dir() and not (path / "manifests-v2").is_dir():
        return path / "models"
    return path


class Store:
    def __init__(self, root):
        self.root = resolve_models_dir(root)

    def blob(self, digest):
        return self.root / "blobs" / digest.replace(":", "-")

    def read_manifest(self, path):
        """Read a manifest and return (data, sha256 hex). Symlinks are followed."""
        data = path.read_bytes()
        return data, hashlib.sha256(data).hexdigest()

    def read_blob_manifest(self, digest):
        p = self.blob(digest)
        if not p.is_file():
            return None
        data = p.read_bytes()
        return json.loads(data), digest.removeprefix("sha256:")

    def config(self, mf):
        digest = (mf.get("config") or {}).get("digest")
        if not digest:
            return {}
        try:
            return json.loads(self.blob(digest).read_bytes())
        except (OSError, ValueError):
            return {}


def manifest_size(mf):
    return sum(l.get("size", 0) for l in (mf.get("layers") or []) + [mf.get("config") or {}])


def select_child(store, parent):
    """Pick the child of a manifest list for the most preferred runner present locally."""
    for runner in RUNNER_PREFERENCES:
        for child in parent.get("manifests") or []:
            if (child.get("runner") or "").lower() != runner:
                continue
            if child.get("mediaType") not in (None, "",
                                              "application/vnd.docker.distribution.manifest.v2+json"):
                continue
            if child.get("digest"):
                loaded = store.read_blob_manifest(child["digest"])
                if loaded:
                    return loaded
            elif child.get("config"):
                data = json.dumps(child, separators=(",", ":")).encode()
                return child, hashlib.sha256(data).hexdigest()
    return None


def manifest_paths(store):
    """Yield ((host, ns, model, tag), path) for every manifest in manifests-v2 and manifests."""
    for dirname in ("manifests-v2", "manifests"):
        base = store.root / dirname
        for path in sorted(base.glob("*/*/*/*")):
            if path.is_dir():
                continue
            host, ns, model, tag = path.relative_to(base).parts
            host = host.replace("%3A", ":").replace("%3a", ":")
            if host.lower() in PUBLIC_HOSTS:
                host = DEFAULT_HOST
            yield (host, ns, model, tag), path


def collect(store):
    """Collect {(host, ns, model, tag): path}, preferring manifests-v2 over manifests."""
    seen = {}
    for key, path in manifest_paths(store):
        seen.setdefault(key, path)
    return seen


def list_models(store):
    rows = []
    for (host, ns, model, tag), path in collect(store).items():
        try:
            data, digest = store.read_manifest(path)
            mf = json.loads(data)
            mtime = path.lstat().st_mtime
            if mf.get("mediaType") == MEDIA_TYPE_LIST:
                picked = select_child(store, mf)
                if not picked:
                    continue
                mf, digest = picked
            cfg = store.config(mf)
        except (OSError, ValueError) as e:
            print(f"warning: bad manifest {path}: {e}", file=sys.stderr)
            continue

        name = display_name(host, ns, model, tag)
        if name == f"llamacpp:{digest}":  # downgrade guard tag
            continue
        size = "-" if cfg.get("remote_model") else human_bytes(manifest_size(mf))
        rows.append((mtime, name, digest[:12], size))

    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows


def cmd_ls(args):
    store = Store(args.dir or default_models_dir())
    table = [("NAME", "ID", "SIZE", "MODIFIED")]
    for mtime, name, digest, size in list_models(store):
        table.append((name, digest, size, human_time(mtime)))

    widths = [max(len(r[i]) for r in table) for i in range(4)]
    for r in table:
        print("    ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


def parse_name(s):
    """Split host/namespace/model:tag into a tuple, filling omitted parts with defaults."""
    tag = "latest"
    if s.rfind(":") > s.rfind("/"):
        s, tag = s.rsplit(":", 1)
    parts = s.split("/")
    if not 1 <= len(parts) <= 3 or not all(parts):
        raise ValueError(f"invalid model name: {s}")
    model = parts[-1]
    namespace = parts[-2] if len(parts) >= 2 else DEFAULT_NAMESPACE
    host = parts[-3] if len(parts) == 3 else DEFAULT_HOST
    return host, namespace, model, tag


def blobs_of(store, mf):
    """List the blob digests a manifest references, including manifest-list children."""
    digests = []
    cfg = (mf.get("config") or {}).get("digest")
    if cfg:
        digests.append(cfg)
    digests += [l["digest"] for l in (mf.get("layers") or []) if l.get("digest")]
    for child in mf.get("manifests") or []:
        if child.get("digest"):
            digests.append(child["digest"])
            loaded = store.read_blob_manifest(child["digest"])
            if not loaded:
                raise FileNotFoundError(f"child manifest blob not found: {child['digest']}")
            digests += blobs_of(store, loaded[0])
        else:
            digests += blobs_of(store, child)
    return list(dict.fromkeys(digests))


def copy_blob(src, dst, digest):
    s, d = src.blob(digest), dst.blob(digest)
    if not s.is_file():
        raise FileNotFoundError(f"blob not found in source: {s}")
    if d.is_file() and d.stat().st_size == s.stat().st_size:
        return False
    d.parent.mkdir(parents=True, exist_ok=True)
    tmp = d.with_name(d.name + ".partial")
    shutil.copyfile(s, tmp)
    os.replace(tmp, d)
    return True


def copy_metadata(src, dst, digest):
    """Copy the GGUF metadata cache for a blob if present (ollama re-extracts it otherwise)."""
    name = digest.replace(":", "-") + ".json"
    s, d = src.root / "metadata" / name, dst.root / "metadata" / name
    if not s.is_file() or d.exists():
        return False
    d.parent.mkdir(parents=True, exist_ok=True)
    tmp = d.with_name(d.name + ".partial")
    shutil.copyfile(s, tmp)
    os.replace(tmp, d)
    return True


def make_plan(src, dst, items):
    """Decide an action (new/update/same/older/error) for each (key, src_path) in items."""
    dst_index = {tuple(x.lower() for x in k): p for k, p in collect(dst).items()}
    plan = []
    for key, path in items:
        item = {"name": display_name(*key), "src": path, "action": None, "error": None}
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            dpath = dst_index.get(tuple(x.lower() for x in key))
            if dpath is None:
                item["action"] = "new"
                item["target"] = dst.root / path.relative_to(src.root)
            else:
                item["target"] = dpath
                item["src_mtime"] = path.stat().st_mtime
                item["dst_mtime"] = dpath.stat().st_mtime
                if hashlib.sha256(dpath.read_bytes()).hexdigest() == digest:
                    item["action"] = "same"
                elif item["src_mtime"] > item["dst_mtime"]:
                    item["action"] = "update"
                else:
                    item["action"] = "older"
            if item["action"] in ("new", "update"):
                digests = blobs_of(src, json.loads(data))
                for d in digests:
                    if not src.blob(d).is_file():
                        raise FileNotFoundError(f"blob not found in source: {src.blob(d)}")
                item["digests"] = digests
        except (OSError, ValueError) as e:
            item["action"], item["error"] = "error", str(e)
        plan.append(item)
    return plan


def confirm_updates(updates):
    print(f"{len(updates)} model(s) will be overwritten with newer ones "
          "(blobs no longer referenced by any model will be deleted):")
    rows = [("NAME", "DEST MODIFIED", "SRC MODIFIED")]
    rows += [(u["name"], human_time(u["dst_mtime"]), human_time(u["src_mtime"])) for u in updates]
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    for r in rows:
        print("  " + "    ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    try:
        answer = input("Overwrite? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def referenced_digests(store):
    """Return the digests referenced by every manifest in store. Raise if any is unreadable."""
    digests = set()
    for path in collect(store).values():
        if path.is_symlink():  # symlink to a blob: the blob itself is in use
            digests.add("sha256:" + path.resolve().name.removeprefix("sha256-"))
        digests.update(blobs_of(store, json.loads(path.read_bytes())))
    return digests


def remove_orphans(dst, old_digests):
    """Delete blobs and metadata no manifest references any more; return the blob count."""
    try:
        orphans = set(old_digests) - referenced_digests(dst)
    except (OSError, ValueError) as e:
        print(f"warning: orphan cleanup skipped (cannot read all manifests): {e}", file=sys.stderr)
        return 0
    removed = 0
    for d in orphans:
        meta = dst.root / "metadata" / (d.replace(":", "-") + ".json")
        for f in (dst.blob(d), meta):
            if f.is_file():
                f.unlink()
                removed += f is not meta
    return removed


def apply_item(src, dst, item):
    old_digests = []
    if item["action"] == "update":
        try:
            old_digests = blobs_of(dst, json.loads(item["target"].read_bytes()))
        except (OSError, ValueError):
            pass  # unreadable old manifest: skip the orphan cleanup
    copied = sum(copy_blob(src, dst, d) for d in item["digests"])
    metas = sum(copy_metadata(src, dst, d) for d in item["digests"])
    path, target = item["src"], item["target"]
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".partial")
    shutil.copyfile(path, tmp)
    shutil.copystat(path, tmp)
    os.replace(tmp, target)
    detail = f"{copied}/{len(item['digests'])} blobs, {metas} metadata"
    if old_digests:
        detail += f", {remove_orphans(dst, old_digests)} orphaned blobs removed"
    return detail


def copy_models(src, dst, items, yes):
    """Shared by cp and merge: plan, confirm updates, then apply."""
    if src.root.resolve() == dst.root.resolve():
        sys.exit("error: source and destination are the same directory")

    plan = make_plan(src, dst, items)
    updates = [it for it in plan if it["action"] == "update"]
    allow_update = bool(updates) and (confirm_updates(updates) if not yes else True)
    if updates and yes:
        names = ", ".join(u["name"] for u in updates)
        print(f"overwriting with newer: {names}")

    counts = {"new": 0, "update": 0, "same": 0, "older": 0, "declined": 0, "error": 0}
    for it in plan:
        action = it["action"]
        if action == "error":
            print(f"error: {it['name']}: {it['error']}", file=sys.stderr)
        elif action == "same":
            print(f"skip {it['name']} (identical)")
        elif action == "older":
            print(f"skip {it['name']} (destination is newer or same age)")
        elif action == "update" and not allow_update:
            print(f"skip {it['name']} (overwrite declined)")
            counts["declined"] += 1
            continue
        else:
            try:
                detail = apply_item(src, dst, it)
            except OSError as e:
                print(f"error: {it['name']}: {e}", file=sys.stderr)
                counts["error"] += 1
                continue
            print(f"{'updated' if action == 'update' else 'copied'} {it['name']} ({detail})")
        counts[action] += 1

    print(f"done: {counts['new']} new, {counts['update']} updated, "
          f"{counts['same'] + counts['older']} skipped, {counts['declined']} declined, {counts['error']} errors")
    sys.exit(1 if counts["error"] else 0)


def cmd_cp(args):
    src = Store(args.src)
    dst = Store(args.dest or default_models_dir())
    available = {tuple(x.lower() for x in k): (k, p) for k, p in collect(src).items()}
    items, missing = [], 0
    for name in args.models:
        try:
            key = parse_name(name)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            missing += 1
            continue
        found = available.get(tuple(x.lower() for x in key))
        if found is None:
            print(f"error: model not found in {src.root}: {name}", file=sys.stderr)
            missing += 1
        else:
            items.append(found)
    if missing and not items:
        sys.exit(1)
    try:
        copy_models(src, dst, items, args.yes)
    except SystemExit as e:
        sys.exit(1 if missing else e.code)


def cmd_merge(args):
    src = Store(args.src)
    dst = Store(args.dest or default_models_dir())
    items = sorted(collect(src).items(), key=lambda kp: display_name(*kp[0]))
    copy_models(src, dst, items, args.yes)


def prune_empty_dirs(path, stop):
    """Remove empty directories from path up to, but not including, stop."""
    while path != stop and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        path = path.parent


def cmd_rm(args):
    store = Store(args.dir)
    paths = {}
    for key, path in manifest_paths(store):
        paths.setdefault(tuple(x.lower() for x in key), (key, []))[1].append(path)

    targets, missing = {}, 0
    for name in args.models:
        try:
            lkey = tuple(x.lower() for x in parse_name(name))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            missing += 1
            continue
        if lkey not in paths:
            print(f"error: model not found in {store.root}: {name}", file=sys.stderr)
            missing += 1
        else:
            targets[lkey] = paths[lkey]
    if not targets:
        sys.exit(1)

    print(f"{len(targets)} model(s) will be deleted (blobs no longer referenced by any model will also be deleted):")
    for key, _ in targets.values():
        print("  " + display_name(*key))
    if not args.yes:
        try:
            answer = input("Delete? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted")
            sys.exit(1)

    old_digests, cleanup = [], True
    for key, files in targets.values():
        for path in files:
            try:
                if path.is_symlink():  # symlink to a blob: that blob may become orphaned too
                    old_digests.append("sha256:" + path.resolve().name.removeprefix("sha256-"))
                old_digests += blobs_of(store, json.loads(path.read_bytes()))
            except (OSError, ValueError) as e:
                print(f"warning: {display_name(*key)}: cannot read manifest, orphan cleanup skipped: {e}",
                      file=sys.stderr)
                cleanup = False
            path.unlink()
            for dirname in ("manifests-v2", "manifests"):
                base = store.root / dirname
                if base in path.parents:
                    prune_empty_dirs(path.parent, base)
        print(f"removed {display_name(*key)}")

    if cleanup:
        print(f"{remove_orphans(store, old_digests)} orphaned blobs removed")
    sys.exit(1 if missing else 0)


def add_copy_options(p):
    p.add_argument("src", help="source models directory (or .ollama)")
    p.add_argument("-d", "--dest", help="destination models directory (default: $OLLAMA_MODELS or ~/.ollama/models)")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation before overwriting with newer models")


def main():
    ap = argparse.ArgumentParser(description="Inspect and manage Ollama model stores directly from the models directory")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ls", help="list models (like `ollama list`)")
    p.add_argument("dir", nargs="?", help="models directory (or .ollama)")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("cp", help="copy the given models from another store")
    add_copy_options(p)
    p.add_argument("models", nargs="+", metavar="MODEL", help="model name to copy (e.g. gemma3:4b)")
    p.set_defaults(func=cmd_cp)

    p = sub.add_parser("rm", help="delete models and their orphaned blobs")
    p.add_argument("dir", help="models directory (or .ollama)")
    p.add_argument("models", nargs="+", metavar="MODEL", help="model name to delete (e.g. gemma3:4b)")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation before deleting")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("merge", help="copy every model from another store")
    add_copy_options(p)
    p.set_defaults(func=cmd_merge)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
