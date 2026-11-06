#!/bin/bash
set +e
failures=""
require() {
  if ! eval "$1"; then failures="${failures}${failures:+\n}$2"; fi
}

require 'pgrep -x nginx >/dev/null' 'nginx is not running'
require 'pgrep -x fcgiwrap >/dev/null' 'fcgiwrap is not running'
require 'test -e /etc/nginx/sites-enabled/git-http' 'missing /etc/nginx/sites-enabled/git-http'
require 'test -e /etc/nginx/git-htpasswd' 'missing /etc/nginx/git-htpasswd'
nginx_config="$(nginx -T 2>&1)"
require 'nginx -t >/dev/null 2>&1' 'nginx configuration is invalid'
require 'printf "%s" "$nginx_config" | grep -q auth_basic' 'effective nginx configuration does not enable HTTP basic auth'
require 'printf "%s" "$nginx_config" | grep -Fq /etc/nginx/git-htpasswd' 'effective nginx configuration does not use the required htpasswd file'
require 'printf "%s" "$nginx_config" | grep -q git-http-backend' 'effective nginx configuration does not invoke git-http-backend'
require 'htpasswd -vb /etc/nginx/git-htpasswd gituser gitpass >/dev/null 2>&1' 'the required gituser/gitpass htpasswd credential is invalid'
refs_url='http://localhost/project-alpha.git/info/refs?service=git-upload-pack'
headers="$(mktemp)"
code="$(curl -sS -D "$headers" -o /dev/null -w '%{http_code}' "$refs_url")"
require 'test "$code" = 401 && grep -qi "^WWW-Authenticate: Basic" "$headers"' 'repository endpoint does not challenge unauthenticated clients with HTTP basic auth'
auth_code="$(curl -sS -u gituser:gitpass -o /dev/null -w '%{http_code}' "$refs_url")"
require 'test "$auth_code" = 200' 'fcgiwrap is not accessible to nginx for authenticated Git HTTP requests'

temporary_directory="$(mktemp -d)"
clone_url='http://gituser:gitpass@localhost/project-alpha.git'
git clone "$clone_url" "$temporary_directory/first" >/tmp/verifier-clone.log 2>&1
clone_status=$?
require 'test "$clone_status" -eq 0' "authenticated clone failed: $(cat /tmp/verifier-clone.log)"
require 'test -f "$temporary_directory/first/README.md" && test "$(cat "$temporary_directory/first/README.md")" = "# Project Alpha"' 'clone does not contain the supplied README.md content'
require 'test -f "$temporary_directory/first/alpha.txt" && test "$(cat "$temporary_directory/first/alpha.txt")" = "Initial content for project alpha."' 'clone does not contain the supplied alpha.txt content'
if [ "$clone_status" -eq 0 ]; then
  marker_name="verifier-$(date +%s%N)-$$.txt"
  marker_content="authenticated push marker $(date +%s%N)-$$"
  printf '%s\n' "$marker_content" > "$temporary_directory/first/$marker_name"
  git -C "$temporary_directory/first" config user.email verifier@example.invalid
  git -C "$temporary_directory/first" config user.name Verifier
  git -C "$temporary_directory/first" add "$marker_name"
  git -C "$temporary_directory/first" commit -m 'Verifier authenticated push' >/dev/null 2>&1
  git -C "$temporary_directory/first" push origin HEAD >/tmp/verifier-push.log 2>&1
  push_status=$?
  require 'test "$push_status" -eq 0' "authenticated push failed: $(cat /tmp/verifier-push.log)"
  git clone "$clone_url" "$temporary_directory/second" >/tmp/verifier-reclone.log 2>&1
  reclone_status=$?
  require 'test "$reclone_status" -eq 0' "fresh clone after push failed: $(cat /tmp/verifier-reclone.log)"
  require 'test -f "$temporary_directory/second/$marker_name" && test "$(cat "$temporary_directory/second/$marker_name")" = "$marker_content"' 'fresh authenticated clone does not contain the exact pushed content'
fi
rm -rf "$temporary_directory" "$headers"
mkdir -p /logs/verifier
if [ -z "$failures" ]; then
  echo 'Git HTTP clone, authentication, fcgiwrap, and push checks passed'
  echo 1 > /logs/verifier/reward.txt
else
  printf '%b\n' "$failures"
  echo 0 > /logs/verifier/reward.txt
fi
exit 0
