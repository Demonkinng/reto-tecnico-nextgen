# Observabilidad del servicio transaccional

## Qué mirar y por qué

| Señal | Utilidad |
|---|---|
| `http_requests_total` por método, ruta y código | Separar tráfico, rechazos de negocio y errores de servidor |
| `http_request_duration_seconds` | Identificar degradación de latencia; el objetivo del reto es menor a 2 s |
| `transactions_total` por resultado | Separar transferencias nuevas, duplicadas, rechazadas y fallos de base |
| `db_operation_duration_seconds` por operación | Identificar el paso SQL que consume tiempo |
| `db_pool_wait_seconds` | Distinguir espera de conexión de una consulta lenta |
| `db_errors_total` por tipo | Diferenciar deadlock, lock timeout, statement timeout y saturación del pool |
| `ai_request_duration_seconds`, `ai_requests_total` | Detectar demora y fallos del servicio IA |
| `ai_jobs`, `ai_oldest_pending_age_seconds` | Observar estado y acumulación de tareas en el worker |

Los IDs no se usan como etiquetas métricas: crear una serie por transferencia consumiría memoria sin límite. Se reservan para logs. Se ejecuta un proceso Uvicorn por contenedor porque los contadores viven en memoria; al escalar, Prometheus debe recoger cada réplica. No aumentar `--workers` sin configurar antes el modo multiproceso de métricas.

Logs críticos: `database_commit`, `transaction_completed`, `transaction_duplicate`, `operation_rejected`, `database_failure`, `db_operation_failed`, `db_operation_slow`, `unexpected_error`, `job_started`, `ai_call_finished` y `job_finished`. Los eventos del worker comparten el `trace_id` original. Una nueva petición HTTP de reintento puede tener otro trace; la respuesta mantiene la transacción y el trace original persistidos.

El worker actualiza los totales de tareas cada 15 segundos; no recorre el historial después de cada trabajo. Ese agregado todavía crece con el historial, por lo que un sistema de mayor volumen necesitaría retención, estadísticas o un recolector independiente.

## Consultas en Prometheus

Transacciones nuevas por segundo:

```promql
sum(rate(transactions_total{job="ms-transaction",result="completed"}[5m]))
```

Latencia p95 de recepción de transferencias, incluidos errores y duplicados:

```promql
histogram_quantile(0.95, sum by (le) (
  rate(http_request_duration_seconds_bucket{job="ms-transaction",route="/transactions",method="POST"}[5m])
))
```

La métrica es una distribución, no una garantía de que cada transferencia tarde menos de dos segundos. `lock_timeout=500 ms` y `statement_timeout=1000 ms` limitan cada operación SQL, no la duración acumulada de toda la petición. El objetivo necesita pruebas de carga con datos, contención y recursos representativos.

## Diagnosticar timeouts y bloqueos

1. Ubicar el intervalo con errores/latencia y el `trace_id` afectado.
2. Buscar `db_operation_failed` o `db_operation_slow`: `operation` indica el SQL lógico y `backend_pid` el proceso exacto; `55P03` señala lock timeout, `40P01` deadlock y `57014` cancelación por tiempo límite en esta configuración.
3. Conectar como administrador a PostgreSQL y observar bloqueadores mientras el incidente está activo:

```sql
SELECT pid, application_name, state, wait_event_type, wait_event,
       now() - query_start AS query_age,
       pg_blocking_pids(pid) AS blocking_pids,
       query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY query_start;
```

El PID puede reutilizarse después de terminar una sesión; correlacionarlo con la hora y `application_name` (`ms-transaction` o `transaction-worker`). El nombre de operación corresponde a la consulta en `service.py` o `worker.py`; por ejemplo, `lock_accounts` identifica la adquisición ordenada de bloqueos. La inspección de actividad SQL queda restringida a administradores porque otras consultas podrían contener datos sensibles.

Los bloqueos de cuenta se adquieren en orden UUID para evitar ciclos habituales entre transferencias opuestas. PostgreSQL sigue siendo quien detecta deadlocks de otras operaciones. Ante contención, el cliente recibe `503` y puede reintentar con la misma clave de idempotencia. Una cancelación no debe ejecutarse automáticamente sólo por detectar una consulta lenta: identificar primero la sesión y el impacto.

Las reglas locales de alerta tienen umbrales iniciales para la demostración; necesitan ajustarse con una línea base. Prometheus no reúne logs ni crea spans: Grafana, Loki, OpenTelemetry y un canal de alertas son ampliaciones pendientes. La guía completa del incidente, escalamiento y post mortem del reto todavía debe desarrollarse.
