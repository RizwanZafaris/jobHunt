"""
agents/sources/ — Job-discovery source adapters.

Each source returns a list of `DiscoveredJob` rows that the
job_validation pipeline can hydrate + score.

Adapters
--------
- firecrawl_source — JS-rendered enterprise career pages
  (Visa / Mastercard / Stripe / Adyen / Marqeta). Returns
  clean markdown + Pydantic-extracted JD fields.
"""
