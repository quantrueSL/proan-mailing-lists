#!/usr/bin/env bash
set -euo pipefail

PROJECT="proan-quantrue"
REGION="us-west4"
SERVICE="mailing-lists"
FIRESTORE_DATABASE_ID="proan-lista-mails"

if [[ -f ".env" ]]; then
  set -o allexport
  source .env
  set +o allexport
fi

strip_newlines() {
  printf "%s" "$1" | tr -d '\r\n'
}

require_value() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "ERROR: falta $name. Definelo en .env o exportalo antes de ejecutar deploy.sh." >&2
    exit 1
  fi
}

create_or_update_secret() {
  local name="$1"
  local value="$2"
  if gcloud secrets describe "$name" --project="$PROJECT" &>/dev/null; then
    printf "%s" "$value" | gcloud secrets versions add "$name" --data-file=- --project="$PROJECT"
  else
    printf "%s" "$value" | gcloud secrets create "$name" --data-file=- --project="$PROJECT" --replication-policy=automatic
  fi
}

FLASK_SECRET="$(strip_newlines "${FLASK_SECRET_KEY:-}")"
require_value "FLASK_SECRET_KEY" "$FLASK_SECRET"
create_or_update_secret "mailing-lists-flask-secret-key" "$FLASK_SECRET"

# OJO: no metas comentarios entre las lineas de abajo. Cada linea acaba en "\"
# para continuar el comando; un "#" en medio corta el comando ahi y bash intenta
# ejecutar el resto como si fuera otro programa. Los comentarios, aqui arriba.
#
# --allow-unauthenticated: el servicio queda abierto en internet y solo lo
# protege su propio login. Es temporal y esta pendiente de blindar (ver README).
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT" \
  --allow-unauthenticated \
  --timeout=60 \
  --min-instances=0 \
  --max-instances=1 \
  --concurrency=10 \
  --cpu-throttling \
  --set-env-vars "GCP_PROJECT=${PROJECT},FIREBASE_PROJECT_ID=${PROJECT},FIRESTORE_DATABASE_ID=${FIRESTORE_DATABASE_ID}" \
  --set-secrets "FLASK_SECRET_KEY=mailing-lists-flask-secret-key:latest"
