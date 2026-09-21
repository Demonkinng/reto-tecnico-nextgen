# Respuesta al incidente: latencia, timeouts y deadlocks

Esta guía cubre el escenario del reto: durante un pico de quincena aumentan la latencia, los timeouts de base y los deadlocks. El objetivo inicial es proteger la integridad de las transferencias, medir el impacto y recuperar el servicio sin duplicar movimientos.

## Señales disponibles

| Señal | Pregunta que responde |
|---|---|
| `http_request_duration_seconds` | ¿La latencia HTTP aumentó y en qué ruta? |
| `http_requests_total` | ¿Qué códigos HTTP están creciendo? |
| `transactions_total` | ¿Aumentan operaciones rechazadas, duplicadas o fallidas? |
| `db_operation_duration_seconds` | ¿Qué operación lógica de base consume más tiempo? |
| `db_pool_wait_seconds` | ¿Las peticiones esperan una conexión libre? |
| `db_errors_total` | ¿Predominan `lock_timeout`, `deadlock`, `statement_timeout` o `pool_exhausted`? |
| `ai_jobs` y `ai_oldest_pending_age_seconds` | ¿La cola de recomendaciones se acumula aparte del dinero? |

Los logs JSON aportan `trace_id`, `operation`, `backend_pid` y `sqlstate`. `55P03` indica lock timeout, `40P01` deadlock y `57014` cancelación por tiempo límite en esta configuración.

## Diagnóstico paso a paso

1. **Confirmar la alerta y el impacto.** Anotar hora UTC, rutas afectadas, códigos HTTP y número aproximado de clientes. No asumir que toda latencia implica dinero perdido.
2. **Separar la capa afectada.** Comparar duración HTTP, duración SQL y espera del pool. Si suben las operaciones SQL o los errores de locks, concentrarse en PostgreSQL. Si sólo aumenta la cola de IA, las transferencias pueden seguir confirmándose.
3. **Elegir trazas representativas.** Buscar entre tres y cinco `trace_id` de peticiones lentas y localizar `operation`, `backend_pid`, duración y `sqlstate`.
4. **Inspeccionar PostgreSQL.** Ejecutar las consultas de sólo lectura de [`scripts/incident_queries.sql`](../scripts/incident_queries.sql). Identificar el PID bloqueado, cada bloqueador, la antigüedad de su transacción y su `application_name`.
5. **Determinar el patrón.** Verificar si varias operaciones esperan la misma cuenta, si existe una transacción abierta sin actividad o si el pool está agotado. Una cuenta de nómina muy usada puede serializar muchas escrituras sobre la misma fila aun con locks correctos.
6. **Elegir una mitigación y medirla.** Registrar responsable, hora y resultado. Confirmar que bajan la latencia y los errores antes de aplicar otra acción.

## Acciones inmediatas

### Contención de locks

- Validar primero PID, usuario, aplicación, consulta, antigüedad e impacto. Un PID puede reutilizarse después de que una sesión termina.
- Preferir `pg_cancel_backend` para cancelar la consulta actual. Usar `pg_terminate_backend` sólo como último recurso autorizado, porque cierra la sesión y revierte su transacción.
- Mantener el orden de locks por UUID y la misma clave de idempotencia en cada reintento. Un reintento con otra clave representa una transferencia nueva.
- No eliminar restricciones ni desactivar los locks de fila: protegen el saldo frente a carreras.

### Pool o base saturada

- Si crece `db_pool_wait_seconds` sin consultas lentas, limitar temporalmente el tráfico de entrada o la concurrencia del worker.
- No aumentar conexiones sin revisar CPU, memoria y `max_connections`; demasiadas sesiones pueden agravar la saturación.
- No escalar réplicas de la API cuando la misma fila o PostgreSQL sean el cuello de botella: añadiría competidores por los mismos locks.

### IA degradada

- El worker puede detenerse temporalmente sin revertir las transferencias ya confirmadas. Los trabajos quedan persistidos para reintento.
- Vigilar `ai_oldest_pending_age_seconds` y reactivar el worker de forma gradual después de estabilizar la base.

### Comunicación

- Informar que las transferencias presentan demora y pedir que se reintente con la misma clave de idempotencia.
- Actualizar el estado cada 15 minutos en SEV1, cada 30 minutos en SEV2 y cuando cambie el impacto en SEV3.
- No compartir consultas SQL, UUID, trazas completas ni credenciales en canales públicos.

## Escalamiento

| Severidad | Criterio inicial | Participantes |
|---|---|---|
| SEV1 | Transferencias generalizadamente detenidas, inconsistencia de saldo o riesgo de movimientos duplicados | Líder del incidente, backend, DBA, infraestructura, seguridad y responsable de negocio de inmediato |
| SEV2 | Degradación importante, timeouts recurrentes o un grupo relevante de clientes afectado | Backend, DBA e infraestructura; negocio informado |
| SEV3 | Fallo limitado sin pérdida de la función principal | Equipo propietario durante horario operativo |

Roles mínimos:

- **Líder del incidente:** fija severidad, prioridades, autorizaciones y criterio de cierre.
- **Operador técnico:** ejecuta una mitigación a la vez y conserva la evidencia.
- **Comunicaciones:** informa impacto y evolución sin publicar datos sensibles.
- **Relator:** registra la línea de tiempo UTC, decisiones y resultados.

Escalar inmediatamente a seguridad y negocio si existe diferencia de saldo, transferencia duplicada o exposición de datos. Escalar al DBA cuando haya bloqueadores persistentes, deadlocks repetidos o presión de conexiones. Involucrar al equipo de IA sólo si el dinero funciona y el impacto está limitado a recomendaciones.

## Cierre y seguimiento

El incidente puede cerrarse cuando la tasa de error y la latencia regresan al rango habitual, no quedan bloqueadores anómalos, el saldo se reconcilia y la cola de IA está estable o tiene un plan de recuperación. Conservar gráficas, consultas sanitizadas y `trace_id` representativos.

El post mortem usa la [plantilla del proyecto](postmortem-template.md). Debe distinguir la causa raíz de síntomas como “hubo timeouts”, asignar dueño y fecha a cada acción y evitar culpar a personas.
