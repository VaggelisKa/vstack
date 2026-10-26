#!/usr/bin/env bash
# Read-only git for reviewers: the only way a narrowed reviewer reads code.
#
# Usage (run from inside the repo):
#   git-read.sh show <sha>:<path>
#   git-read.sh diff [opts] <sha>...<sha> [-- <path>...]
#       opts: --stat --name-only --name-status -U<n> --unified=<n>
#   git-read.sh log [opts] [<sha>|<sha>..<sha>] [-- <path>...]
#       opts: --oneline --stat --name-only -n<n> -n <n> --max-count=<n>
#             -S<str> -S <str> -G<regex> -G <regex>
#
# A -S or -G value is never read as an option, even if it starts with "-":
# it is passed to git stuck to its flag (-S"$value"). Empty values are
# rejected.
#
# Permission rules match the command text, and shell quoting can split an
# option so a deny pattern misses it (git show "--output=/tmp/x" HEAD still
# writes /tmp/x). This script sees the arguments after the shell has unquoted
# them, so it rejects every option that isn't on the allowlist above, however
# it was quoted. Each <sha> may be any name for a commit; it is resolved with
# rev-parse and git only ever sees the full SHA. Paths must be relative,
# without ".." components, and can't start with "-" or ":" (pathspec magic).
# Diffs always use a/ and b/ prefixes, whatever diff.noprefix or
# diff.mnemonicPrefix say, and never run external diff or textconv programs.
#
# Exits 2 with a message on stderr when it rejects the arguments; otherwise
# exits with git's status.
set -euo pipefail

# Nothing from the caller's environment steers git: no pager, no external
# diff, no injected config (GIT_CONFIG_*), no trace files (GIT_TRACE=<path>
# writes one), no other GIT_DIR.
while IFS= read -r v; do
  case $v in GIT_*) unset "$v" ;; esac
done < <(compgen -e)
unset PAGER

GIT=(git --no-pager -c core.pager=cat -c diff.external= -c diff.noprefix=false
  -c diff.mnemonicPrefix=false -c color.ui=false -c log.showSignature=false
  -c core.fsmonitor=false)

die() {
  printf 'git-read: %s\n' "$*" >&2
  exit 2
}

num() { [[ $1 =~ ^[0-9]{1,6}$ ]] || die "not a number: $1"; }

# Resolve a commit name to its full SHA, or reject it.
commit() {
  [[ -n $1 && $1 != -* ]] || die "bad commit: '$1'"
  "${GIT[@]}" rev-parse --verify --quiet --end-of-options "$1^{commit}" ||
    die "not a commit: $1"
}

path_ok() {
  local p=$1 part
  [[ $p != -* ]] || die "path starts with '-': $p"
  [[ $p != /* ]] || die "absolute path: $p"
  [[ $p != :* ]] || die "path starts with ':': $p"
  [[ $p != *$'\n'* ]] || die "path has a newline"
  IFS=/ read -ra parts <<< "$p"
  for part in "${parts[@]}"; do
    [[ $part != .. ]] || die "path has a '..' component: $p"
  done
}

[[ $# -ge 1 ]] || die "usage: git-read.sh show <sha>:<path> | diff [opts] <sha>...<sha> [-- <path>...] | log [opts] [<sha>|<sha>..<sha>] [-- <path>...]"
sub=$1
shift

case $sub in
show)
  [[ $# -eq 1 ]] || die "show takes exactly one <sha>:<path>"
  [[ $1 == *:* ]] || die "show needs <sha>:<path>, got: $1"
  rev=${1%%:*}
  path=${1#*:}
  [[ -z $path ]] || path_ok "$path"
  sha=$(commit "$rev")
  exec "${GIT[@]}" show --no-ext-diff --no-textconv "$sha:$path"
  ;;

diff)
  opts=() range="" paths=() sep=""
  while [[ $# -gt 0 ]]; do
    case $1 in
    --) shift; paths=("$@"); sep=1; break ;;
    --stat | --name-only | --name-status) opts+=("$1") ;;
    -U*) num "${1#-U}"; opts+=("$1") ;;
    --unified=*) num "${1#--unified=}"; opts+=("$1") ;;
    -*) die "option not allowed: $1" ;;
    *)
      [[ -z $range ]] || die "diff takes one <sha>...<sha> range, got another: $1"
      range=$1
      ;;
    esac
    shift
  done
  [[ $range == *...* ]] || die "diff needs a three-dot range <sha>...<sha>, got: '$range'"
  base=${range%%...*}
  head=${range#*...}
  [[ $head != *...* ]] || die "bad range: $range"
  [[ -z $sep || ${#paths[@]} -gt 0 ]] || die "'--' needs at least one path"
  for p in "${paths[@]+"${paths[@]}"}"; do
    [[ -n $p ]] || die "empty path"
    path_ok "$p"
  done
  base=$(commit "$base")
  head=$(commit "$head")
  exec "${GIT[@]}" diff --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ \
    "${opts[@]+"${opts[@]}"}" "$base...$head" -- "${paths[@]+"${paths[@]}"}"
  ;;

log)
  opts=() rev="" paths=() sep=""
  while [[ $# -gt 0 ]]; do
    case $1 in
    --) shift; paths=("$@"); sep=1; break ;;
    --oneline | --stat | --name-only) opts+=("$1") ;;
    # The pickaxe value always reaches git stuck to its flag (-S<str>), so it
    # is searched for as a string even when it reads like an option.
    -S | -G)
      [[ $# -ge 2 && -n $2 ]] || die "$1 needs a non-empty value"
      opts+=("$1$2"); shift
      ;;
    -S* | -G*) opts+=("$1") ;;
    --max-count=*) num "${1#--max-count=}"; opts+=("$1") ;;
    -n)
      [[ $# -ge 2 ]] || die "-n needs a number"
      num "$2"; opts+=("--max-count=$2"); shift
      ;;
    -n*) num "${1#-n}"; opts+=("--max-count=${1#-n}") ;;
    -*) die "option not allowed: $1" ;;
    *)
      [[ -z $rev ]] || die "log takes one <sha> or <sha>..<sha>, got another: $1"
      rev=$1
      ;;
    esac
    shift
  done
  [[ -z $sep || ${#paths[@]} -gt 0 ]] || die "'--' needs at least one path"
  for p in "${paths[@]+"${paths[@]}"}"; do
    [[ $p != -- ]] || die "'--' may appear only once"
    [[ -n $p ]] || die "empty path"
    path_ok "$p"
  done
  revs=()
  if [[ -z $rev ]]; then
    revs=("$(commit HEAD)")
  elif [[ $rev == *...* ]]; then
    die "log takes <sha> or <sha>..<sha>, not three dots: $rev"
  elif [[ $rev == *..* ]]; then
    from=$(commit "${rev%%..*}")
    to=$(commit "${rev#*..}")
    revs=("$from..$to")
  else
    revs=("$(commit "$rev")")
  fi
  exec "${GIT[@]}" log --no-ext-diff --no-textconv "${opts[@]+"${opts[@]}"}" \
    "${revs[@]}" -- "${paths[@]+"${paths[@]}"}"
  ;;

*)
  die "unknown subcommand: $sub (only show, diff, log)"
  ;;
esac
