#!/usr/bin/env bash
# Probe proxyapi.ru to find what model + endpoint + body actually works.
# Usage:
#   chmod +x scripts/probe_proxyapi.sh
#   ./scripts/probe_proxyapi.sh
#
# Reads PROXY_API_KEY and PROXY_BASE_URL from .env in the project root.

set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

KEY="${PROXY_API_KEY:-${OPENAI_API_KEY:-}}"
BASE="${PROXY_BASE_URL:-${OPENAI_BASE_URL:-https://api.proxyapi.ru/openai/v1}}"

if [[ -z "$KEY" ]]; then
  echo "ERROR: PROXY_API_KEY is empty"
  exit 1
fi

echo "BASE = $BASE"
echo "KEY  = ${KEY:0:10}...${KEY: -4}"
echo

probe () {
  local label="$1" path="$2" body="$3"
  echo "─── $label ───"
  echo "POST $BASE$path"
  echo "body: $body"
  local http_code
  http_code=$(curl -sS -o /tmp/proxyapi.out -w "%{http_code}" \
    -X POST "$BASE$path" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d "$body" || echo "curl-err")
  echo "HTTP $http_code"
  echo "response:"
  cat /tmp/proxyapi.out | head -c 1200
  echo
  echo
}

# 1. List available models
echo "─── GET $BASE/models ───"
curl -sS "$BASE/models" -H "Authorization: Bearer $KEY" | head -c 4000
echo
echo

# 2. Minimal Responses API call — exact shape of user's working snippet.
for model in gpt-4o gpt-4o-mini gpt-4.1-mini gpt-4.1 gpt-3.5-turbo; do
  probe "responses · $model" "/responses" \
    "{\"model\":\"$model\",\"input\":\"Привет\"}"
done

# 3. Minimal chat.completions call.
for model in gpt-4o gpt-4o-mini gpt-4.1-mini gpt-4.1 gpt-3.5-turbo; do
  probe "chat.completions · $model" "/chat/completions" \
    "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Привет\"}]}"
done

echo "Done."
