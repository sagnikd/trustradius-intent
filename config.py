"""
ICP + competitor classification rules for the TrustRadius intent dashboard.
Edit this file to adjust targeting/scoring — no need to touch build_dashboard.py.
"""

# ---- ICP hard filter -------------------------------------------------------
# Account Industry values as they literally appear in the TrustRadius export.
ICP_INDUSTRIES = {
    "Banking and Financial Services",  # closest match to "Banking" — TrustRadius doesn't split banking from broader financial services
    "Insurance",
    "Retail Trade",
    "Telecommunications",
}
MIN_EMPLOYEES = 1000       # qualifies if Account Size lower bound >= this...
MIN_REVENUE = 500_000_000  # ...OR Account Annual Revenue >= this (either qualifies)
# Geography: global — no country filter applied.

# ---- Competitor / own-product classification ------------------------------
# Matched against (Object N Vendor, Object N Name) for each of the up-to-3
# objects on a row. `products=None` means "any product under this vendor
# counts" (used for vendors not yet seen in the data, so they're picked up
# automatically the first time they appear).
RULES = [
    {"bucket": "HCL Unica (Own)", "vendor": "HCLSoftware", "products": {"HCL Unica"}, "own": True},

    {"bucket": "Adobe Experience Cloud", "vendor": "Adobe", "products": {
        "Adobe Campaign",
        "Adobe Marketo Engage",
        "Adobe Journey Optimizer",
        "Adobe Marketing Cloud",
        "Adobe Audience Manager",
        "Adobe Real-Time Customer Data Platform",
        "Adobe Target",
        "Adobe Customer Journey Analytics",
        "Adobe Customer Journey Analytics B2B Edition",
        "Adobe GenStudio for Performance Marketing",
    }},
    {"bucket": "Salesforce Marketing Cloud", "vendor": "Salesforce", "products": {
        "Salesforce Agentforce Marketing",
    }},
    {"bucket": "Oracle Marketing Cloud", "vendor": "Oracle", "products": {
        "Oracle Marketing",
    }},
    {"bucket": "Acoustic Campaign", "vendor": "Acoustic", "products": {
        "Acoustic Campaign",
    }},
    {"bucket": "Braze", "vendor": "Braze", "products": {
        "Braze",
    }},
    {"bucket": "Zeta Global (incl. Selligent, Cheetah Digital)", "vendor": "Zeta Global", "products": {
        "Cheetah Digital",
        "Loyalty by Zeta",
        "Sailthru",
        "Selligent",
        "Zeta Marketing Platform",
    }},
    {"bucket": "Bloomreach", "vendor": "Bloomreach", "products": {
        "Bloomreach", "",
    }},
    {"bucket": "Actito", "vendor": "Actito", "products": {
        "Actito",
        "SmartFocus, now part of Actito",
    }},
    {"bucket": "Pega Customer Decision Hub", "vendor": "Pegasystems", "products": {
        "Pega Customer Decision Hub",
    }},
    {"bucket": "Gainsight PX", "vendor": "Gainsight", "products": {
        "Gainsight PX",
    }},
    {"bucket": "SAS Marketing Automation", "vendor": "SAS", "products": {
        "SAS 360 Discover",
        "SAS 360 Engage",
        "SAS 360 Plan",
    }},

    # Not seen in the data yet as of 2026-08-26 — vendor-level fallback so
    # they're captured automatically the first time they show up.
    {"bucket": "Emarsys", "vendor": "Emarsys", "products": None},
    {"bucket": "Evam", "vendor": "Evam", "products": None},
    {"bucket": "Knowesis", "vendor": "Knowesis", "products": None},
]

# Vendors explicitly excluded despite being under a tracked parent company —
# kept here just as documentation of a deliberate decision, not used by code.
EXCLUDED_PRODUCTS_NOTE = """
Excluded on purpose (confirmed 2026-08-26):
- Adobe: Experience Manager, Experience Platform, Commerce(+Intelligence),
  PhotoShop, Captivate, Workfront, Advertising Cloud, Analytics — not
  campaign/marketing-automation products.
- Salesforce: everything except Agentforce Marketing (Sales, Service, Slack,
  HR, CPQ, etc. are not Unica competitors).
- Oracle: everything except Oracle Marketing (CRM, ERP, Retail, etc. excluded).
- Pegasystems: Customer Service (helpdesk) excluded, only CDH counted.
- Gainsight: CS (customer success) excluded, only PX counted.
- SAS: Viya (general stats platform) excluded.
- "AdBraze" is an unrelated company name-collision with Braze — excluded.
- Selligent is now sold by Zeta Global (post-acquisition) — merged into the
  Zeta Global bucket rather than kept separate.
"""

# ---- Activity → intent weight ---------------------------------------------
# "Activity Type" in the raw data is just view/click (not useful). The real
# signal is "Activity Label". Weights below are a starting judgment call —
# adjust freely.
LABEL_WEIGHTS = {
    "Product Pricing": 5,     # evaluating cost = strong buying signal
    "Comparison": 5,          # actively comparing vendors
    "Product Paid CTA": 5,    # clicked a paid lead-gen CTA
    "Competitors": 4,         # viewing a competitors page
    "Product Scorecard": 3,
    "Reviews and Ratings": 2,
    "Review": 2,
    "Product Details": 2,
    "Product Listing": 1,
    "Category": 1,
    "Article": 1,
}
DEFAULT_LABEL_WEIGHT = 1

# Multiplier applied on top of the label weight, per user instruction that
# research on HCL Unica itself should score higher than competitor research.
OWN_PRODUCT_MULTIPLIER = 3
COMPETITOR_MULTIPLIER = 1

# Rolling window (days) for "current" scoring and for the prior-period
# comparison used to compute trend.
ROLLING_WINDOW_DAYS = 30
