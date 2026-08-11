import os
import plaid
from plaid.api import plaid_api
from plaid.model.products import Products
from plaid.model.country_code import CountryCode

# --- Config -----------------------------------------------------------
# Set these in your .env / environment:
#   PLAID_CLIENT_ID
#   PLAID_SECRET
#   PLAID_ENV        one of: sandbox | development | production (defaults to sandbox)

PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID", "")
PLAID_SECRET = os.environ.get("PLAID_SECRET", "")
PLAID_ENV = os.environ.get("PLAID_ENV", "sandbox")

_HOST_BY_ENV = {
    "sandbox": plaid.Environment.Sandbox,
    "development": plaid.Environment.Sandbox,  # legacy alias, Plaid merged dev into sandbox
    "production": plaid.Environment.Production,
}

configuration = plaid.Configuration(
    host=_HOST_BY_ENV.get(PLAID_ENV, plaid.Environment.Sandbox),
    api_key={
        "clientId": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
    },
)

api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

PLAID_PRODUCTS = [Products("transactions")]
PLAID_COUNTRY_CODES = [CountryCode("US")]
