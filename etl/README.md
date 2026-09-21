# ETL de transacciones

Este proceso transforma un archivo CSV ficticio exportado por Bancs en un conjunto pequeño y estable para análisis.

## Ejecutar

Desde la raíz del proyecto:

```powershell
python etl/transform.py --input etl/input/raw_transactions.csv --output-dir etl/output
```

El comando devuelve código `0` si el lote pudo procesarse, aunque existan filas rechazadas. Devuelve código `1` cuando el archivo no puede leerse, faltan columnas obligatorias o no se pueden escribir las salidas.

## Reglas

| Campo | Transformación |
|---|---|
| `transaction_id` | Debe ser UUID y no repetirse dentro del lote. |
| IDs de cuenta | Deben ser UUID. La salida expone un SHA-256 truncado a 16 caracteres. |
| `amount` | Debe ser positivo y tener máximo dos decimales; se convierte exactamente a centavos. |
| `currency` | Se convierte a mayúsculas. Vacío usa `USD`; otra moneda se rechaza. |
| `occurred_at` | Acepta ISO 8601 y `YYYY-MM-DD HH:MM:SS`; una fecha sin zona se interpreta como UTC. |
| `category` | Se recorta y convierte a minúsculas; vacío usa `uncategorized`. |

Las filas válidas se ordenan por fecha UTC e ID. Los rechazos registran el número de fila, el ID disponible y uno de estos códigos: `missing_required`, `invalid_uuid`, `invalid_amount`, `unsupported_currency`, `invalid_timestamp` o `duplicate_transaction_id`.

Los archivos se escriben primero con un nombre temporal y después reemplazan la salida final. Esto evita publicar un archivo incompleto si la escritura falla. El resumen no contiene identificadores de cuenta.

## Privacidad

Los UUID del ejemplo son ficticios. El hash truncado es una **pseudonimización para la demostración**: evita exponer el UUID directamente, pero no equivale a anonimización irreversible ni sustituye la tokenización administrada que requeriría un banco.

## Pruebas

```powershell
python -m unittest discover -s etl/tests -v
```

`etl/expected/` contiene la salida exacta del lote de ejemplo para facilitar su revisión.
