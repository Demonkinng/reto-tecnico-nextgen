from uuid import uuid4

from .schemas import MAX_CENTS


class ServiceError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def existing_transfer(db, connection, key, request):
    row = db.query(connection, "find_idempotency", "SELECT * FROM transactions WHERE idempotency_key = %s", (key,)).fetchone()
    if row is None:
        return None
    if any(row[field] != getattr(request, field) for field in ("source_account_id", "destination_account_id", "amount", "currency")):
        raise ServiceError(409, "idempotency_conflict", "La clave ya pertenece a otra transferencia")
    if row["status"] != "completed":
        raise ServiceError(409, "transaction_not_completed", "La transferencia previa aún no está completada")
    return row


def create_transfer(db, request, key, trace_id):
    with db.connection() as connection:
        with connection.transaction():
            previous = existing_transfer(db, connection, key, request)
            if previous:
                return previous, False

            # Siempre adquirir todos los bloqueos en el mismo orden.
            rows = db.query(connection, "lock_accounts", """
                SELECT account_id, balance, currency FROM accounts
                WHERE account_id IN (%s, %s) ORDER BY account_id FOR UPDATE
            """, (request.source_account_id, request.destination_account_id)).fetchall()

            # Una petición con la misma clave pudo terminar mientras esperábamos.
            previous = existing_transfer(db, connection, key, request)
            if previous:
                return previous, False
            if len(rows) != 2:
                raise ServiceError(404, "account_not_found", "Una de las cuentas no existe")
            accounts = {row["account_id"]: row for row in rows}
            source = accounts[request.source_account_id]
            destination = accounts[request.destination_account_id]
            if source["currency"] != "USD" or destination["currency"] != "USD":
                raise ServiceError(409, "currency_mismatch", "Ambas cuentas deben operar en USD")
            if source["balance"] < request.amount:
                raise ServiceError(409, "insufficient_funds", "Saldo insuficiente")
            if destination["balance"] + request.amount > MAX_CENTS:
                raise ServiceError(409, "balance_limit", "El saldo de destino excedería el límite")

            row = db.query(connection, "insert_transaction", """
                INSERT INTO transactions
                    (transaction_id, idempotency_key, source_account_id, destination_account_id,
                     amount, currency, status, trace_id, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'completed', %s, now())
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING *
            """, (uuid4(), key, request.source_account_id, request.destination_account_id,
                  request.amount, request.currency, trace_id)).fetchone()
            if row is None:
                # También cubre claves iguales con pares de cuentas diferentes.
                return existing_transfer(db, connection, key, request), False

            db.query(connection, "debit_account", "UPDATE accounts SET balance = balance - %s WHERE account_id = %s",
                     (request.amount, request.source_account_id))
            db.query(connection, "credit_account", "UPDATE accounts SET balance = balance + %s WHERE account_id = %s",
                     (request.amount, request.destination_account_id))
            db.query(connection, "insert_ai_job", "INSERT INTO ai_jobs (transaction_id) VALUES (%s)", (row["transaction_id"],))
        # Sólo se anuncia éxito después de que el contexto haya confirmado el commit.
        db.telemetry.log("database_commit", transaction_id=row["transaction_id"], backend_pid=connection.info.backend_pid)
        return row, True


def get_transfer(db, transaction_id):
    with db.connection() as connection:
        row = db.query(connection, "get_transaction", "SELECT * FROM transactions WHERE transaction_id = %s", (transaction_id,)).fetchone()
    if row is None:
        raise ServiceError(404, "transaction_not_found", "Transferencia no encontrada")
    return row


def get_account(db, account_id):
    with db.connection() as connection:
        row = db.query(connection, "get_account", "SELECT account_id, balance, currency FROM accounts WHERE account_id = %s", (account_id,)).fetchone()
    if row is None:
        raise ServiceError(404, "account_not_found", "Cuenta no encontrada")
    return row


def get_recommendation(db, transaction_id):
    with db.connection() as connection:
        row = db.query(connection, "get_recommendation", "SELECT transaction_id, status, attempts, result, last_error_code FROM ai_jobs WHERE transaction_id = %s", (transaction_id,)).fetchone()
    if row is None:
        raise ServiceError(404, "recommendation_not_found", "No hay una tarea de IA para esta transferencia")
    return row
