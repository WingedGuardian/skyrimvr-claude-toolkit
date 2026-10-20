#!/bin/bash
# nexus.sh -- Nexus Mods API helper for the Skyrim AI Assistant Toolkit: version lookups and the desk review
# of a mod before you update or add it (skill `mod-update-check`). Read-only: it never downloads a mod.
# The key is NEVER printed. Get a free Personal API Key: https://www.nexusmods.com/users/myaccount?tab=api
#
# Usage:
#   bash tools/nexus.sh mod <mod_id> [game_domain]            # mod summary (default domain: skyrimspecialedition)
#   bash tools/nexus.sh find "<name>" [game_domain]           # name -> candidate mod ids (GraphQL name search)
#   bash tools/nexus.sh page <mod_id> [--since <installed version>] [--full] [game_domain]
#                                                             # desk review: summary, requirements, description,
#                                                             # files, and every changelog since --since
#   bash tools/nexus.sh contents <mod_id> [file version] [game_domain]
#                                                             # what is inside each file (Nexus content previews)
#   bash tools/nexus.sh validate                              # confirm the key works (prints account name only)
#   bash tools/nexus.sh raw <api_path>                        # raw GET of any /v1/... path, prints JSON
#
# Key resolution order:  tools/.nexus_api_key  ->  $NEXUS_API_KEY
#
# Exit: 0 answered | 2 bad usage | 3 COULD-NOT-CHECK: no key, the network, an HTTP error, or a response that could
# not be read. Exit 3 always prints "RESULT: COULD-NOT-CHECK" -- it is NOT "no update" or "nothing found";
# nothing was compared.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_FILE="$SCRIPT_DIR/.nexus_api_key"
API="https://api.nexusmods.com/v1"
GQL="https://api.nexusmods.com/v2/graphql"

usage() {
    sed -n '/^# Usage:/,/^# Key resolution/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//' >&2
    exit 2
}

could_not_check() {   # could_not_check [why]: in the main shell only (a $(...) subshell would swallow stdout)
    [ -n "${1:-}" ] && echo "ERROR: $1" >&2
    echo "RESULT: COULD-NOT-CHECK -- this is NOT 'no update' or 'nothing found'; nothing was compared."
    exit 3
}

KEY=""
if [ -s "$KEY_FILE" ]; then
    KEY="$(tr -d ' \t\r\n' < "$KEY_FILE")"
elif [ -n "${NEXUS_API_KEY:-}" ]; then
    KEY="$NEXUS_API_KEY"
fi

PY=""
find_python() {
    [ -n "$PY" ] && return 0
    for c in "py -3" "python" "python3"; do
        if $c -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' >/dev/null 2>&1; then PY="$c"; return 0; fi
    done
    could_not_check "Python 3 not found (tried py -3, python, python3); the responses cannot be read without it"
}
render() { $PY "$SCRIPT_DIR/nexus-render.py" "$@"; }

# http <curl args...> -> body on stdout when the answer is HTTP 200; otherwise the reason on stderr and status 3.
# Three outcomes, never two: "the API said nothing" and "we never reached the API" must not both arrive as an
# empty string and print as a blank answer.
# The key reaches curl on STDIN (`-H @-`; printf is a shell builtin, so no process ever carries it), never as an
# argument: a curl command line is readable by any process of the same user and by command-line auditing for as
# long as the request runs (review L5 F10).
http() {
    local out code
    out="$(printf 'apikey: %s\n' "$KEY" | curl -s --max-time 30 -w $'\n%{http_code}' -H @- "$@")" \
        || { echo "ERROR: the request did not complete (network, DNS or timeout)" >&2; return 3; }
    code="${out##*$'\n'}"
    out="${out%$'\n'*}"
    if [ "$code" != "200" ]; then
        echo "ERROR: Nexus answered HTTP $code" >&2
        [ "$code" = "401" ] && echo "       (401: the API key is missing, wrong or expired)" >&2
        return 3
    fi
    printf '%s' "$out"
}
get() { http -H "Accept: application/json" "$API$1"; }
gql() { http -X POST -H "Content-Type: application/json" --data-binary "$1" "$GQL"; }

need_id() { [[ "${1:-}" =~ ^[0-9]+$ ]] || { echo "usage: a numeric Nexus mod id (got '${1:-}')" >&2; exit 2; }; }
need_domain() { [[ "$1" =~ ^[a-z0-9]+$ ]] || { echo "usage: a game domain like skyrimspecialedition (got '$1')" >&2; exit 2; }; }

cmd="${1:-}"
case "$cmd" in
    validate|mod|raw|find|page|contents) ;;
    *) usage ;;
esac
if [ -z "$KEY" ]; then
    echo "No Nexus API key found." >&2
    echo "  Save your free Personal API Key (one line) to: $KEY_FILE" >&2
    echo "  or set NEXUS_API_KEY. Get a key: https://www.nexusmods.com/users/myaccount?tab=api" >&2
    could_not_check "no Nexus API key"
fi
JQ="$(command -v jq || true)"
case "$cmd" in find|page|contents) find_python ;; esac
shift

case "$cmd" in
    validate)
        body="$(get "/users/validate.json")" || could_not_check
        if [ -n "$JQ" ]; then printf '%s' "$body" | "$JQ" '{name, user_id, is_premium}'
        else printf '%s\n' "$body"; fi
        ;;
    mod)
        need_id "${1:-}"; DOMAIN="${2:-skyrimspecialedition}"; need_domain "$DOMAIN"
        body="$(get "/games/$DOMAIN/mods/$1.json")" || could_not_check
        if [ -n "$JQ" ]; then printf '%s' "$body" | "$JQ" '{name, version, updated_time, author, summary}'
        else printf '%s\n' "$body"; fi
        ;;
    raw)
        [ -n "${1:-}" ] || { echo "usage: nexus.sh raw <api_path, e.g. /games/skyrimspecialedition/mods/176043.json>" >&2; exit 2; }
        get "$1" || could_not_check
        echo
        ;;
    find)
        [ -n "${1:-}" ] || { echo "usage: nexus.sh find \"<name>\" [game_domain]" >&2; exit 2; }
        DOMAIN="${2:-skyrimspecialedition}"; need_domain "$DOMAIN"
        q="$(render gql-find "$1" "$DOMAIN")" || could_not_check "could not build the search request"
        resp="$(gql "$q")" || could_not_check
        R="$resp" NEXUS_DOMAIN="$DOMAIN" NEXUS_QUERY="$1" render find || could_not_check "the search response could not be read"
        echo "RESULT: OK"
        ;;
    page)
        need_id "${1:-}"; id="$1"; shift
        since=(); full=(); DOMAIN="skyrimspecialedition"
        while [ $# -gt 0 ]; do
            case "$1" in
                --since) [ -n "${2:-}" ] || { echo "usage: --since <installed version>" >&2; exit 2; }; since=(--since "$2"); shift 2 ;;
                --full) full=(--full); shift ;;
                -*) echo "usage: unknown option $1" >&2; exit 2 ;;
                *) DOMAIN="$1"; shift ;;
            esac
        done
        need_domain "$DOMAIN"
        info="$(get "/games/$DOMAIN/mods/$id.json")" || could_not_check
        files="$(get "/games/$DOMAIN/mods/$id/files.json")" || could_not_check
        # Optional parts: a failure here is reported by the renderer as "could NOT fetch", never as "none".
        log_status=ok
        log="$( (get "/games/$DOMAIN/mods/$id/changelogs.json") 2>/dev/null )" || { log=""; log_status=failed; }
        req=""
        if q="$(render gql-req "$id" "$DOMAIN" 2>/dev/null)"; then
            req="$( (gql "$q") 2>/dev/null )" || req=""
        fi
        INFO="$info" FILES="$files" LOG="$log" LOG_STATUS="$log_status" REQ="$req" render page "$DOMAIN" "$id" "${since[@]}" "${full[@]}" \
            || could_not_check "the mod page response could not be read"
        echo "RESULT: OK"
        ;;
    contents)
        need_id "${1:-}"; id="$1"; ver="${2:-}"; DOMAIN="${3:-skyrimspecialedition}"; need_domain "$DOMAIN"
        files="$(get "/games/$DOMAIN/mods/$id/files.json")" || could_not_check
        FILES="$files" render contents ${ver:+"$ver"} || could_not_check "the file list could not be read"
        echo "RESULT: OK"
        ;;
esac
