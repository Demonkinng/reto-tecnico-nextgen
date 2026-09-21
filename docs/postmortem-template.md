# Post-mortem — [Título del incidente]

**Severidad:** SEV[1|2|3]
**Fecha del incidente:** AAAA-MM-DD
**Duración total:** [detección → resolución]
**Autor(es):** 
**Estado:** Borrador / Revisado / Aprobado

## 1. Resumen ejecutivo
(2-3 líneas: qué pasó, a quién afectó, impacto en negocio)

## 2. Impacto
- Transacciones afectadas / fallidas:
- Usuarios impactados (estimado):
- Duración de la degradación visible al usuario:

## 3. Línea de tiempo (UTC)
| Hora | Evento |
|---|---|
|  | Alerta disparada: ... |
|  | Se confirma contención en `accounts` vía `pg_locks` |
|  | Se termina sesión bloqueante `pid=...` |
|  | Latencia vuelve a rango normal |

## 4. Causa raíz
(No el síntoma — la causa de fondo. Ej: "una cuenta de nómina generó actualizaciones
concurrentes sobre la misma fila sin cola de serialización, causando contención de
locks que escaló a timeouts en cascada bajo el pico de quincena")

## 5. Qué funcionó bien
-

## 6. Qué no funcionó / gaps detectados
-

## 7. Acciones preventivas
| Acción | Área | Dueño | Fecha objetivo |
|---|---|---|---|
| Serializar transferencias masivas hacia una misma cuenta destino | Código | | |
| Particionar/aislar cuentas "hot" | Infraestructura | | |
| Añadir alerta sobre `smartbancs_outbox_pending` con umbral | Observabilidad | | |
| Runbook de incidente ensayado en game day | Operaciones | | |

## 8. Anexos
- Gráficas de métricas (latencia p99, `db_pool_in_use`, `outbox_pending`)
- Salida de `scripts/incident_queries.sql` durante el incidente
- `trace_id` de transacciones representativas afectadas
