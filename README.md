# SmartBancs: microservicio de transacciones

MVP local en Python 3.13, FastAPI y PostgreSQL. Permite transferencias en **centavos de USD**, evita dobles débitos y guarda una tarea de IA junto con cada transferencia. El worker consume el servicio de IA en un proceso separado.

Implementa el microservicio transaccional, su worker, un microservicio independiente de recomendaciones simuladas y un ETL reproducible con datos ficticios.

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

Sin worker, los trabajos permanecen `pending`; las transferencias se completan normalmente. Para levantar también el worker y el mock funcional de IA:

```powershell
docker compose --profile ia up --build -d
```

El perfil `ia` incluye `ms-inference-ai`; no necesita API key ni configurar otra URL. Su [Swagger](http://localhost:8002/docs) y métricas están en el puerto `8002`. La API transaccional sigue en `8000`. Si previamente configuraste `AI_SERVICE_URL` para otro servidor, elimina esa sobrescritura para usar el mock incluido. Para un servicio compatible fuera de Docker puedes usar `http://host.docker.internal:8002/recommendations`.

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
  "recommendation": "Registra este movimiento para mantener actualizado tu seguimiento de gastos.",
  "mode": "mock"
}
```

El servicio incluido siempre devuelve `mode: "mock"`: usa tres reglas deterministas según el monto y no llama a un modelo entrenado. El worker también admite `mode: "api"` para la integración futura, que deberá gestionar su propia clave Gemini/OpenAI. No se comparten secretos del proveedor con transacciones. El monto por sí solo no permite una personalización financiera completa. Ver el [diseño y ciclo de vida teórico](docs/design-document.md#6-inteligencia-artificial-implementación-y-despliegue).

Se permiten tres intentos totales. Timeout, errores de red, `429` y `5xx` se reintentan con esperas de 2 y 4 segundos; los demás errores HTTP y respuestas inválidas se marcan como fallidos. Una reserva vence a los 30 segundos para recuperar procesos interrumpidos. Las llamadas HTTP usan timeout de 5 segundos por fase de red. La entrega es **al menos una vez**: el futuro servicio IA debe deduplicar por `transaction_id` para evitar costes repetidos. Un fallo de IA no revierte dinero ya transferido.

## Observabilidad

```powershell
docker compose --profile observability up -d
docker compose logs -f ms_transaction
```

[Prometheus](http://localhost:9090) recoge volumen, errores, duración HTTP/SQL, espera del pool y métricas de IA. Incluye reglas de latencia, errores, contención y retraso de tareas. Las alertas se consultan en Prometheus; no se ha configurado envío de notificaciones. Los targets del worker y del mock aparecen `DOWN` si el perfil `ia` está apagado: no tienen alerta de caída. Para levantar todo, usar `docker compose --profile ia --profile observability up --build -d`.

Las operaciones de negocio se registran en JSON con `trace_id`, evento y servicio. Las operaciones SQL lentas/fallidas incluyen nombre de operación y PID de PostgreSQL; los errores SQL incluyen `sqlstate`. No se registran contraseñas, nombres, cuerpos de petición ni recomendaciones. `X-Trace-ID` permite correlacionar API, base y worker; no es todavía una traza distribuida OpenTelemetry con spans. Ver la [guía de diagnóstico y escalamiento](docs/incident-response.md).

## ETL de datos ficticios

El ejemplo transforma un lote exportado por Bancs, normaliza centavos, fechas y categorías, pseudonimiza las cuentas y separa filas rechazadas:

```powershell
python etl/transform.py --input etl/input/raw_transactions.csv --output-dir etl/output
python -m unittest discover -s etl/tests -v
```

Las reglas, códigos de rechazo y límites de privacidad están en la [guía del ETL](etl/README.md). `etl/expected/` contiene la salida exacta que debe producir el lote incluido.

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

Pruebas del servicio simulado, sin base de datos:

```powershell
.venv/Scripts/python.exe -m pip install -r ms-inference-ai/requirements-dev.txt
.venv/Scripts/python.exe -m pytest -c ms-inference-ai/pytest.ini ms-inference-ai/tests -q
```

Ejecutar las suites de cada microservicio por separado: ambos tienen su propio paquete `app`. Para verificar los componentes con HTTP real y base desechable, después de terminar las suites:

```powershell
docker compose -f compose.test.yml --profile pipeline up --build -d --wait
$env:TEST_DATABASE_URL = "postgresql://postgres:local_test_only@127.0.0.1:55432/smartbancs_test"
$env:TEST_API_URL = "http://127.0.0.1:58000"
.venv/Scripts/python.exe scripts/verify_pipeline.py
docker compose -f compose.test.yml --profile pipeline down
```

El script crea una transferencia de un centavo en el entorno de prueba, verifica que el reintento no duplique el débito y espera la recomendación guardada. No ejecutarlo contra la API de desarrollo en `8000`. No ejecutar la suite transaccional mientras el worker de pruebas está activo: las pruebas vacían sus tablas. Para puertos de prueba personalizados, definir `TEST_DB_PORT`, `TEST_API_PORT` y `TEST_AI_PORT` antes de levantar el entorno; el script exige que las URLs coincidan con los puertos configurados.

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
ms-inference-ai/    # Mock independiente: app, tests y Dockerfile
scripts/           # Verificación del recorrido completo en el entorno desechable
etl/               # Transformación práctica, lote ficticio, pruebas y resultados esperados
observability/     # Configuración y alertas Prometheus
docs/              # Justificación, matriz de cumplimiento y guía operativa
evidence/          # Guion de demo, exposición, datos y checklist de entrega
```

Es una organización pequeña por responsabilidades, cercana a una Minimal API: no agrega repositorios genéricos, interfaces ni arquitectura hexagonal. Ver [decisiones técnicas](docs/design-document.md).
