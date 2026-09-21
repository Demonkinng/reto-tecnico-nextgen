from .schemas import RecommendationResponse

RULE_VERSION = "mock-v1"


def recommend(transaction):
    # Umbrales de demostración, no criterios bancarios ni un modelo entrenado.
    if transaction.amount < 10000:
        text = "Registra este movimiento para mantener actualizado tu seguimiento de gastos."
    elif transaction.amount < 50000:
        text = "Revisa cómo encaja este movimiento en tu presupuesto del mes."
    else:
        text = "Antes de nuevos movimientos, revisa tus gastos previstos y compromisos pendientes."
    return RecommendationResponse(transaction_id=transaction.transaction_id, recommendation=text)
