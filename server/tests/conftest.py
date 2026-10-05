import os

# Tests never touch the live DB (data/claros.db): set before claros loads .env, which doesn't override.
os.environ["CLAROS_DB"] = ":memory:"
