# SmartBancs: microservicio de transacciones

MVP local en Python 3.13, FastAPI y PostgreSQL. Permite transferencias en **centavos de USD**, evita dobles débitos y guarda una tarea de IA junto con cada transferencia. El worker consume el servicio de IA en un proceso separado.

Este incremento implementa el microservicio transaccional y su consumidor HTTP de IA. El microservicio independiente de IA, el proveedor real, el ETL y la integración Bancs siguen pendientes. No se afirma capacidad de 10 000 transacciones/s: requiere pruebas de carga y dimensionamiento.

## Ejecutar

Prerrequisito: Docker Desktop con contenedores Linux y Docker Compose v2. Desde la raíz del proyecto:

1. Si aún no existe `.env`, copiar `.env.example` a `.env` y definir `POSTGRES_PASSWORD`. Si ya tienes la base inicializada, conservar su contraseña: cambiar la variable no cambia la contraseña del volumen existente.
2. Levantar backend y base:

```powershell
docker compose up --build -d
```

Abrir [Swagger](http://localhost:8000/docs). Estado del proceso: `GET /health`; disponibilidad de base: `GET /ready`; métricas: `GET /metrics`. Los puertos de desarrollo sólo se publican en `127.0.0.1`.

El esquema y el seed se ejecutan **solamente al inicializar un volumen vacío**. No vuelven a ejecutarse al reiniciar los contenedores. Los cambios posteriores de esquema requieren una migración explícita.

## Probar una transferencia

Las cuentas ficticias iniciales terminan en `001` (USD 1000), `002` (USD 500) y `003` (USD 0). Este ejemplo envía USD 25 de la primera a la segunda:

```powershell
$headers = @{ "Idempotency-Key" = "demo-transferencia-001"; "X-Trace-ID" = "demo-001" }
$body = @{
  source_account_id = "00000000-0000-4000-8000-000000000001"
  destination_account_id = "00000000-0000-4000-8000-000000000002"
  amount = 2500
  currency = "USD"
} | ConvertTo-Json

$transaction = Invoke-RestMethod -Method Post -Uri http://localhost:8000/transactions -Headers $headers -ContentType application/json -Body $body
$transaction
Invoke-RestMethod "http://localhost:8000/accounts/00000000-0000-4000-8000-000000000001"
Invoke-RestMethod "http://localhost:8000/transactions/$($transaction.transaction_id)/recommendation"
```

La primera petición devuelve `201`; repetir exactamente los mismos datos y clave devuelve `200` y la misma transferencia, sin descontar otra vez. Usar otra clave representa **otra transferencia**. Reutilizar la clave con otros datos devuelve `409`. El saldo inicial cambia al ejecutar este ejemplo.

| Ruta | Uso |
|---|---|
| `POST /transactions` | Crear transferencia con `Idempotency-Key` obligatoria |
| `GET /transactions/{transaction_id}` | Consultar una transferencia |
| `GET /accounts/{account_id}` | Consultar saldo en centavos |
| `GET /transactions/{transaction_id}/recommendation` | Consultar estado, intentos y resultado de IA |

Validación: `422`; cuenta o recurso inexistente: `404`; saldo insuficiente/conflicto: `409`; base temporalmente ocupada: `503`; fallo interno: `500`. Ante timeout o error de comunicación, reintentar **con la misma clave y cuerpo**, porque el cliente puede no haber recibido la respuesta de una operación ya confirmada.

## Worker e IA

Sin worker, los trabajos permanecen `pending`; las transferencias se completan normalmente. Cuando exista el servicio independiente, configurar `AI_SERVICE_URL` en `.env` con la URL completa del endpoint e iniciar:

```powershell
docker compose --profile ia up --build -d
```

Si IA corre en Windows fuera de Docker, utilizar por ejemplo `http://host.docker.internal:8002/recommendations`. El valor predeterminado apunta al futuro servicio `ms-inference-ai`; **no activar el perfil antes de tener ese endpoint**. El worker agotaría los intentos ante una URL inaccesible.

Contrato HTTP esperado, con cabecera `X-Trace-ID` y `POST`:

```json
{
  "transaction_id": "0e3d735e-9856-40a5-8ba6-cb9769e4dbe8",
  "amount": 2500,
  "currency": "USD"
}
```

Respuesta `200` del servicio de IA:

```json
{
  "transaction_id": "0e3d735e-9856-40a5-8ba6-cb9769e4dbe8",
  "recommendation": "Texto producido por el servicio de IA.",
  "mode": "api"
}
```

También se acepta `mode: "mock"` para una simulación explícita. La API transaccional no fabrica recomendaciones. El futuro servicio de IA debe manejar su propia clave Gemini/OpenAI y validar este contrato; no compartir credenciales del proveedor con el servicio de transacciones. El payload actual permite demostrar el flujo, pero no ofrece aún el contexto necesario para una personalización financiera completa.

Se permiten tres intentos totales. Timeout, errores de red, `429` y `5xx` se reintentan con esperas de 2 y 4 segundos; los demás errores HTTP y respuestas inválidas se marcan como fallidos. Una reserva vence a los 30 segundos para recuperar procesos interrumpidos. Las llamadas HTTP usan timeout de 5 segundos por fase de red. La entrega es **al menos una vez**: el futuro servicio IA debe deduplicar por `transaction_id` para evitar costes repetidos. Un fallo de IA no revierte dinero ya transferido.

## Observabilidad

```powershell
docker compose --profile observability up -d
docker compose logs -f ms_transaction
```

[Prometheus](http://localhost:9090) recoge volumen, errores, duración HTTP/SQL, espera del pool y métricas de IA. Incluye reglas de latencia, errores, contención y retraso de tareas. Las alertas se consultan en Prometheus; no se ha configurado envío de notificaciones. El target del worker aparece `DOWN` si el perfil `ia` está apagado: no tiene alerta de caída en este incremento.

Las operaciones de negocio se registran en JSON con `trace_id`, evento y servicio. Las operaciones SQL lentas/fallidas incluyen nombre de operación y PID de PostgreSQL; los errores SQL incluyen `sqlstate`. No se registran contraseñas, nombres, cuerpos de petición ni recomendaciones. `X-Trace-ID` permite correlacionar API, base y worker; no es todavía una traza distribuida OpenTelemetry con spans. Ver [guía de diagnóstico](docs/observability.md).

## Pruebas automatizadas

Requieren Python 3.13 y Docker. La base de pruebas es independiente, temporal y escucha en `55432`. **Las pruebas vacían sus tablas antes de cada caso**; usar exclusivamente esta base desechable. La configuración rechaza nombres de base que no terminen en `_test`.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r ms-transaction/requirements-dev.txt
docker compose -f compose.test.yml up -d --wait
$env:TEST_DATABASE_URL = "postgresql://postgres:local_test_only@127.0.0.1:55432/smartbancs_test"
.venv/Scripts/python.exe -m pytest -c ms-transaction/pytest.ini ms-transaction/tests -q
docker compose -f compose.test.yml down
```

Incluyen dinero atómico, rollback, validación, duplicados simultáneos, transferencias en sentidos opuestos, bloqueo SQL, respuestas sanitizadas, reintentos y reservas de IA. Se utiliza PostgreSQL real y un servidor HTTP controlado en las pruebas; no se llama a Gemini/OpenAI. No son un benchmark de carga ni prueban integración con un proveedor real.

## Detener

```powershell
docker compose --profile ia --profile observability down
```

Los volúmenes de desarrollo se conservan. No añadir `-v` si necesitas mantener los datos.

## Organización y decisiones

```text
ms-transaction/
  app/
    main.py        # Rutas, validación HTTP, errores y ciclo de vida
    schemas.py     # Contratos Pydantic y enums
    service.py     # Reglas de transferencia y SQL del negocio
    database.py    # Pool, consultas instrumentadas y conexiones
    config.py      # Configuración por variables de entorno
    telemetry.py   # Logs JSON y métricas Prometheus
    worker.py      # Consumo de ai_jobs y llamadas HTTP a IA
  tests/
  Dockerfile
database/          # DDL y datos ficticios existentes
observability/     # Configuración y alertas Prometheus
docs/              # Justificación y guía operativa
```

Es una organización pequeña por responsabilidades, cercana a una Minimal API: no agrega repositorios genéricos, interfaces ni arquitectura hexagonal. Ver [decisiones técnicas](docs/design-document.md).

Limitaciones del entorno local: no hay autenticación/autorización, TLS ni roles de base con privilegio mínimo; Compose reutiliza el usuario local de PostgreSQL. Esto permite demostrar el flujo con datos ficticios, pero requiere resolver esos controles antes de exponerlo fuera de la máquina. También quedan pendientes prueba de carga, Bancs/ETL, servicio de IA, ciclo de vida del modelo y entregables de presentación del reto.
