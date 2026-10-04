#!/usr/bin/env bash
#
# esp-verify-wrapper.sh — cross-reference inventory guard for ESP/ESM operations.
#
# WHY THIS EXISTS
#   A whole class of plugin corruption is silent: a bulk ModKey/master remap
#   (e.g. a blanket plugin-name find/replace during a mod split, or an
#   over-broad rename) can re-master a record's cross-references from
#   :Skyrim.esm to a local plugin — no crash, no log, just dead links that
#   only surface as broken behavior in-game. (Note: Spriggit round-trips are
#   byte-stable and are NOT a cause of this; the risk is bulk remaps.)
#   This wrapper makes that corruption impossible to miss.
#
# WHAT IT DOES
#   Builds a full inventory of every record's every cross-reference
#   (FormID + target master) by serializing the plugin to YAML, then diffs a
#   before/after pair. It LOUDLY reports — and exits non-zero on — any reference
#   whose target master changed or that vanished entirely.
#
#   Tool-agnostic: guards Spriggit edits, xEditLib scripts, CK saves, manual
#   YAML find/replace — anything that rewrites a plugin.
#
# USAGE
#   tools/esp-verify-wrapper.sh snapshot <plugin> [<plugin> ...]
#       Record the current cross-reference inventory of each plugin.
#
#   tools/esp-verify-wrapper.sh verify   [--allow-empty] <plugin> [<plugin> ...]
#       Re-inventory and diff against the stored snapshot. Exit 1 on any
#       master change or dropped reference. Also exit 1 (REFUSING PASS) when the
#       snapshot or the new inventory holds zero cross-references, unless
#       --allow-empty says the plugin genuinely has none.
#
#   tools/esp-verify-wrapper.sh guard [--allow-empty] <plugin> [<plugin> ...] -- <command...>
#       snapshot -> run <command> -> verify, in one shot.
#
# Snapshots are stored OUTSIDE the game directory ($HOME/.esp-verify-snapshots)
# so running this never trips the game-directory edit hooks.
#
# CONFIG (env overrides): $SPRIGGIT (path or PATH name; default "spriggit"),
#   $ESP_VERIFY_SNAPDIR, $ESP_VERIFY_RELEASE (default SkyrimSE),
#   $ESP_VERIFY_PKGVER (default 0.40.0).
#
set -uo pipefail

SPRIGGIT="${SPRIGGIT:-spriggit}"
SNAPDIR="${ESP_VERIFY_SNAPDIR:-$HOME/.esp-verify-snapshots}"
GAME_RELEASE="${ESP_VERIFY_RELEASE:-SkyrimSE}"
PKG=Spriggit.Yaml
PKGVER="${ESP_VERIFY_PKGVER:-0.40.0}"

mkdir -p "$SNAPDIR"

die() { echo "esp-verify: $*" >&2; exit 2; }
[ -x "$SPRIGGIT" ] || command -v "$SPRIGGIT" >/dev/null 2>&1 || die "spriggit not found at $SPRIGGIT (override with \$SPRIGGIT)"

# build_inventory <plugin-path> <output-inv-file>
#   Emits one TSV line per cross-reference:  <EditorID>\t<FORMID:Master>
#   sorted, so two inventories diff cleanly with comm.
build_inventory() {
  local plugin="$1" out="$2" tmp
  [ -f "$plugin" ] || die "plugin not found: $plugin"
  tmp="$(mktemp -d)"
  if ! "$SPRIGGIT" serialize -i "$plugin" -o "$tmp" \
        -g "$GAME_RELEASE" -p "$PKG" -v "$PKGVER" -u >/dev/null 2>&1; then
    rm -rf "$tmp"
    die "spriggit serialize failed for $plugin"
  fi
  # A serializer that exits 0 yet wrote no YAML (missing runtime, wrong package version) would
  # otherwise yield an empty inventory, and empty-vs-empty reads as "no changes": a PASS from a
  # check that looked at nothing.
  if [ -z "$(find "$tmp" -name '*.yaml' -print -quit)" ]; then
    rm -rf "$tmp"
    die "spriggit serialize produced no YAML for $plugin -- nothing to inventory"
  fi
  : > "$out"
  find "$tmp" -name '*.yaml' | while IFS= read -r f; do
    local base rec
    base="$(basename "$f" .yaml)"
    rec="${base%% - *}"          # EditorID (strip ' - FORMID_Master' suffix)
    # FormKey-style tokens: 6 hex digits, colon, master file name.
    { grep -oE '[0-9A-Fa-f]{6}:[A-Za-z0-9 ._-]+\.(esp|esm|esl)' "$f" || true; } \
      | while IFS= read -r tok; do printf '%s\t%s\n' "$rec" "$tok"; done
  done | sort > "$out"
  rm -rf "$tmp"
  local n; n="$(wc -l < "$out")"
  echo "esp-verify: inventoried $(basename "$plugin") — $n cross-references" >&2
}

cmd_snapshot() {
  [ "$#" -ge 1 ] || die "snapshot needs at least one plugin path"
  local p
  for p in "$@"; do
    build_inventory "$p" "$SNAPDIR/$(basename "$p").inv"
    echo "esp-verify: snapshot saved -> $SNAPDIR/$(basename "$p").inv"
  done
}

# compare_inventory <name> <before.inv> <after.inv>  -> 0 clean / 1 changes
compare_inventory() {
  local name="$1" before="$2" after="$3" rc=0
  local removed added
  if [ ! -s "$before" ]; then   # only reachable under --allow-empty; awk below needs a non-empty first file
    comm -13 "$before" "$after" | awk -F'	' '{ printf "  + reference added (info)  %-22s %s
", $1, $2 }'
    echo "  ✓ $name — only reference additions (no masters changed, none dropped)"
    return 0
  fi
  removed="$(comm -23 "$before" "$after")"
  added="$(comm -13 "$before" "$after")"

  if [ -z "$removed" ] && [ -z "$added" ]; then
    echo "  ✓ $name — no cross-reference changes"
    return 0
  fi

  # Classify with awk: a removed FORMID that reappears under a different master
  # is a MASTER CHANGE; one that does not reappear at all is a DROPPED reference.
  local report
  report="$(awk -F'\t' -v name="$name" '
    function fid(t){ sub(/:.*/,"",t); return t }
    function mst(t){ sub(/^[^:]*:/,"",t); return t }
    NR==FNR { key=$1 SUBSEP fid($2); rmast[key]=$2; rkeys[NR]=$1 "\t" $2; rn++; next }
    { key=$1 SUBSEP fid($2); amast[key]=$2; akeys[NR-rn]=$1 "\t" $2; an++ }
    END {
      crit=0
      for (i=1;i<=rn;i++) {
        split(rkeys[i],c,"\t"); ed=c[1]; tok=c[2]; key=ed SUBSEP fid(tok)
        if (key in amast && amast[key]!=tok) {
          printf "  \342\232\240 MASTER CHANGED  %-34s %s\n      %s  ->  %s\n", ed, fid(tok), tok, amast[key]
          seen[key]=1; crit++
        } else if (!(key in amast)) {
          printf "  \342\232\240 REFERENCE DROPPED  %-30s %s\n", ed, tok
          crit++
        }
      }
      for (i=1;i<=an;i++) {
        split(akeys[i],c,"\t"); ed=c[1]; tok=c[2]; key=ed SUBSEP fid(tok)
        if (key in seen) continue
        if (!(key in rmast)) printf "  + reference added (info)  %-22s %s\n", ed, tok
      }
      exit (crit>0)
    }
  ' "$before" "$after")"
  local awkrc=$?

  if [ -n "$report" ]; then echo "$report"; fi
  if [ "$awkrc" -ne 0 ]; then
    echo "  ✗ $name — CRITICAL: master change(s) or dropped reference(s) above"
    rc=1
  else
    echo "  ✓ $name — only reference additions (no masters changed, none dropped)"
  fi
  return $rc
}

# which_empty <snapshot> <after> -> a phrase naming the empty inventory(ies)
which_empty() {
  if [ ! -s "$1" ] && [ ! -s "$2" ]; then echo "both the snapshot and the new inventory"
  elif [ ! -s "$1" ]; then echo "the snapshot inventory"
  else echo "the new inventory"; fi
}

cmd_verify() {
  local allow_empty=0
  if [ "${1:-}" = "--allow-empty" ]; then allow_empty=1; shift; fi
  [ "$#" -ge 1 ] || die "verify needs at least one plugin path"
  local p name snap after overall=0
  echo "=============================================="
  echo " esp-verify — cross-reference integrity check"
  echo "=============================================="
  for p in "$@"; do
    name="$(basename "$p")"
    snap="$SNAPDIR/$name.inv"
    [ -f "$snap" ] || die "no snapshot for $name — run 'snapshot $name' before the operation"
    after="$(mktemp)"
    build_inventory "$p" "$after"
    if [ "$allow_empty" -eq 0 ] && { [ ! -s "$snap" ] || [ ! -s "$after" ]; }; then
      echo "  ✗ $name — REFUSING PASS: $(which_empty "$snap" "$after") holds 0 cross-references; pass --allow-empty if that is genuinely so"
      overall=1
      rm -f "$after"
      continue
    fi
    compare_inventory "$name" "$snap" "$after" || overall=1
    rm -f "$after"
  done
  echo "----------------------------------------------"
  if [ "$overall" -ne 0 ]; then
    echo " RESULT: ✗ FAIL — review the changes above."
    echo "         If a change was intended, re-run 'snapshot' to accept it."
  else
    echo " RESULT: ✓ PASS — no master changes, no dropped references."
  fi
  echo "=============================================="
  return $overall
}

cmd_guard() {
  local plugins=() sawsep=0 vflag=()
  if [ "${1:-}" = "--allow-empty" ]; then vflag=(--allow-empty); shift; fi
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--" ]; then sawsep=1; shift; break; fi
    plugins+=("$1"); shift
  done
  [ "$sawsep" -eq 1 ] || die "guard requires:  guard <plugin>... -- <command...>"
  [ "${#plugins[@]}" -ge 1 ] || die "guard needs at least one plugin before --"
  [ "$#" -ge 1 ] || die "guard needs a command after --"

  cmd_snapshot "${plugins[@]}"
  echo "esp-verify: running guarded command: $*"
  "$@"; local cmdrc=$?
  echo "esp-verify: command exited with code $cmdrc"
  cmd_verify ${vflag[@]+"${vflag[@]}"} "${plugins[@]}"; local vrc=$?
  [ "$cmdrc" -eq 0 ] && [ "$vrc" -eq 0 ]
}

case "${1:-}" in
  snapshot) shift; cmd_snapshot "$@" ;;
  verify)   shift; cmd_verify   "$@" ;;
  guard)    shift; cmd_guard    "$@" ;;
  ""|-h|--help)
    sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *) die "unknown subcommand '${1}' — use snapshot | verify | guard" ;;
esac
