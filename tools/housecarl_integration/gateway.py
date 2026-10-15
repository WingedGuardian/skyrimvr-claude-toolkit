"""One policy and transaction boundary for CLI and MCP."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from . import Refusal
from .files import backup, contained, exclusive, json_write, plain_plugin, publish, sha
from .order import capture, plugin_header
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
        for key in ("ops", "assignments", "records", "formids"):
            value = a.get(key)
            if isinstance(value, str) and value.startswith("@"):
                a[key] = json.loads(Path(value[1:]).read_text(encoding="utf-8-sig"))
            elif key == "formids" and isinstance(value, list):
                expanded = []
                for token in value:
                    if isinstance(token, str) and token.startswith("@"):
                        expanded.extend(json.loads(Path(token[1:]).read_text(encoding="utf-8-sig")))
                    else:
                        expanded.append(token)
                a[key] = expanded
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


def independent_check(config, candidate, snapshot, before=None, allowed=None):
    """xEdit parses every candidate record; preserves untouched records by canonical JSON."""
    verifier = Path(config["root"]) / "tools/housecarl-verify.js"
    work = Path(tempfile.mkdtemp(prefix="hc-xedit-"))
    data = work / "Data"
    data.mkdir(parents=True)
    # Only a read-only verifier sees these master hardlinks; the writer never gets this directory.
    for name, original in snapshot.paths.items():
        dest = data / name
        if name.casefold() == candidate.name.casefold():
            shutil.copyfile(candidate, dest)
        else:
            try:
                os.link(original, dest)
            except OSError:
                shutil.copyfile(original, dest)
    if not (data / candidate.name).exists():
        shutil.copyfile(candidate, data / candidate.name)
    out = work / "inspection.json"
    env = os.environ.copy()
    env["HOUSECARL_XELIB_ROOT"] = config.get("xelib_root", config["root"])
    result = subprocess.run([config.get("node", "node"), str(verifier), str(work), candidate.name, str(out)],
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    # The installed xelib has a documented native teardown exit-code problem. Require both markers and data.
    if "RESULT: OK" not in result.stdout or not out.is_file():
        detail = (result.stderr + result.stdout).replace("\0", "")
        raise Refusal("Independent xEdit validation failed: " + detail[-1400:] + "; retained " + str(work))
    obj = json.loads(out.read_text(encoding="utf-8"))
    shutil.copyfile(out, candidate.parent / ("xedit-" + uuid.uuid4().hex + ".json"))
    # This is this call's own shallow scratch directory, never a backup or a save folder.
    if work.resolve().parent != Path(tempfile.gettempdir()).resolve() or not work.name.startswith("hc-xedit-"):
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


def allowed_records(arguments):
    keys = set(arguments.get("formids") or [])
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
            return {"context": context(self.config, snap), "result": result.model_dump(mode="json", exclude_none=True)}

    async def write(self, name, arguments, snap):
        config = self.config
        ident = uuid.uuid4().hex
        work = Path(config["state_dir"]) / "transactions" / ident
        work.mkdir(parents=True)
        a = copy.deepcopy(arguments)
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
                candidates = list((Path(config["state_dir"]) / "patches").glob("*/" + target_name))
                if len(candidates) != 1:
                    raise Refusal("Existing target must resolve to one active plugin or one staged patch.")
                original = candidates[0]
            if original.name.lower() in {"skyrim.esm", "update.esm", "dawnguard.esm", "hearthfires.esm",
                                         "dragonborn.esm", "skyrimvr.esm"}:
                raise Refusal("Edit a patch rather than rewriting an official master.")
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
