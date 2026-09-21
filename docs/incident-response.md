## Incidente simulado: latencia alta + timeouts de DB + deadlocks en pico de quincena

### Diagnóstico

1. **Métricas primero**: se observa en el dashboard que `smartbancs_transaction_duration_seconds` (p99)
   sube abruptamente y `smartbancs_db_query_duration_seconds` sube en paralelo → el problema está en la
   capa de base de datos, no en IA (su métrica se mantiene estable) ni en la capa HTTP en sí.
2. **Confirmar contención/deadlocks en Postgres** (ver `scripts/incident_queries.sql`):
   - `pg_stat_activity` filtrado por `state='active'` y `wait_event_type='Lock'` para ver qué queries
     están bloqueadas y desde cuándo (`now() - query_start`).
   - `pg_locks` cruzado con `pg_stat_activity` para identificar la sesión que **sostiene** el lock que
     está bloqueando a las demás (bloqueante vs. bloqueado).
   - Postgres registra deadlocks detectados automáticamente en sus logs (`log_lock_waits=on`,
     `deadlock_timeout`), lo que confirma si hubo deadlocks reales o solo contención/espera larga.
3. **Correlacionar con logs por `trace_id`**: tomar 3-5 `transaction_id` de transacciones lentas
   reportadas y buscar su traza completa — normalmente se ve el patrón: "adquiriendo lock en accounts →
   esperando... → timeout" y permite ver si todas afectan la(s) misma(s) fila(s) (p. ej. una cuenta
   "hot" con muchísimas transacciones concurrentes, típico de nómina en quincena donde una cuenta
   corporativa paga a miles de empleados).

### 7.2 Acciones inmediatas (mitigación, no fix definitivo)

- **Terminar/cancelar las queries bloqueantes de larga duración** que no son parte de una transacción de
  usuario activa (`pg_terminate_backend(pid)` sobre sesiones "zombie" o de más de X segundos bloqueadas),
  liberando el lock para que el resto avance.
- **Reducir el alcance del lock**: si el cuello de botella es un `UPDATE accounts SET balance = ... WHERE id = X`
  que bloquea fila por fila, verificar que se está usando locking a nivel de fila (no de tabla) y que no
  hay un índice faltante causando escaneo secuencial con locks innecesarios sobre filas no relacionadas.
- **Aplicar `statement_timeout` y `lock_timeout` agresivos temporalmente** a nivel de conexión para que
  las transacciones que no pueden completarse en tiempo razonable fallen rápido (y se reintenten desde el
  cliente) en vez de acumular locks indefinidamente.
- **Activar/ajustar pgbouncer** (pool `transaction` mode) si el problema incluye agotamiento de
  conexiones, y subir temporalmente el límite de conexiones si hay margen de recursos en el servidor.
- **Balanceo de carga / escalado horizontal** del `transaction-api` si el cuello de botella es CPU/handlers
  saturados y no la DB en sí — pero si el cuello de botella *es* la DB, escalar más réplicas de la API
  **empeora** el problema (más conexiones compitiendo por los mismos locks), así que primero hay que
  confirmar con las métricas dónde está el cuello de botella antes de escalar a ciegas.
- **Circuit breaker hacia Bancs y hacia IA**: si en medio del incidente esas llamadas externas también
  empiezan a fallar/tardar, abrir el circuito para que no consuman threads/conexiones adicionales del
  servicio principal mientras se resuelve la causa raíz.
- **Comunicación**: activar banner de "transferencias con demora" en la app para gestionar expectativa
  del usuario mientras se mitiga (evita que el usuario reintente múltiples veces y genere más carga).

---

## Estructura de post-mortem y prevención

### Estructura del informe post-mortem

1. **Resumen ejecutivo**: qué pasó, impacto en usuarios (número de transacciones afectadas/fallidas,
   duración total del incidente), severidad.
2. **Línea de tiempo**: timestamps de detección, escalamiento, mitigación, resolución, con evidencia
   (gráficas de métricas, capturas de `pg_locks`, logs con `trace_id` específicos).
3. **Causa raíz** (no solo el síntoma): p. ej. "una cuenta corporativa de nómina generó miles de updates
   concurrentes sobre la misma fila, causando contención de locks que degeneró en timeouts en cascada",
   distinto del síntoma "hubo timeouts de DB".
4. **Qué funcionó / qué no funcionó** en la respuesta (detección, alertas, runbooks, comunicación).
5. **Acciones preventivas** con dueño y fecha (no solo una lista de buenas intenciones).
6. **Severidad y clasificación** (SEV1/2/3) para trazabilidad histórica y análisis de tendencias.

### Acciones preventivas concretas

**Infraestructura:**
- Particionar/shard la tabla `accounts`/`transactions` de forma que una cuenta "hot" (nómina) no compita
  por el mismo bloque de páginas/locks que el resto del tráfico; o aplicar un patrón de "cola por cuenta"
  para transacciones masivas hacia una sola cuenta origen (procesarlas secuencialmente y no en paralelo
  puro, ya que paralelizarlas sobre la misma fila no gana nada y sí genera contención).
- Añadir un **read replica** para consultas de solo lectura (consulta de saldo, historial) para que no
  compitan con los `UPDATE`s del hot path.
- Autoscaling con límites y alertas de saturación de connection pool **antes** de llegar al 100%.
- Runbook de "modo degradado" documentado y ensayado (game days / chaos engineering periódico simulando
  picos de quincena en staging).

**Código:**
- Reintentos con backoff + jitter en el cliente para transacciones que fallan por lock timeout, en vez de
  reintento inmediato (que agrava la contención).
- Revisar que todas las escrituras a `accounts` usen `SELECT ... FOR UPDATE` con el **orden consistente**
  de adquisición de locks entre cuentas (p. ej. siempre por `account_id` ascendente cuando una transacción
  toca dos cuentas) para eliminar deadlocks por orden inverso.
- Añadir *idempotency keys* obligatorias y índice único para que los reintentos del cliente no generen
  transacciones duplicadas ni locks adicionales innecesarios.
- Feature flag / kill-switch para desactivar temporalmente funcionalidades no críticas (p. ej. IA) sin
  desplegar código nuevo en medio de un incidente.
- Añadir pruebas de carga automatizadas (k6/Locust) en el pipeline de CI para el escenario "pico de
  quincena" antes de cada release mayor, no solo pruebas funcionales.