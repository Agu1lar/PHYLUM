#!/usr/bin/env bash
# Usage: ./request_and_approve.sh "Title" "Details" [approve|reject]
API_BASE=${API_BASE:-http://127.0.0.1:8000}
TITLE="$1"
DETAILS="$2"
ACTION="$3"

if [ -z "$TITLE" ]; then
  echo "Usage: $0 \"Title\" \"Details\" [approve|reject]"
  exit 1
fi

RESP=$(curl -s -X POST "$API_BASE/request_approval" -H "Content-Type: application/json" -d "{\"title\": \"$TITLE\", \"details\": \"$DETAILS\"}")
AID=$(echo "$RESP" | jq -r .approval_id)
echo "Created approval_id: $AID"

if [ "$ACTION" = "approve" ]; then
  curl -s -X POST "$API_BASE/approval/$AID" -H "Content-Type: application/json" -d '{"status":"approved"}'
  echo "Approved $AID"
fi
if [ "$ACTION" = "reject" ]; then
  curl -s -X POST "$API_BASE/approval/$AID" -H "Content-Type: application/json" -d '{"status":"rejected"}'
  echo "Rejected $AID"
fi
