# Implementación del microservicio de transacciones

Autorizada por el usuario para ejecutarse en esta carpeta. No usar Git, crear ramas, hacer commits ni push. Se conserva el esquema actual: UUID, enums y dinero en centavos de USD.

## Alcance y pasos

- [x] Pruebas de API contra PostgreSQL dedicado: validación, atomicidad, duplicados y concurrencia.
- [x] API FastAPI con `main.py`, `schemas.py`, `service.py`, `database.py`, `config.py` y `telemetry.py`.
- [x] Worker independiente: reserva de tareas, consumo HTTP de IA fuera de la transacción SQL, reintentos limitados y recuperación de reservas.
- [x] Compose, Dockerfile, observabilidad, instrucciones y justificación del resultado.
- [x] Verificación completa y revisión independiente de sólo lectura, sin herramientas Git.

## Contrato

`POST /transactions`: cabecera `Idempotency-Key` (1-128 caracteres alfanuméricos, punto, guion, guion bajo o dos puntos); cuerpo con UUID de origen/destino, `amount` entero estricto de centavos positivo y `currency` USD. La API genera `transaction_id`. Devuelve 201 al crear y 200 al repetir los mismos datos. Misma clave con otros datos: 409. Entrada inválida: 422, cuenta inexistente: 404, saldo insuficiente o desbordamiento del destino: 409, indisponibilidad temporal de base: 503. No se publica éxito antes del commit.

`GET /transactions/{id}`, `GET /transactions/{id}/recommendation`, `GET /accounts/{id}` para demostrar saldos; `/health`, `/ready` y `/metrics` para operación. La correlación usa `X-Trace-ID`, UUID generado si falta o es inválido; se devuelve en respuestas y persiste en la transferencia. Un reintento HTTP puede tener una correlación nueva, pero conserva el identificador original del trabajo de IA.

## Decisiones de implementación

Se utiliza un pool síncrono Psycopg y endpoints `def`, que FastAPI ejecuta en su grupo de hilos; no se bloquea el bucle asíncrono con consultas síncronas. La creación consulta idempotencia, bloquea las cuentas en orden UUID y vuelve a consultar la clave después de adquirir los bloqueos. Este segundo control evita rechazar por saldo insuficiente un reintento cuyo primer intento ya descontó el dinero. Después inserta la transferencia confirmada con unicidad de clave, actualiza saldos y crea la tarea en una transacción SQL. Si otra petición con una clave igual y cuentas diferentes gana la inserción, se compara el contenido y se rechaza el conflicto sin mover dinero.

El worker pertenece al servicio de transacciones. Se inicia con un perfil opcional de Compose mientras el servicio de IA se desarrolla. Se proporciona un contrato HTTP y se prueba con un servidor local controlado; no se consume una API externa real ni se inventa una recomendación en la API transaccional. Sólo se envían ID, monto y moneda, nunca nombres ni números de cuenta. Cada tarea permite tres intentos; `attempts` y `locked_until` identifican la reserva para impedir que una respuesta tardía sobrescriba el trabajo de un intento posterior.

No se añade Redis: la tabla `ai_jobs` ya conserva las tareas de manera duradera y permite reducir dependencias del MVP. Los logs JSON identifican operación, duración, SQLSTATE y PID de base ante fallos; las métricas usan etiquetas acotadas y no contienen IDs de cuentas o transacciones. Prometheus será opcional en Compose. No se afirma capacidad de 10 000 transacciones/s ni cumplimiento universal de dos segundos sin prueba de carga.

La base que ya está levantada no se usa para las pruebas ni se reinicia. Se crea una instancia desechable con el mismo esquema y datos ficticios. No se alteran saldos existentes. Los permisos de producción, autenticación, integración Bancs, ETL y proveedor real de IA quedan fuera de este incremento local.

## Evidencia de verificación

Verificado el 20 de septiembre de 2026 (hora local):

- Suite final: **30 pruebas aprobadas**, PostgreSQL 18.6 real en un contenedor desechable y servidor HTTP controlado para IA. Dos avisos de deprecación proceden de TestClient/Starlette/AnyIO; no son fallos de las pruebas, pero requieren revisar compatibilidad al actualizar dependencias.
- Construcción de la imagen `nextgen-transaction:local` completada. En la imagen final, `/health`, `/ready`, `/docs`, `/openapi.json` y `/metrics` respondieron `200`.
- Petición HTTP a la API en contenedor: creación `201`, repetición `200`, mismo resultado, un solo débito de un centavo y tarea IA pendiente en la base de pruebas.
- Ambos archivos Compose validaron su configuración. `promtool` validó la configuración de Prometheus y las cinco reglas de alerta.
- Revisión independiente de sólo lectura: se corrigió el cálculo de métricas de tareas para ejecutarlo cada 15 segundos y se unificó la clasificación `pool_exhausted` entre API y worker. La revisión posterior confirmó las correcciones.
- No se ejecutó ningún comando Git ni se modificaron `database/schema.sql`, `database/seed.sql` o la base de desarrollo existente.

Estas comprobaciones acreditan el flujo funcional del incremento, no un benchmark, la integración con un modelo externo ni todos los entregables del reto.
