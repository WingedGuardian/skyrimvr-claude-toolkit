"""One policy and transaction boundary for CLI and MCP."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from . import Refusal
from .files import backup, contained, exclusive, json_write, plain_plugin, publish, sha
from .order import BASE as IMPLICIT_MASTERS, capture, plugin_header
from .runtime import Backend, READS, WRITES, context, response_json


def load_config(root):
    state = Path(root).resolve() / ".skyguard/housecarl"
    path = state / "config.json"
    if not path.is_file():
        raise Refusal("House Carl is not configured. Run tools/housecarl.py setup with this install's paths.")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("root") != str(Path(root).resolve()) or config.get("state_dir") != str(state):
        raise Refusal("House Carl configuration belongs to another install; rerun setup.")
    return config


# The FormIDs a frozen @file list names, kept beside the backend's own `formids` so the gateway can derive
# its permitted edit set. Removed before the backend call; recorded in the receipt.
FROZEN_FORMIDS = "_gateway_formids"


def _refuse_list(label, why):
    raise Refusal(label + "=" + why + " Accepted list files: a JSON array of FormIDs, a plain list (one "
                  "XXXXXX:Plugin.esp per line or comma-separated), or a House Carl result artifact (.jsonl).")


def _formid_tokens(content, label, path):
    """House Carl 2.1.2's @file grammar for a FormID list (Artifacts.ExpandListInput, ResultArtifact.ReadIdentity):
    a result artifact (line 1 is a manifest carrying `housecarl_artifact`; error rows skipped) or a plain list
    split on commas and line breaks, with brackets, quotes and blanks stripped per token."""
    first = re.split(r"[\r\n]", content, maxsplit=1)[0].lstrip("﻿ \t")
    manifest = None
    if first.startswith("{"):
        try:
            parsed = json.loads(first)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and "housecarl_artifact" in parsed:
            manifest = parsed
    if manifest is None:
        tokens = [t.strip("[]\"' \t") for t in re.split(r"[,\r\n]", content)]
        tokens = [t for t in tokens if t]
        if not tokens:
            _refuse_list(label, "@" + path + " is empty.")
        return tokens
    identity = manifest.get("identity")
    if not isinstance(identity, str) or identity.casefold() != "formid":
        _refuse_list(label, "@" + path + " is a result artifact whose rows carry " + repr(identity)
                     + " identities, not FormIDs.")
    tokens = []
    for number, line in enumerate(content.splitlines()[1:], start=2):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            _refuse_list(label, "@" + path + " line " + str(number) + " is not a JSON row; regenerate the artifact.")
        if not isinstance(row, dict):
            _refuse_list(label, "@" + path + " line " + str(number) + " is not a JSON object row.")
        if "error" in row:
            continue        # a failed input of the producing call names no record
        if not isinstance(row.get(identity), str):
            _refuse_list(label, "@" + path + " line " + str(number) + " carries no '" + identity + "' value.")
        tokens.append(row[identity])
    if not tokens:
        _refuse_list(label, "@" + path + " has no record rows.")
    return tokens


def _freeze_formid_list(token, label, config):
    """Read one @file FormID list once, freeze its exact bytes under the state folder and return
    (frozen path, FormIDs). The backend re-reads the frozen copy, so its own artifact epoch check still runs."""
    path = token.strip()[1:].strip().strip("\"'")
    if not path:
        _refuse_list(label, "'@' names no file.")
    source = Path(path)
    if not source.is_absolute():
        _refuse_list(label, "@" + path + " must be an absolute path.")
    try:
        raw = source.read_bytes()
        content = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        _refuse_list(label, "@" + path + " could not be read (" + type(exc).__name__ + ").")
    tokens = _formid_tokens(content, label, path)
    digest = hashlib.sha256(raw).hexdigest()
    inputs = Path(config["state_dir"]) / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    frozen = inputs / (digest + (".jsonl" if content.lstrip("﻿ \t").startswith("{") else ".txt"))
    if not frozen.exists():
        temp = inputs / (digest + "." + uuid.uuid4().hex + ".tmp")
        temp.write_bytes(raw)
        os.replace(temp, frozen)
    if hashlib.sha256(frozen.read_bytes()).hexdigest() != digest:
        raise Refusal("A frozen input in " + str(inputs) + " does not match its name; remove it and retry.")
    return frozen, tokens


def _json_manifest(value, key):
    path = value[1:].strip().strip("\"'")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise Refusal(key + "=@" + path + " could not be read (" + type(exc).__name__ + ").") from None
    except ValueError as exc:
        raise Refusal(key + "=@" + path + " must be a JSON document (" + str(exc) + "). Only formids= also takes "
                      "plain lists and result artifacts.") from None


def check_arguments(name, arguments, config):
    if name not in READS | WRITES | {"toolkit_housecarl_status"}:
        raise Refusal("This integration does not expose " + name + "; use the existing toolkit workflow.")
    a = copy.deepcopy(arguments)
    if not isinstance(a, dict):
        raise Refusal("Tool arguments must be an object.")
    if name in WRITES:
        for key in ("in_place", "into"):
            if a.get(key):
                plain_plugin(a[key])
        if a.get("patch"):
            patch = a["patch"]
            if not isinstance(patch, str) or patch.strip() != patch or not patch or any(
                    c in patch for c in '/\\:<>"|?*\r\n\0') or patch in (".", ".."):
                raise Refusal("patch must be a base filename, without a path or wildcard.")
        # Freeze manifests into the transaction request before deriving its permitted edit set.
        for key in ("ops", "assignments", "records"):
            value = a.get(key)
            if isinstance(value, str) and value.startswith("@"):
                a[key] = _json_manifest(value, key)
        # A FormID @file follows House Carl's own grammar (issue 20): one @file stands in place of the whole list.
        value = a.get("formids")
        items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        if any(isinstance(t, str) and t.lstrip().startswith("@") for t in items):
            if len(items) != 1:
                _refuse_list("formids", "mixes an '@file' entry with inline entries; '@<path>' stands in place of "
                             "the whole list. Pass it alone, or put every entry in the file.")
            frozen, tokens = _freeze_formid_list(items[0], "formids", config)
            a["formids"] = ["@" + str(frozen)]
            a[FROZEN_FORMIDS] = tokens
    if name == "housecarl_copy" and a.get("into"):
        raise Refusal("Closure copy currently supports a new patch only; existing-patch copy is not qualified.")
    if name == "housecarl_load_order_status":
        if a.get("profile"):
            raise Refusal("This gateway inspects its configured authority only; alternate-profile status is unavailable.")
        if a.get("filter"):
            plain_plugin(a["filter"])
    if name == "housecarl_check":
        families = a.get("findings", ["errors"])
        if isinstance(families, str):
            families = [families]
        if "facegen" in families:
            raise Refusal("Facegen provenance is unavailable for the deployed-Data provider.")
        a["findings"] = families
    if name == "housecarl_skse":
        if a.get("findings") != "config":
            raise Refusal("On this provider, housecarl_skse supports findings='config' only; use skse-load-status for loaded DLLs.")
    if name == "housecarl_records" and a.get("formids"):
        tokens = a["formids"]
        if not isinstance(tokens, list):
            raise Refusal("formids must be a list.")
        if any(isinstance(t, str) and not t.startswith("@") and ":" not in t for t in tokens):
            raise Refusal("Runtime addresses are not qualified here; use XXXXXX:Plugin.esp FormKeys.")
    if a.get("to_file"):
        p = Path(a["to_file"])
        outputs = Path(config["state_dir"]) / "artifacts"
        if not p.is_absolute() or p.suffix.lower() != ".jsonl" or not contained(p, outputs):
            raise Refusal("to_file must name a .jsonl beneath " + str(outputs))
        outputs.mkdir(parents=True, exist_ok=True)
    return a


def _masters_of(candidate, active):
    """The active plugins xEdit loads for `candidate`: the implicit masters, then its masters' closure. Only these are
    linked (the user's exception covers masters, not the whole load order)."""
    by_name = {name.casefold(): (name, Path(path)) for name, path in active.items()}
    out, queue = {}, [plugin_header(candidate)["masters"]]
    queue.append([n for n in IMPLICIT_MASTERS])
    while queue:
        for wanted in queue.pop():
            found = by_name.get(wanted.casefold())
            if found is None or found[0] in out or wanted.casefold() == candidate.name.casefold():
                continue
            out[found[0]] = found[1]
            queue.append(plugin_header(found[1])["masters"])
    return out


def _drive(path):
    return Path(path).anchor.lower()


def _scratch_parent(config, masters):
    """A hard link needs one volume, and masters are never copied, so the scratch folder follows the masters."""
    drives = {_drive(path) for path in masters.values()}
    if len(drives) > 1:
        raise Refusal("The read-only check needs its masters on one drive, and they are on: " + ", ".join(sorted(drives)))
    temp = Path(tempfile.gettempdir())
    if not drives or _drive(temp) in drives:
        return temp
    root = Path(config["root"])
    if _drive(root) in drives:
        return root / ".housecarl-tmp"
    raise Refusal("The read-only check needs scratch space on the masters' drive ({}); set TEMP to a folder there.".format(
        ", ".join(sorted(drives))))


def independent_check(config, candidate, snapshot, before=None, allowed=None):
    """xEdit parses every candidate record; preserves untouched records by canonical JSON."""
    verifier = Path(config["root"]) / "tools/housecarl-verify.js"
    masters = _masters_of(candidate, snapshot.paths)
    parent = _scratch_parent(config, masters)
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="hc-xedit-", dir=str(parent)))
    try:                                  # housekeep's temp sweep retires a leftover only with this declaration
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import housekeep_backups
        housekeep_backups.mark_disposable_temp(work, Path(config["root"]))
    except Exception:
        pass                              # without it the sweep only keeps the folder
    data = work / "Data"
    data.mkdir(parents=True)
    out = work / "inspection.json"
    # USER EXCEPTION, 2026-11-02, this check only: the "never hard-link staged inputs" rule is waived here because xEdit
    # needs the candidate and every master in one folder. Conditions: the verifier is read-only (tools/housecarl-verify.js
    # never saves), the links last this one call, and masters are never copied. A linked view is the real file under a
    # second name, so it is gone before this call returns, also when the check fails or times out, and every master is
    # compared afterwards: House Carl's backups cover the plugin being edited, not the masters.
    before_stats = {}
    try:
        for name, original in masters.items():
            info = os.stat(original)
            if not info.st_mode & stat.S_IWRITE:
                # Its link could not be removed without changing the attribute the real file shares.
                raise Refusal("Master {} is read-only, so the read-only check cannot link it safely. Clear that "
                              "attribute, or lift the toolkit's file lock, and retry.".format(name))
            try:
                os.link(original, data / name)
            except OSError as exc:
                raise Refusal("Cannot link master {} for the read-only check ({}). If the toolkit's file lock is on, "
                              "lift it and retry; masters are never copied.".format(name, exc)) from exc
            before_stats[name] = (original, info.st_size, info.st_mtime_ns)
        shutil.copyfile(candidate, data / candidate.name)
        env = os.environ.copy()
        env["HOUSECARL_XELIB_ROOT"] = config.get("xelib_root", config["root"])
        result = subprocess.run([config.get("node", "node"), str(verifier), str(work), candidate.name, str(out)],
                                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    finally:
        shutil.rmtree(data, ignore_errors=True)
        if data.exists():
            raise Refusal("The read-only check could not remove its links in {}: remove that folder.".format(data))
        for name, (original, size, mtime) in before_stats.items():
            info = os.stat(original)
            if (info.st_size, info.st_mtime_ns) != (size, mtime):
                raise Refusal("Master {} changed during the read-only check. It is not backed up here: restore it from "
                              "your mod manager or Steam.".format(name))
    # The installed xelib has a documented native teardown exit-code problem. Require both markers and data.
    if "RESULT: OK" not in result.stdout or not out.is_file():
        detail = (result.stderr + result.stdout).replace("\0", "")
        raise Refusal("Independent xEdit validation failed: " + detail[-1400:] + "; retained " + str(work))
    obj = json.loads(out.read_text(encoding="utf-8"))
    shutil.copyfile(out, candidate.parent / ("xedit-" + uuid.uuid4().hex + ".json"))
    # This is this call's own shallow scratch directory, never a backup or a save folder.
    if work.resolve().parent != parent.resolve() or not work.name.startswith("hc-xedit-"):
        raise Refusal("Unexpected verifier scratch location; retained for inspection.")
    shutil.rmtree(work)
    if not obj.get("complete") or not isinstance(obj.get("records"), dict):
        raise Refusal("xEdit returned an incomplete record inventory.")
    if before is not None:
        new_errors = set(obj.get("errors", [])) - set(before.get("errors", []))
        if new_errors:
            raise Refusal("Candidate introduced xEdit reference errors: " + "; ".join(sorted(new_errors)[:5]))
        changed = {k for k in set(before["records"]) | set(obj["records"])
                   if before["records"].get(k) != obj["records"].get(k)}
        unauthorized = changed - set(allowed or [])
        if unauthorized:
            raise Refusal("Unexpected changes outside requested records: " + ", ".join(sorted(unauthorized)[:12]))
        obj["changed_records"] = sorted(changed)
    elif obj.get("errors"):
        raise Refusal("xEdit found reference errors: " + "; ".join(obj["errors"][:5]))
    return obj


def staged_patches(state_dir, name):
    """Every staged patch named `name` (any case). Compared as a NAME, never used as a glob pattern: plain_plugin
    allows [ ], which Path.glob reads as a character class, so `Foo [Fix].esp` missed itself and could resolve to
    a different staged patch such as `Foo F.esp` (review L5 F3)."""
    root = Path(state_dir) / "patches"
    if not root.is_dir():
        return []
    return [p for d in sorted(root.iterdir()) if d.is_dir() for p in sorted(d.iterdir())
            if p.is_file() and p.name.casefold() == name.casefold()]


def allowed_records(arguments):
    keys = set(arguments.get(FROZEN_FORMIDS) or arguments.get("formids") or [])
    ops = arguments.get("ops")
    if isinstance(ops, list):
        keys |= {o.get("formid", "") for o in ops if isinstance(o, dict)}
    if arguments.get("target"):
        keys.add(arguments["target"])
    assignments = arguments.get("assignments")
    if isinstance(assignments, list):
        keys |= {r.get("target", "") for r in assignments if isinstance(r, dict)}
    records = arguments.get("records")
    if isinstance(records, list):
        keys |= {r.get("parent", "") for r in records if isinstance(r, dict)}
    return {str(k).casefold().lstrip("0") for k in keys if isinstance(k, str)}


class Gateway:
    def __init__(self, config):
        self.config = config

    async def call(self, name, arguments):
        a = check_arguments(name, arguments, self.config)
        with exclusive(Path(self.config["state_dir"]) / "transaction.lock"):
            snap = capture(self.config)
            if name in {"toolkit_housecarl_status", "housecarl_load_order_status"}:
                selected = a.get("filter")
                matches = lambda n: not selected or n.casefold() == selected.casefold()
                return {"context": context(self.config, snap), "active_plugins": [n for n in snap.names if matches(n)],
                        "inactive_plugins": [n for n in snap.inactive if matches(n)], "filter": selected,
                        "note": "Captured authority membership, not a disk inventory. No source load-order file was rewritten."}
            if name in WRITES:
                return await self.write(name, a, snap)
            async with Backend(self.config, snap) as backend:
                result = await backend.call(name, a)
            if capture(self.config).fingerprint != snap.fingerprint:
                raise Refusal("Inputs changed during the read; discard this response and retry.")
            # Issue 11: an absence is conclusive only when every archive input was qualified; say so in the response.
            return {"context": context(self.config, snap), "absence_conclusive": snap.archive_coverage["complete"],
                    "result": result.model_dump(mode="json", exclude_none=True)}

    async def write(self, name, arguments, snap):
        config = self.config
        ident = uuid.uuid4().hex
        work = Path(config["state_dir"]) / "transactions" / ident
        work.mkdir(parents=True)
        a = copy.deepcopy(arguments)
        a.pop(FROZEN_FORMIDS, None)      # gateway-only; the backend reads the frozen @file itself
        target_name = a.get("in_place") or a.get("into")
        if a.get("in_place") and a.get("into"):
            raise Refusal("Choose one write lane.")
        original, preservation, baseline, digest = None, None, None, None
        writer_snap = copy.deepcopy(snap)
        if target_name:
            target_name = plain_plugin(target_name)
            original = next((p for n, p in snap.paths.items() if n.casefold() == target_name.casefold()), None)
            if original is None:
                # A previously staged, still inactive patch may be extended.
                candidates = staged_patches(config["state_dir"], target_name)
                if len(candidates) != 1:
                    raise Refusal("Existing target must resolve to one active plugin or one staged patch.")
                original = candidates[0]
            if original.name.lower() in {"skyrim.esm", "update.esm", "dawnguard.esm", "hearthfires.esm",
                                         "dragonborn.esm", "skyrimvr.esm"}:
                raise Refusal("Edit a patch rather than rewriting an official master.")
            if not arguments.get("dry_run"):     # backups are kept forever; a dry run publishes nothing to restore from
                preservation = backup(original, Path(config["state_dir"]) / "backups")
                digest = preservation["sha256"]
            staged = work / "mods/Target" / original.name
            staged.parent.mkdir(parents=True)
            shutil.copyfile(original, staged)
            baseline = independent_check(config, staged, snap)
            if original.name not in writer_snap.paths:
                writer_snap.names.append(original.name)
                writer_snap.paths[original.name] = original
                writer_snap.headers[original.name] = plugin_header(original)
            a.pop("into", None)
            a["in_place"] = original.name
            a["acknowledge"] = True
        if name not in {"housecarl_copy", "housecarl_create_plugin"}:
            a["format"] = "json"
        if name in {"housecarl_apply", "housecarl_create", "housecarl_forward"}:
            a["readback"] = True
        # Backend will create the profile; add Target as its highest priority mod afterwards.
        async with Backend(config, writer_snap, work) as backend:
            if original:
                modlist = work / "profile/modlist.txt"
                modlist.write_text("+Target\n" + modlist.read_text(encoding="utf-8"), encoding="utf-8")
            raw_result = await backend.call(name, a)
            result = ({"ok": True, "text": "\n".join(getattr(c, "text", "") for c in raw_result.content)}
                      if name in {"housecarl_copy", "housecarl_create_plugin"} else response_json(raw_result))
        if result.get("ok") is not True:
            raise Refusal("No publication: " + json.dumps(result, ensure_ascii=True)[:1600])
        if arguments.get("dry_run"):
            return {"context": context(config, snap), "dry_run": True, "result": result}
        if (name in {"housecarl_apply", "housecarl_create"} and result.get("verify_ran") is not True) or result.get("ops_record_absent", 0) or result.get("records_absent", 0):
            raise Refusal("Backend file readback was incomplete; no publication.")
        for row in result.get("created", []):
            if row.get("verified") is not True:
                raise Refusal("Created record was not verified in the written file.")
        op_rows = list(result.get("ops", []))
        for created in result.get("created", []):
            op_rows.extend(created.get("ops", []))
        for row in op_rows:
            if row.get("applied") is not True or row.get("error") or row.get("landed_source") not in {"written_file", "superseded"}:
                raise Refusal("An edit lacks successful written-file readback; no publication.")
        for total, rendered in (("total_ops", "rendered_ops"), ("total_created", "rendered_created"),
                                ("total_forwarded", "rendered_forwarded"), ("total_removed", "rendered_removed")):
            if total in result and result.get(rendered) != result[total]:
                raise Refusal("Write verification response was capped; raise max_chars and retry.")
        candidates = list((work / "mods").rglob("*.esp")) + list((work / "mods").rglob("*.esm")) + list((work / "mods").rglob("*.esl"))
        if len(candidates) != 1:
            raise Refusal("Expected exactly one candidate plugin; nothing was published.")
        candidate = candidates[0]
        plugin_header(candidate)
        allowed = allowed_records(arguments)
        if name == "housecarl_create":
            allowed |= {r["formid"].casefold().lstrip("0") for r in result.get("created", []) if r.get("formid")}
        verified = independent_check(config, candidate, snap, baseline, allowed)
        if capture(config).fingerprint != snap.fingerprint:
            raise Refusal("Authoritative inputs changed during preparation; nothing published.")
        target = original if original else Path(config["state_dir"]) / "patches" / ident / candidate.name
        target.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"id": ident, "state": "prepared", "tool": name, "arguments": arguments,
                   "target": str(target), "before_sha256": digest, "backup": preservation,
                   "candidate_sha256": sha(candidate), "validation": verified,
                   "context": context(config, snap), "backend_result": result}
        receipt_path = work / "receipt.json"
        json_write(receipt_path, receipt)
        try:
            after = publish(candidate, target, digest)
            receipt.update(state="published", after_sha256=after,
                           note="Active file only; no alternate supplier changed. Redeployment can replace this edit."
                           if original else "Staged patch; activate manually in the mod manager.")
            json_write(receipt_path, receipt)
        except BaseException:
            receipt["state"] = "publication-failed-check-target"
            json_write(receipt_path, receipt)
            raise
        return {"receipt": str(receipt_path), **receipt}

    def restore(self, ident):
        if len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
            raise Refusal("Restore requires a transaction ID.")
        state = Path(self.config["state_dir"])
        with exclusive(state / "transaction.lock"):
            path = state / "transactions" / ident / "receipt.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            record = receipt.get("backup")
            if not record:
                raise Refusal("This transaction created a new patch and has no overwritten original.")
            target, saved = Path(receipt["target"]), Path(record["backup"])
            if not contained(saved, state / "backups") or sha(saved) != record["sha256"]:
                raise Refusal("Backup identity/hash verification failed.")
            if not any(contained(target, p) for p in [self.config["data_dir"], str(state / "patches"),
                                                     self.config.get("mods_dir", str(state / "patches")),
                                                     self.config.get("overwrite_dir", str(state / "patches"))]):
                raise Refusal("Restore target is outside integration write roots.")
            if target.suffix.lower() not in (".esp", ".esm", ".esl"):
                raise Refusal("Restore target is not a plugin.")
            if receipt.get("state") != "published" or sha(target) != receipt.get("after_sha256"):
                raise Refusal("Target no longer matches the published transaction; resolve the conflict explicitly.")
            current = backup(target, state / "backups")
            publish(saved, target, current["sha256"])
            receipt.update(state="restored", restore_backup=current)
            json_write(path, receipt)
            return {"restored": str(target), "sha256": sha(target), "receipt": str(path)}
