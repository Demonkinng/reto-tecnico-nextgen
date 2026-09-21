# Post mortem — [título del incidente]

**Severidad:** SEV[1|2|3]  
**Inicio UTC:** AAAA-MM-DD HH:MM  
**Fin UTC:** AAAA-MM-DD HH:MM  
**Duración:** [minutos]  
**Autoría:** [nombres o roles]  
**Estado:** Borrador / Revisado / Aprobado

Este informe analiza el sistema y el proceso sin asignar culpa personal.

## 1. Resumen

[Qué ocurrió, a quién afectó, cuánto duró y cuál fue el estado final.]

## 2. Impacto

- Transferencias completadas, rechazadas y con respuesta incierta:
- Clientes o cuentas afectadas, usando datos agregados:
- Impacto financiero confirmado:
- Impacto en recomendaciones de IA:
- Periodo visible para clientes:

## 3. Detección

- Primera alerta o reporte:
- Métrica o log que permitió confirmar el incidente:
- Tiempo desde el inicio hasta la detección:
- Mejora necesaria en la detección:

## 4. Línea de tiempo UTC

| Hora | Evento, decisión o cambio de impacto | Evidencia |
|---|---|---|
| | Alerta recibida | Regla o reporte |
| | Incidente clasificado y roles asignados | Registro operativo |
| | Causa o bloqueador identificado | Consulta sanitizada |
| | Mitigación aplicada | Acción y responsable |
| | Servicio estabilizado | Métricas |
| | Cierre y reconciliación | Resultado |

## 5. Causa raíz

[Explicar el mecanismo técnico y organizativo que permitió el incidente. “Hubo timeouts” describe un síntoma y no una causa.]

## 6. Factores contribuyentes

- Condiciones de tráfico o datos:
- Diseño o configuración:
- Detección y alertas:
- Procedimientos y comunicación:

## 7. Respuesta

### Qué funcionó

-

### Qué dificultó la recuperación

-

### Riesgos introducidos por la mitigación

-

## 8. Acciones

| Acción concreta | Prioridad | Dueño | Fecha objetivo | Criterio verificable de cierre | Estado |
|---|---|---|---|---|---|
| | Alta/Media/Baja | | | Prueba, métrica o documento que demuestra el cierre | Pendiente |

## 9. Anexos

- Gráficas de `http_request_duration_seconds`, `db_operation_duration_seconds` y `db_pool_wait_seconds`.
- Conteos de `db_errors_total` y `transactions_total` durante el periodo.
- Estado de `ai_jobs` y `ai_oldest_pending_age_seconds` si la IA fue afectada.
- Salida sanitizada de [`scripts/incident_queries.sql`](../scripts/incident_queries.sql).
- `trace_id` representativos sin datos personales.
