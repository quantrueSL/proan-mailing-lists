from __future__ import annotations

import logging
import os
import re
import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from google.cloud import firestore


GCP_PROJECT = os.environ.get("GCP_PROJECT", "proan-quantrue").strip()
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", GCP_PROJECT).strip()
FIRESTORE_DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "proan-lista-mails").strip()
LISTS_COLLECTION = "lists"
AUTH_USERS_COLLECTION = "auth_users"
MAX_EMAILS_PER_LIST = int(os.environ.get("MAX_EMAILS_PER_LIST", "100"))
MAX_GROUPS_PER_LIST = int(os.environ.get("MAX_GROUPS_PER_LIST", "40"))
MAX_HISTORY_ITEMS = int(os.environ.get("MAX_HISTORY_ITEMS", "20"))

logger = logging.getLogger("mailing-lists")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class ValidationError(ValueError):
    pass


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LIST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,64}$")
# Clave de grupo de una lista segmentada: el codigo de sociedad de SAP (PAL, PAN...).
GROUP_KEY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,15}$")

# Dos tipos de lista, que la interfaz separa en secciones distintas:
#   mailing -> destinatarios de un aviso automatico (p.ej. cambio_divisa)
#   access  -> quien puede entrar en una herramienta, y con que rol
# Sin campo `kind` -> mailing, para que las listas anteriores sigan igual.
LIST_KINDS = ("mailing", "access")
DEFAULT_LIST_KIND = "mailing"

# Roles de acceso. Deben coincidir con SessionRole del frontend de
# proan-Hidrocarburos (ver LOGIN.md, seccion 3).
ROLE_VALUES = ("gerencia", "generico")
DEFAULT_ROLE = "generico"

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16
SCRYPT_KEY_LEN = 32


@lru_cache(maxsize=1)
def get_firestore_client() -> firestore.Client:
    return firestore.Client(project=FIREBASE_PROJECT_ID, database=FIRESTORE_DATABASE_ID)


def _list_doc_ref(list_id: str):
    return get_firestore_client().collection(LISTS_COLLECTION).document(list_id)


def _auth_user_doc_ref(username: str):
    return get_firestore_client().collection(AUTH_USERS_COLLECTION).document(username)


def _normalize_list_id(list_id: str) -> str:
    value = (list_id or "").strip()
    if not value:
        raise ValidationError("list_id obligatorio.")
    if not LIST_ID_RE.fullmatch(value):
        raise ValidationError(
            "list_id invalido. Usa minusculas, numeros, guion o guion bajo."
        )
    return value


def normalize_username(username: str) -> str:
    value = str(username or "").strip().lower()
    if not value:
        raise ValidationError("username obligatorio.")
    if not USERNAME_RE.fullmatch(value):
        raise ValidationError(
            "username invalido. Usa minusculas, numeros, punto, guion o guion bajo."
        )
    return value


def generate_password_hash(password: str) -> str:
    raw_password = str(password or "")
    if not raw_password:
        raise ValidationError("password obligatorio.")
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    derived = hashlib.scrypt(
        raw_password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_KEY_LEN,
    )
    return (
        f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:"
        f"{salt.hex()}:{derived.hex()}"
    )


def check_password_hash(password_hash: str, password: str) -> bool:
    try:
        scheme, n_str, r_str, p_str, salt_hex, digest_hex = str(password_hash).split(":")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=int(n_str),
            r=int(r_str),
            p=int(p_str),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _normalize_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValidationError("name obligatorio.")
    return name


def _normalize_updated_by(value: Any) -> str:
    updated_by = str(value or "").strip()
    return updated_by or "system"


def _normalize_comment(value: Any) -> str:
    return str(value or "").strip()


def normalize_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in LIST_KINDS else DEFAULT_LIST_KIND


def normalize_role(value: Any) -> str:
    """Rol desconocido -> el menos privilegiado. Una errata quita permisos, nunca los da."""
    role = str(value or "").strip().lower()
    return role if role in ROLE_VALUES else DEFAULT_ROLE


def normalize_roles(raw_roles: Any, emails: list[str]) -> dict[str, str]:
    """
    Mapa correo -> rol, limitado a los correos que estan en `emails`.

    `emails` es la puerta y `roles` solo reparte permisos: un correo con rol pero
    fuera de la lista no debe quedar guardado, porque induce a pensar que tiene
    acceso. Y al quitar un correo de la lista, su rol desaparece con el.
    """
    if raw_roles is None:
        return {}
    if not isinstance(raw_roles, dict):
        raise ValidationError("roles debe ser un objeto de correo a rol.")

    permitidos = {email.strip().lower(): email for email in emails}
    roles: dict[str, str] = {}
    for raw_email, raw_role in raw_roles.items():
        clave = str(raw_email or "").strip().lower()
        if clave in permitidos:
            # Se guarda con la misma grafia que en `emails` para que la interfaz
            # pueda emparejar fila y rol sin normalizar de nuevo.
            roles[permitidos[clave]] = normalize_role(raw_role)
    return roles


def extract_email(raw: Any) -> str:
    """
    Saca la direccion de un texto que puede venir como 'Nombre Apellido <correo>'.

    Ese es el formato en el que estan los destinatarios en las hojas de calculo de
    origen, y es lo que se pega en el campo. Rechazarlo obligaria a limpiar a mano
    ciento y pico direcciones.
    """
    texto = str(raw or "").strip().strip(",").strip().strip('"').strip()
    if "<" in texto and ">" in texto:
        texto = texto[texto.rfind("<") + 1 : texto.rfind(">")].strip()
    return texto


def _clean_emails(raw_emails: Any) -> list[str]:
    """Valida y quita repetidos, sin aplicar limite de tamano."""
    if not isinstance(raw_emails, list):
        raise ValidationError("emails debe ser una lista.")

    normalized = []
    seen = set()
    for raw in raw_emails:
        email = extract_email(raw)
        if not email:
            continue
        if not EMAIL_RE.fullmatch(email):
            raise ValidationError(f"Email invalido: {email}")
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(email)
    return normalized


def normalize_emails(raw_emails: Any) -> list[str]:
    normalized = _clean_emails(raw_emails)
    if len(normalized) > MAX_EMAILS_PER_LIST:
        raise ValidationError(
            f"Demasiados emails. Limite actual: {MAX_EMAILS_PER_LIST}."
        )
    return normalized


def normalize_group_key(value: Any) -> str:
    key = str(value or "").strip().upper()
    if not key:
        raise ValidationError("El identificador de grupo no puede estar vacio.")
    if not GROUP_KEY_RE.fullmatch(key):
        raise ValidationError(
            f"Identificador de grupo invalido: {value}. "
            f"Usa mayusculas, numeros, guion o guion bajo."
        )
    return key


def normalize_por_sociedad(raw_groups: Any) -> dict[str, list[str]]:
    """
    Mapa sociedad -> correos de una lista segmentada.

    Los grupos vacios SE CONSERVAN. Un grupo sin nadie significa "esta sociedad
    existe y no tiene destinatarios", que es informacion distinta de que el grupo no
    exista: si se descartara, la interfaz dejaria de mostrarlo y habria que volver a
    teclear el codigo para anadir gente.
    """
    if raw_groups is None:
        return {}
    if not isinstance(raw_groups, dict):
        raise ValidationError(
            "por_sociedad debe ser un objeto de sociedad a lista de correos."
        )

    grupos: dict[str, list[str]] = {}
    for raw_key, raw_emails in raw_groups.items():
        key = normalize_group_key(raw_key)
        if key in grupos:
            raise ValidationError(f"Grupo repetido: {key}.")
        grupos[key] = normalize_emails(raw_emails)

    if len(grupos) > MAX_GROUPS_PER_LIST:
        raise ValidationError(f"Demasiados grupos. Limite actual: {MAX_GROUPS_PER_LIST}.")
    return grupos


def _serialize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _serialize_doc(snapshot) -> dict[str, Any] | None:
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["list_id"] = snapshot.id
    data["updated_at"] = _serialize_timestamp(data.get("updated_at"))
    # Defaults explicitos para que la interfaz reciba siempre la misma forma,
    # tambien en las listas creadas antes de que existieran estos campos.
    data["kind"] = normalize_kind(data.get("kind"))
    data["enabled"] = bool(data.get("enabled", True))
    # Sin el campo -> la lista SI usa roles: es como se comportaban todas antes de
    # que existiera, y el valor por defecto no debe cambiar lo que ya funciona.
    data["usa_roles"] = bool(data.get("usa_roles", True))
    if not isinstance(data.get("roles"), dict):
        data["roles"] = {}
    data["segmentada"] = bool(data.get("segmentada", False))
    if not isinstance(data.get("globales"), list):
        data["globales"] = []
    if not isinstance(data.get("por_sociedad"), dict):
        data["por_sociedad"] = {}
    return data


def _serialize_auth_user(snapshot) -> dict[str, Any] | None:
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["user_id"] = snapshot.id
    data["username"] = data.get("username") or snapshot.id
    data["created_at"] = _serialize_timestamp(data.get("created_at"))
    data["updated_at"] = _serialize_timestamp(data.get("updated_at"))
    return data


def list_lists() -> list[dict[str, Any]]:
    docs = get_firestore_client().collection(LISTS_COLLECTION).stream()
    items = []
    for doc in docs:
        serialized = _serialize_doc(doc)
        if serialized:
            items.append(serialized)
    items.sort(key=lambda item: item.get("name", "").lower())
    return items


def get_list(list_id: str) -> dict[str, Any] | None:
    list_id = _normalize_list_id(list_id)
    return _serialize_doc(_list_doc_ref(list_id).get())


def get_auth_user(username: str) -> dict[str, Any] | None:
    username = normalize_username(username)
    return _serialize_auth_user(_auth_user_doc_ref(username).get())


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    user = get_auth_user(username)
    if not user or not user.get("active", True):
        return None
    password_hash = str(user.get("password_hash") or "")
    if not password_hash or not check_password_hash(password_hash, str(password or "")):
        return None
    return user


def create_or_update_auth_user(
    username: str,
    password: str,
    active: bool = True,
) -> dict[str, Any]:
    normalized_username = normalize_username(username)
    now = datetime.now(timezone.utc)
    doc_ref = _auth_user_doc_ref(normalized_username)
    existing_snapshot = doc_ref.get()
    existing = existing_snapshot.to_dict() if existing_snapshot.exists else None
    payload = {
        "username": normalized_username,
        "password_hash": generate_password_hash(password),
        "active": bool(active),
        "updated_at": now,
    }
    if not existing:
        payload["created_at"] = now
    else:
        payload["created_at"] = existing.get("created_at") or now
    doc_ref.set(payload)
    saved = _serialize_auth_user(doc_ref.get())
    if not saved:
        raise RuntimeError("No se pudo guardar el usuario de autenticacion.")
    return saved


def _is_api_request() -> bool:
    from flask import request
    return request.path.startswith("/api/")


def ensure_authenticated():
    from flask import jsonify, redirect, request, session, url_for

    public_paths = {"/health", "/login", "/api/auth/login", "/favicon.ico"}
    if request.path in public_paths:
        return None

    username = session.get("auth_username")
    user_id = session.get("auth_user_id")
    if not username or not user_id:
        if _is_api_request():
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("serve_login"))

    try:
        user = get_auth_user(username)
    except ValidationError:
        user = None
    if not user or user.get("user_id") != user_id or not user.get("active", True):
        session.clear()
        if _is_api_request():
            return jsonify({"error": "Cuenta desactivada o sesion invalida."}), 401
        return redirect(url_for("serve_login"))
    return None


def get_list_history(list_id: str) -> list[dict[str, Any]]:
    list_id = _normalize_list_id(list_id)
    query = (
        _list_doc_ref(list_id)
        .collection("history")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(MAX_HISTORY_ITEMS)
    )
    items = []
    for doc in query.stream():
        serialized = _serialize_doc(doc)
        if serialized:
            items.append(serialized)
    return items


def _build_record(list_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get("enabled", True))
    kind = normalize_kind(payload.get("kind"))
    usa_roles = bool(payload.get("usa_roles", True))
    segmentada = bool(payload.get("segmentada", False))

    if segmentada and kind == "access":
        # El rol solo significa algo en una lista de acceso, y no hay forma sensata de
        # repartirlo entre grupos. Mejor prohibir la combinacion que dejar un estado
        # que nadie sabria interpretar.
        raise ValidationError("Una lista de acceso no puede estar segmentada.")

    globales = normalize_emails(payload.get("globales") or [])
    por_sociedad = normalize_por_sociedad(payload.get("por_sociedad"))

    if segmentada:
        # `emails` es DERIVADO: la union de globales y de todos los grupos. En una lista
        # segmentada los destinatarios de verdad son los grupos, asi que dejar `emails`
        # como campo editable aparte garantizaria que se desincronizara. Se calcula sin
        # aplicar MAX_EMAILS_PER_LIST porque el limite se controla ya en cada grupo, y
        # penalizar por la suma seria castigar algo que nadie escribio a mano.
        emails = _clean_emails(
            [*globales, *(correo for grupo in por_sociedad.values() for correo in grupo)]
        )
    else:
        emails = normalize_emails(payload.get("emails"))

    if enabled and not emails:
        raise ValidationError("La lista activa debe tener al menos un email valido.")

    # `usa_roles: False` vacia los roles en el SERVIDOR, no solo en la interfaz.
    # Si se confiara en que el cliente no los manda, una pestana abierta de antes
    # del cambio seguiria escribiendo roles al guardar, y volveria el problema que
    # esto arregla: roles visibles en una herramienta que no los usa.
    roles = normalize_roles(payload.get("roles"), emails) if usa_roles else {}

    return {
        "list_id": _normalize_list_id(list_id),
        "name": _normalize_name(payload.get("name")),
        "emails": emails,
        "roles": roles,
        "kind": kind,
        "usa_roles": usa_roles,
        "segmentada": segmentada,
        "globales": globales,
        "por_sociedad": por_sociedad,
        "enabled": enabled,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": _normalize_updated_by(payload.get("updated_by")),
        "comment": _normalize_comment(payload.get("comment")),
    }


def save_list(list_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    existing = get_list(list_id)
    effective_payload = dict(payload)
    effective_payload.setdefault("updated_by", "system")
    effective_payload.setdefault("comment", "")

    if existing:
        # Campos que un cliente puede no enviar y que NO deben perderse por
        # omision. Se distingue "no me lo has mandado" de "mandame esto vacio":
        # `roles: {}` explicito si borra los roles, ausencia los conserva.
        if "roles" not in effective_payload:
            effective_payload["roles"] = existing.get("roles")
        if not effective_payload.get("kind"):
            effective_payload["kind"] = existing.get("kind")
        # `usa_roles` es configuracion de la lista, no algo que se edite desde la
        # interfaz: el cliente no lo manda nunca, asi que se conserva siempre.
        if "usa_roles" not in effective_payload:
            effective_payload["usa_roles"] = existing.get("usa_roles", True)
        for campo in ("segmentada", "globales", "por_sociedad"):
            if campo not in effective_payload:
                effective_payload[campo] = existing.get(campo)

    record = _build_record(list_id, effective_payload)
    doc_ref = _list_doc_ref(record["list_id"])
    history_ref = doc_ref.collection("history").document()

    # `set` sin merge, a proposito: es la unica forma de que quitar un correo o un
    # rol se refleje de verdad (con merge, borrar una clave de un mapa no tiene
    # efecto). La contrapartida es que aqui hay que escribir TODOS los campos del
    # documento; si se anade uno nuevo al modelo, hay que anadirlo tambien aqui.
    firestore_payload = {
        "name": record["name"],
        "emails": record["emails"],
        "roles": record["roles"],
        "kind": record["kind"],
        "usa_roles": record["usa_roles"],
        "segmentada": record["segmentada"],
        "globales": record["globales"],
        "por_sociedad": record["por_sociedad"],
        "enabled": record["enabled"],
        "updated_at": record["updated_at"],
        "updated_by": record["updated_by"],
        "comment": record["comment"],
    }

    batch = get_firestore_client().batch()
    batch.set(doc_ref, firestore_payload)
    batch.set(history_ref, firestore_payload)
    batch.commit()

    saved = dict(record)
    saved["updated_at"] = _serialize_timestamp(saved["updated_at"])
    return saved


def delete_list(list_id: str) -> None:
    """
    Borra una lista y su historial.

    Firestore no borra subcolecciones en cascada: sin este bucle, los documentos
    de `history` quedarian huerfanos, invisibles y facturando almacenamiento.
    """
    list_id = _normalize_list_id(list_id)
    doc_ref = _list_doc_ref(list_id)
    if not doc_ref.get().exists:
        raise ValidationError("Lista no encontrada.")

    for history_doc in doc_ref.collection("history").stream():
        history_doc.reference.delete()
    doc_ref.delete()
