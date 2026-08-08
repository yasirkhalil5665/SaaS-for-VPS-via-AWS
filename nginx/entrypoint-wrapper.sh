#!/bin/sh
set -e

mkdir -p /etc/nginx/conf.d.disabled

for f in /etc/nginx/conf.d/*.conf; do
  [ -e "$f" ] || continue

  host=$(grep -oE 'proxy_pass http://[^:;]+' "$f" | head -1 | sed 's#proxy_pass http://##')

  if [ -n "$host" ] && ! getent hosts "$host" > /dev/null 2>&1; then
    echo "[entrypoint-wrapper] Stale config found (upstream '$host' unreachable) — quarantining $f"
    mv "$f" /etc/nginx/conf.d.disabled/
  fi
done

exec /docker-entrypoint.sh "$@"
