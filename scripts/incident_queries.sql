-- Diagnóstico de PostgreSQL para SmartBancs.
-- Ejecutar con psql como usuario autorizado durante un incidente.
-- Las consultas 1 a 6 son de sólo lectura. Pueden mostrar texto SQL sensible;
-- no copiar sus resultados a canales públicos.

-- 1. Sesiones relevantes, duración y tipo de espera.
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    wait_event_type,
    wait_event,
    now() - query_start AS query_age,
    now() - xact_start AS transaction_age,
    pg_blocking_pids(pid) AS blocking_pids,
    LEFT(query, 300) AS query_preview
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY query_start NULLS LAST;

-- 2. Relación entre sesión bloqueada y cada PID bloqueante.
SELECT
    blocked.pid AS blocked_pid,
    blocked.application_name AS blocked_application,
    now() - blocked.query_start AS blocked_for,
    blocked.wait_event_type,
    blocked.wait_event,
    blocker.pid AS blocker_pid,
    blocker.usename AS blocker_user,
    blocker.application_name AS blocker_application,
    blocker.state AS blocker_state,
    now() - blocker.xact_start AS blocker_transaction_age,
    LEFT(blocked.query, 250) AS blocked_query,
    LEFT(blocker.query, 250) AS blocker_query
FROM pg_stat_activity AS blocked
CROSS JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS blocking_pid(pid)
JOIN pg_stat_activity AS blocker ON blocker.pid = blocking_pid.pid
WHERE blocked.datname = current_database()
ORDER BY blocked_for DESC;

-- 3. Transacciones abiertas por más de cinco segundos.
-- El umbral es inicial para la demostración; debe ajustarse con una línea base.
SELECT
    pid,
    usename,
    application_name,
    state,
    now() - xact_start AS transaction_age,
    wait_event_type,
    wait_event,
    LEFT(query, 300) AS query_preview
FROM pg_stat_activity
WHERE datname = current_database()
  AND xact_start IS NOT NULL
  AND now() - xact_start > interval '5 seconds'
  AND pid <> pg_backend_pid()
ORDER BY xact_start;

-- 4. Uso de conexiones por aplicación y estado.
SELECT
    COALESCE(NULLIF(application_name, ''), '(sin application_name)') AS application_name,
    state,
    COUNT(*) AS connections,
    COUNT(*) FILTER (WHERE wait_event_type IS NOT NULL) AS waiting_connections
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY application_name, state
ORDER BY connections DESC, application_name, state;

-- 5. Locks relacionados con las tablas principales.
SELECT
    activity.pid,
    activity.application_name,
    locks.locktype,
    locks.mode,
    locks.granted,
    locks.relation::regclass AS relation,
    now() - activity.query_start AS query_age,
    LEFT(activity.query, 300) AS query_preview
FROM pg_locks AS locks
JOIN pg_stat_activity AS activity ON activity.pid = locks.pid
WHERE activity.datname = current_database()
  AND locks.relation IN (
      'accounts'::regclass,
      'transactions'::regclass,
      'ai_jobs'::regclass
  )
ORDER BY locks.granted, query_age DESC;

-- 6. Consultas costosas si pg_stat_statements está instalada.
-- Este bloque usa comandos de psql (\gset y \if).
SELECT EXISTS (
    SELECT 1
    FROM pg_extension
    WHERE extname = 'pg_stat_statements'
) AS pg_stat_statements_available \gset

\if :pg_stat_statements_available
SELECT
    calls,
    ROUND(total_exec_time::numeric, 2) AS total_exec_time_ms,
    ROUND(mean_exec_time::numeric, 2) AS mean_exec_time_ms,
    rows,
    LEFT(query, 300) AS query_preview
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 10;
\else
\echo 'pg_stat_statements no está instalada; se omite la consulta 6.'
\endif

-- ACCIONES DE CONTENCIÓN: plantillas intencionalmente comentadas.
-- Antes de usarlas, validar PID, usuario, application_name, antigüedad,
-- transacción, impacto y autorización del líder del incidente.

-- Cancelar sólo la consulta actual y conservar la sesión:
-- SELECT pg_cancel_backend(<pid_validado>);

-- Último recurso: terminar la sesión y provocar rollback de su transacción:
-- SELECT pg_terminate_backend(<pid_validado>);
