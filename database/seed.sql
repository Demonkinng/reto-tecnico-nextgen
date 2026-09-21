-- SmartBancs App — Datos de prueba

INSERT INTO accounts (
    account_id,
    owner_name,
    balance,
    currency
)
VALUES
    (
        '00000000-0000-4000-8000-000000000001',
        'Angel',
        100000,
        'USD'
    ),
    (
        '00000000-0000-4000-8000-000000000002',
        'David',
        50000,
        'USD'
    ),
    (
        '00000000-0000-4000-8000-000000000003',
        'Esteban',
        0,
        'USD'
    )
ON CONFLICT (account_id) DO NOTHING;