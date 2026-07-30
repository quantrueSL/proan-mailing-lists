import os

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from utils import (
    FIRESTORE_DATABASE_ID,
    ValidationError,
    authenticate_user,
    delete_list,
    ensure_authenticated,
    get_list,
    get_list_history,
    list_lists,
    logger,
    save_list,
)


load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")


@app.before_request
def log_request():
    if request.path != "/health":
        logger.info("%s %s", request.method, request.path)


@app.before_request
def require_auth():
    auth_response = ensure_authenticated()
    if auth_response is not None:
        return auth_response


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "mailing-lists",
        "firestore_database_id": FIRESTORE_DATABASE_ID,
    }), 200


@app.route("/")
def index():
    return render_template("index.html", firestore_database_id=FIRESTORE_DATABASE_ID)


@app.route("/login")
def serve_login():
    if session.get("auth_user_id"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        return jsonify({"error": "Usuario y contrasena requeridos."}), 400

    try:
        user = authenticate_user(username, password)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    if not user:
        return jsonify({"error": "Credenciales invalidas o usuario inactivo."}), 401

    session.clear()
    session.permanent = True
    session["auth_user_id"] = user["user_id"]
    session["auth_username"] = user["username"]
    return jsonify({"ok": True, "redirect": "/"}), 200


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True}), 200


@app.route("/api/lists")
def api_lists():
    return jsonify({"lists": list_lists()}), 200


@app.route("/api/lists/<list_id>")
def api_list(list_id: str):
    mailing_list = get_list(list_id)
    if not mailing_list:
        return jsonify({"error": "Lista no encontrada"}), 404
    return jsonify(mailing_list), 200


@app.route("/api/lists/<list_id>", methods=["POST"])
def api_save_list(list_id: str):
    payload = request.get_json(silent=True) or {}

    # `updated_by` sale de la sesion, nunca del cuerpo de la peticion. Antes lo
    # mandaba el cliente (y la interfaz no lo mandaba en absoluto), asi que todo
    # el historial quedaba firmado como "system" y no servia para saber quien
    # habia cambiado que.
    payload["updated_by"] = session.get("auth_username", "desconocido")

    try:
        saved = save_list(list_id, payload)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Error guardando lista %s: %s", list_id, exc)
        return jsonify({"error": "No se pudo guardar la lista."}), 500
    return jsonify(saved), 200


@app.route("/api/lists/<list_id>", methods=["DELETE"])
def api_delete_list(list_id: str):
    try:
        delete_list(list_id)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.exception("Error borrando lista %s: %s", list_id, exc)
        return jsonify({"error": "No se pudo borrar la lista."}), 500
    return jsonify({"ok": True, "list_id": list_id}), 200


@app.route("/api/lists/<list_id>/history")
def api_list_history(list_id: str):
    try:
        items = get_list_history(list_id)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"history": items}), 200


@app.route("/api/session")
def api_session():
    """Quien esta conectado, para poder mostrarlo en la interfaz."""
    return jsonify({"username": session.get("auth_username")}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)
