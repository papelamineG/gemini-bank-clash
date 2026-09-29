"""users service — owns identity.

Endpoints:
    POST /users      register
    POST /sessions   login -> signed token
    GET  /users/<id> user lookup (auth)
    GET  /users      list users id + full_name (auth)
    GET  /healthz    healthcheck
"""
import sqlite3

from flask import Flask, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

import models
from auth import make_token, require_auth


def create_app():
    app = Flask(__name__)
    models.init_db()

    @app.get("/healthz")
    def healthz():
        """Returns the health status of the users service."""
        return jsonify({"status": "ok"})

    @app.post("/users")
    def register():
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        full_name = (body.get("full_name") or "").strip()
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""

        if not username or not full_name or not email or not password:
            return jsonify({"error": "missing_fields"}), 400

        try:
            user_id = models.create_user(
                username, full_name, email, generate_password_hash(password)
            )
        except sqlite3.IntegrityError:
            return jsonify({"error": "user_exists"}), 409

        return jsonify({"id": user_id, "username": username, "full_name": full_name}), 201

    @app.post("/sessions")
    def login():
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        user = models.get_user_by_username(username)
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "invalid_credentials"}), 401

        token = make_token(user["id"], user["full_name"])
        return jsonify(
            {"token": token, "user_id": user["id"], "full_name": user["full_name"]}
        )

    @app.get("/users/<int:user_id>")
    @require_auth
    def get_user(user_id):
        user = models.get_user_by_id(user_id)
        if not user:
            return jsonify({"error": "not_found"}), 404
        return jsonify(
            {"id": user["id"], "username": user["username"], "full_name": user["full_name"]}
        )

    @app.get("/users")
    @require_auth
    def get_users():
        return jsonify(models.list_users())

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
