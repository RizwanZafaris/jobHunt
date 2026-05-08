"""
Seed the user's curated target-company list. These are the companies the
pipeline will SCAN exclusively (no random web search).

Categories follow the user's brief.
Output: profile_build/output/target_companies.sql + .json
"""
from __future__ import annotations
import json
from pathlib import Path

OUT = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build/output")
OUT.mkdir(parents=True, exist_ok=True)


# (name, category, priority, careers_url_hint)
TARGETS: list[tuple[str, str, str, str]] = [
    # Card Networks / Payment Rails — Tier 1
    ("Visa", "Card Networks", "high", "https://corporate.visa.com/en/careers.html"),
    ("Mastercard", "Card Networks", "high", "https://careers.mastercard.com"),
    ("American Express", "Card Networks", "high", "https://careers.americanexpress.com"),
    ("Discover", "Card Networks", "medium", "https://www.discover.com/company/careers/"),
    ("UnionPay", "Card Networks", "low", "https://en.unionpayintl.com"),
    ("JCB", "Card Networks", "low", "https://www.global.jcb/en/about-us/recruit/"),

    # Payment Processors / Gateways — Tier 1 (closest to SimPaisa)
    ("Stripe", "Payment Processor", "high", "https://stripe.com/jobs"),
    ("Adyen", "Payment Processor", "high", "https://careers.adyen.com"),
    ("Checkout.com", "Payment Processor", "high", "https://www.checkout.com/careers"),
    ("PayPal", "Payment Processor", "high", "https://careers.pypl.com"),
    ("Braintree", "Payment Processor", "medium", "https://www.braintreepayments.com/careers"),
    ("Worldpay", "Payment Processor", "medium", "https://www.worldpay.com/careers"),
    ("Fiserv", "Payment Processor", "medium", "https://www.fiserv.com/en/about-fiserv/careers.html"),
    ("FIS Global", "Payment Processor", "medium", "https://www.fisglobal.com/en/careers"),
    ("Square (Block)", "Payment Processor", "high", "https://block.xyz/careers"),
    ("Razorpay", "Payment Processor", "medium", "https://razorpay.com/careers/"),
    ("PayU", "Payment Processor", "medium", "https://corporate.payu.com/careers/"),
    ("Verifone", "Payment Processor", "low", "https://www.verifone.com/en/careers"),
    ("2Checkout", "Payment Processor", "low", "https://www.2checkout.com/careers"),
    ("GoCardless", "Payment Processor", "medium", "https://gocardless.com/careers/"),
    ("Worldline", "Payment Processor", "medium", "https://worldline.com/en/home/careers.html"),

    # Cross-Border / Remittance — Tier 1 (highest fit)
    ("Wise", "Cross-Border / Remittance", "high", "https://wise.jobs"),
    ("Payoneer", "Cross-Border / Remittance", "high", "https://www.payoneer.com/careers/"),
    ("Airwallex", "Cross-Border / Remittance", "high", "https://www.airwallex.com/careers"),
    ("Nium", "Cross-Border / Remittance", "high", "https://www.nium.com/careers"),
    ("Rapyd", "Cross-Border / Remittance", "high", "https://www.rapyd.net/careers"),
    ("Remitly", "Cross-Border / Remittance", "high", "https://careers.remitly.com"),
    ("Western Union", "Cross-Border / Remittance", "medium", "https://careers.westernunion.com"),
    ("MoneyGram", "Cross-Border / Remittance", "medium", "https://corporate.moneygram.com/careers"),
    ("Currencycloud", "Cross-Border / Remittance", "medium", "https://www.currencycloud.com/careers/"),
    ("Thunes", "Cross-Border / Remittance", "high", "https://www.thunes.com/careers/"),
    ("TerraPay", "Cross-Border / Remittance", "high", "https://www.terrapay.com/careers"),
    ("dLocal", "Cross-Border / Remittance", "high", "https://dlocal.com/careers/"),

    # Banking-as-a-Service / Embedded Finance
    ("Marqeta", "BaaS / Embedded Finance", "high", "https://www.marqeta.com/about/careers"),
    ("Plaid", "BaaS / Embedded Finance", "high", "https://plaid.com/careers/"),
    ("Synapse Financial Technologies", "BaaS / Embedded Finance", "medium", "https://synapsefi.com/careers"),
    ("Solaris", "BaaS / Embedded Finance", "medium", "https://www.solarisgroup.com/en/career"),
    ("Unit", "BaaS / Embedded Finance", "medium", "https://www.unit.co/careers"),
    ("Treasury Prime", "BaaS / Embedded Finance", "medium", "https://www.treasuryprime.com/careers"),
    ("Lithic", "BaaS / Embedded Finance", "medium", "https://lithic.com/about/careers"),
    ("Modern Treasury", "BaaS / Embedded Finance", "medium", "https://www.moderntreasury.com/careers"),

    # Digital Banks / NeoBanks
    ("Revolut", "NeoBank", "high", "https://www.revolut.com/careers/"),
    ("N26", "NeoBank", "medium", "https://n26.com/en/careers"),
    ("Monzo", "NeoBank", "medium", "https://monzo.com/careers/"),
    ("Chime", "NeoBank", "medium", "https://www.chime.com/careers/"),
    ("Starling Bank", "NeoBank", "medium", "https://www.starlingbank.com/careers/"),
    ("Wio Bank", "NeoBank", "high", "https://www.wio.io/careers/"),
    ("Mashreq Neo", "NeoBank", "high", "https://www.mashreq.com/en/careers"),
    ("Jupiter Money", "NeoBank", "low", "https://jupiter.money/careers/"),

    # Enterprise Banking / Core Financial Infra
    ("Temenos", "Core Banking / Infra", "medium", "https://www.temenos.com/about-us/careers/"),
    ("Finastra", "Core Banking / Infra", "medium", "https://www.finastra.com/careers"),
    ("Mambu", "Core Banking / Infra", "medium", "https://mambu.com/careers"),
    ("Oracle Financial Services", "Core Banking / Infra", "low", "https://www.oracle.com/financial-services/"),

    # Big Banks competing digitally
    ("JPMorgan Chase", "Big Bank", "medium", "https://careers.jpmorgan.com"),
    ("Goldman Sachs", "Big Bank", "low", "https://www.goldmansachs.com/careers/"),
    ("Citibank", "Big Bank", "medium", "https://jobs.citi.com"),
    ("HSBC", "Big Bank", "medium", "https://www.hsbc.com/careers"),
    ("Standard Chartered", "Big Bank", "high", "https://scb.taleo.net/careersection/jobsearch.ftl"),
    ("DBS Bank", "Big Bank", "medium", "https://www.dbs.com/careers/default.page"),

    # MENA fintech (geographic alignment with Dubai)
    ("Tabby", "MENA Fintech", "high", "https://tabby.ai/careers"),
    ("Tamara", "MENA Fintech", "high", "https://tamara.co/en/careers"),
    ("STC Pay", "MENA Fintech", "high", "https://careers.stcpay.com.sa"),
    ("Careem Pay", "MENA Fintech", "high", "https://www.careem.com/en/about-us/careers/"),
    ("Lean Technologies", "MENA Fintech", "high", "https://www.leantech.me/careers"),
    ("Foodics", "MENA Fintech", "high", "https://www.foodics.com/careers"),
    ("Telr", "MENA Fintech", "medium", "https://www.telr.com/about/careers/"),
    ("Network International", "MENA Fintech", "high", "https://www.network.global/careers"),
    ("MagnatiPay", "MENA Fintech", "medium", "https://www.magnati.com/about/careers/"),
]


def gen_sql() -> str:
    parts = ["-- Target companies seed (run after targets_schema.sql)\n"]
    for (name, category, priority, careers_url) in TARGETS:
        n = name.replace("'", "''")
        c = category.replace("'", "''")
        p = priority.replace("'", "''")
        u = careers_url.replace("'", "''")
        parts.append(
            f"INSERT INTO companies (name, category, is_target, priority, careers_url, target_added_at) "
            f"VALUES ('{n}', '{c}', TRUE, '{p}', '{u}', NOW()) "
            f"ON CONFLICT (name) DO UPDATE SET "
            f"category = EXCLUDED.category, "
            f"is_target = TRUE, "
            f"priority = EXCLUDED.priority, "
            f"careers_url = EXCLUDED.careers_url, "
            f"target_added_at = COALESCE(companies.target_added_at, NOW());"
        )
    return "\n".join(parts)


def main():
    json_out = [
        {"name": n, "category": c, "priority": p, "careers_url": u}
        for n, c, p, u in TARGETS
    ]
    (OUT / "target_companies.json").write_text(json.dumps(json_out, indent=2))
    sql = gen_sql()
    (OUT / "target_companies.sql").write_text(sql)
    print(f"✅ Wrote {len(TARGETS)} target companies")
    print(f"   {OUT/'target_companies.json'}")
    print(f"   {OUT/'target_companies.sql'}")
    by_cat: dict[str, int] = {}
    for _, c, *_ in TARGETS:
        by_cat[c] = by_cat.get(c, 0) + 1
    print()
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"   {n:3d}  {cat}")


if __name__ == "__main__":
    main()
