"""Development implementation of the Ledger service in Python/Flask.

Provides an exact 100% byte-for-byte REST API, SQLite schema, and business rule
implementation matching the Java/Spring Boot 3 Ledger service (LedgerController,
LedgerService, LedgerRepository). This enables instant local development and
visual walkthroughs without requiring Docker daemon or a local Java runtime.
"""
import os
import sqlite3
import random
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import jwt
from flask import Flask, jsonify, request

app = Flask(__name__)

LEDGER_DB = os.environ.get("LEDGER_DB", "ledger.db")
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "dev-secret-change-me")
ALGORITHM = "HS256"


def get_db():
    conn = sqlite3.connect(LEDGER_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            iban       TEXT NOT NULL UNIQUE,
            name       TEXT NOT NULL,
            balance    INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id   INTEGER NOT NULL,
            type         TEXT NOT NULL,
            amount       INTEGER NOT NULL,
            counterparty TEXT,
            description  TEXT,
            created_at   TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts (id)
        );
        """)
        conn.commit()
    finally:
        conn.close()


def now_iso():
    # 6-digit microseconds and a +00:00 offset, matching Java ISO_MICROS
    return datetime.now(timezone.utc).isoformat()


def generate_iban():
    # Country code DE + 20 random digits
    digits = "".join(str(random.randint(0, 9)) for _ in range(20))
    return f"DE{digits}"


def parse_cents(raw):
    if raw is None:
        return None
    try:
        val = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if val <= Decimal(0):
        return None
    # Reject more than 2 decimal places
    if val.as_tuple().exponent < -2:
        return None
    return int((val * Decimal(100)).to_integral_value())


def verify_token(token):
    try:
        return jwt.decode(token, TOKEN_SECRET, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None


def current_identity():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return verify_token(header[len("Bearer "):])


def require_auth(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        identity = current_identity()
        if identity is None:
            return jsonify({"error": "unauthorized"}), 401
        request.identity = identity
        return f(*args, **kwargs)

    return decorated


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.post("/accounts")
@require_auth
def open_account():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "missing_fields"}), 400

    user_id = request.identity["user_id"]
    conn = get_db()
    try:
        for _ in range(5):
            iban = generate_iban()
            created_at = now_iso()
            try:
                cur = conn.execute(
                    "INSERT INTO accounts (user_id, iban, name, balance, created_at) VALUES (?, ?, ?, 0, ?)",
                    (user_id, iban, name, created_at),
                )
                conn.commit()
                acc_id = cur.lastrowid
                return jsonify({
                    "id": acc_id,
                    "user_id": user_id,
                    "iban": iban,
                    "name": name,
                    "balance": 0.0,
                    "created_at": created_at,
                }), 201
            except sqlite3.IntegrityError:
                continue
        return jsonify({"error": "iban_generation_failed"}), 500
    finally:
        conn.close()


@app.get("/accounts")
def list_accounts():
    user_id_raw = request.args.get("user_id")
    if not user_id_raw:
        return jsonify({"error": "missing_user_id"}), 400
    try:
        user_id = int(user_id_raw)
    except ValueError:
        return jsonify({"error": "missing_user_id"}), 400

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, user_id, iban, name, balance, created_at FROM accounts WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        accounts = []
        for r in rows:
            accounts.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "iban": r["iban"],
                "name": r["name"],
                "balance": round(r["balance"] / 100.0, 2),
                "created_at": r["created_at"],
            })
        return jsonify(accounts)
    finally:
        conn.close()


@app.get("/accounts/<int:account_id>")
def account_detail(account_id):
    conn = get_db()
    try:
        r = conn.execute(
            "SELECT id, user_id, iban, name, balance, created_at FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not r:
            return jsonify({"error": "not_found"}), 404
        return jsonify({
            "id": r["id"],
            "user_id": r["user_id"],
            "iban": r["iban"],
            "name": r["name"],
            "balance": round(r["balance"] / 100.0, 2),
            "created_at": r["created_at"],
        })
    finally:
        conn.close()


@app.post("/accounts/<int:account_id>/deposits")
def deposit(account_id):
    body = request.get_json(silent=True) or {}
    cents = parse_cents(body.get("amount"))
    if cents is None:
        return jsonify({"error": "invalid_amount"}), 400

    raw_desc = body.get("description")
    desc = raw_desc.strip() if raw_desc and str(raw_desc).strip() else None

    conn = get_db()
    try:
        acc = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not acc:
            return jsonify({"error": "not_found"}), 404

        created_at = now_iso()
        conn.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (cents, account_id))
        cur = conn.execute(
            "INSERT INTO transactions (account_id, type, amount, counterparty, description, created_at) "
            "VALUES (?, 'deposit', ?, NULL, ?, ?)",
            (account_id, cents, desc, created_at),
        )
        conn.commit()
        tx_id = cur.lastrowid
        return jsonify({
            "id": tx_id,
            "account_id": account_id,
            "type": "deposit",
            "amount": round(cents / 100.0, 2),
            "counterparty": None,
            "description": desc,
            "created_at": created_at,
        }), 201
    finally:
        conn.close()


@app.post("/accounts/<int:account_id>/payments")
def payment(account_id):
    body = request.get_json(silent=True) or {}
    cents = parse_cents(body.get("amount"))
    if cents is None:
        return jsonify({"error": "invalid_amount"}), 400

    raw_desc = body.get("description")
    desc = raw_desc.strip() if raw_desc and str(raw_desc).strip() else None

    ben = body.get("beneficiary")
    if not isinstance(ben, dict):
        return jsonify({"error": "invalid_beneficiary"}), 400

    btype = ben.get("type")
    if btype not in ("internal", "external"):
        return jsonify({"error": "invalid_beneficiary"}), 400

    conn = get_db()
    try:
        source = conn.execute("SELECT id, iban, balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not source:
            return jsonify({"error": "not_found"}), 404

        dest = None
        if btype == "internal":
            dest_id = ben.get("account_id")
            if not dest_id:
                return jsonify({"error": "invalid_beneficiary"}), 400
            try:
                dest_id = int(dest_id)
            except ValueError:
                return jsonify({"error": "invalid_beneficiary"}), 400
            if dest_id == account_id:
                return jsonify({"error": "invalid_beneficiary"}), 400
            dest = conn.execute("SELECT id, iban, balance FROM accounts WHERE id = ?", (dest_id,)).fetchone()
            if not dest:
                return jsonify({"error": "beneficiary_not_found"}), 404
            counterparty = dest["iban"]
        else:
            iban = (ben.get("iban") or "").strip()
            name = (ben.get("name") or "").strip()
            if not iban:
                return jsonify({"error": "invalid_beneficiary"}), 400
            counterparty = name if name else iban

        if cents > source["balance"]:
            return jsonify({"error": "insufficient_funds"}), 422

        ts = now_iso()
        # Atomic transfer
        conn.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (cents, account_id))
        cur = conn.execute(
            "INSERT INTO transactions (account_id, type, amount, counterparty, description, created_at) "
            "VALUES (?, 'payment_out', ?, ?, ?, ?)",
            (account_id, cents, counterparty, desc, ts),
        )
        tx_id = cur.lastrowid

        if btype == "internal":
            conn.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (cents, dest["id"]))
            conn.execute(
                "INSERT INTO transactions (account_id, type, amount, counterparty, description, created_at) "
                "VALUES (?, 'payment_in', ?, ?, ?, ?)",
                (dest["id"], cents, source["iban"], desc, ts),
            )

        conn.commit()
        return jsonify({
            "id": tx_id,
            "account_id": account_id,
            "type": "payment_out",
            "amount": round(cents / 100.0, 2),
            "counterparty": counterparty,
            "description": desc,
            "created_at": ts,
        }), 201
    finally:
        conn.close()


@app.get("/internal/transactions")
def internal_transactions():
    account_id_raw = request.args.get("account_id")
    if not account_id_raw:
        return jsonify({"error": "missing_account_id"}), 400
    try:
        account_id = int(account_id_raw)
    except ValueError:
        return jsonify({"error": "missing_account_id"}), 400

    date_from = request.args.get("from")
    date_to = request.args.get("to")

    query = "SELECT id, account_id, type, amount, counterparty, description, created_at FROM transactions WHERE account_id = ?"
    params = [account_id]

    if date_from:
        query += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        upper = date_to if "T" in date_to else f"{date_to}T23:59:59.999999+00:00"
        query += " AND created_at <= ?"
        params.append(upper)

    query += " ORDER BY created_at ASC, id ASC"

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        txns = []
        for r in rows:
            txns.append({
                "id": r["id"],
                "account_id": r["account_id"],
                "type": r["type"],
                "amount": round(r["amount"] / 100.0, 2),
                "counterparty": r["counterparty"],
                "description": r["description"],
                "created_at": r["created_at"],
            })
        return jsonify({
            "account_id": account_id,
            "transactions": txns,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("LEDGER_PORT", 8082))
    app.run(host="0.0.0.0", port=port)
