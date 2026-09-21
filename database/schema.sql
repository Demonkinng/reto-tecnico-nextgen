-- SmartBancs App — DDL

CREATE TYPE transaction_status AS ENUM (
    'pending',
    'completed',
    'failed'
);

CREATE TYPE ai_job_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed'
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id  UUID PRIMARY KEY,
    owner_name  VARCHAR(200) NOT NULL,
    balance     BIGINT NOT NULL DEFAULT 0,
    currency    VARCHAR(3) NOT NULL DEFAULT 'USD',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT  accounts_balance_valid CHECK (balance >= 0)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id              UUID PRIMARY KEY,
    idempotency_key             VARCHAR(128) NOT NULL UNIQUE,  -- para evitar duplicados por reintentos
    source_account_id           UUID NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
    destination_account_id      UUID NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
    amount                      BIGINT NOT NULL,
    currency                    VARCHAR(3) NOT NULL DEFAULT 'USD',
    status transaction_status   NOT NULL DEFAULT 'pending',
    trace_id                    VARCHAR(64) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at                TIMESTAMPTZ,
    CONSTRAINT transactions_amount_valid CHECK (amount > 0),
    CONSTRAINT transactions_different_accounts CHECK (source_account_id <> destination_account_id),
    CONSTRAINT transactions_completion_valid
        CHECK ((status = 'completed' AND completed_at IS NOT NULL)
            OR (status <> 'completed' AND completed_at IS NULL))
);

CREATE TABLE IF NOT EXISTS ai_jobs (
    transaction_id UUID PRIMARY KEY REFERENCES transactions(transaction_id) ON DELETE RESTRICT,
    status ai_job_status NOT NULL DEFAULT 'pending',
    attempts SMALLINT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_until TIMESTAMPTZ,
    result JSONB,
    last_error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ai_jobs_attempts_valid CHECK (attempts BETWEEN 0 AND 3),
    CONSTRAINT ai_jobs_attempts_by_status CHECK (
        (status = 'pending' AND attempts < 3)
        OR (status <> 'pending' AND attempts >= 1)
    ),
    CONSTRAINT ai_jobs_lease_valid CHECK (
        (status = 'processing' AND locked_until IS NOT NULL)
        OR (status <> 'processing' AND locked_until IS NULL)
    ),
    CONSTRAINT ai_jobs_result_valid CHECK (
        (status = 'completed' AND result IS NOT NULL
         AND jsonb_typeof(result) = 'object' AND completed_at IS NOT NULL)
        OR (status <> 'completed' AND result IS NULL AND completed_at IS NULL)
    ),
    CONSTRAINT ai_jobs_failure_explained CHECK (
        status <> 'failed'
        OR (last_error_code IS NOT NULL AND length(trim(last_error_code)) > 0)
    )
);

CREATE INDEX ai_jobs_pending_idx ON ai_jobs (next_attempt_at, transaction_id)
WHERE status = 'pending';

CREATE INDEX ai_jobs_expired_idx ON ai_jobs (locked_until, transaction_id)
WHERE status = 'processing';

-- Trigger para updated_at
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_accounts_updated_at ON accounts;
CREATE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
