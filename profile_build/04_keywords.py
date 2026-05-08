"""
Step 4: Build categorized keyword bank from all extracted resumes.
Uses a curated vocabulary mapped to the 11 categories the user requested.
For each keyword: count occurrences, count distinct resumes, compute ATS strength.
Output: profile_build/output/keywords.json
"""
from __future__ import annotations
import json
import re
from collections import defaultdict
from pathlib import Path

BUILD = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build")
OUT = BUILD / "output"

# Vocabulary: keyword → category
# Match patterns: each entry can be a simple string or a regex pattern (start with /)
VOCAB: dict[str, list[str]] = {
    "Product Management": [
        "product management", "product manager", "product owner", "product strategy",
        "product roadmap", "product roadmap", "product vision", "product lifecycle",
        "product discovery", "product launch", "product market fit", "product-market fit",
        "go-to-market", "GTM", "product portfolio", "feature prioritization",
        "user research", "user stories", "product analytics", "product KPI",
        "north star metric", "OKR", "MVP", "product requirements", "PRD",
        "stakeholder management", "customer journey", "user journey",
        "value proposition", "voice of customer", "product positioning",
        "competitive analysis", "feature flag", "A/B testing", "ab testing",
        "product backlog", "product hypothesis", "discovery to delivery",
    ],
    "Fintech": [
        "fintech", "financial services", "digital banking", "neobank",
        "embedded finance", "open banking", "BNPL", "buy now pay later",
        "lending", "credit", "wealth management", "insurance tech",
        "regtech", "wealthtech", "financial inclusion", "challenger bank",
        "stablecoin", "agentic commerce", "financial infrastructure",
    ],
    "Payments": [
        "payment", "payments", "card", "cards", "issuing", "issuer",
        "acquiring", "acquirer", "merchant", "merchants", "card-on-file",
        "tokenization", "tokenized", "MDES", "VTS", "DPAN", "FPAN",
        "ISO 8583", "ISO 20022", "SWIFT", "MPGS", "3DS", "3DS2",
        "EMV", "PCI DSS", "settlement", "reconciliation", "chargeback",
        "interchange", "scheme", "Visa", "Mastercard", "AMEX", "JCB",
        "Discover", "RuPay", "wallet", "mobile wallet", "digital wallet",
        "POS", "checkout", "authorization", "authentication", "decline code",
        "approval rate", "authorization rate", "auth rate", "GTV", "TPV",
        "transaction volume", "remittance", "cross-border", "corridor",
        "FX", "foreign exchange", "real-time payments", "RTP", "instant payment",
        "ACH", "BACS", "SEPA", "wire transfer", "card network", "scheme rules",
        "smart retry", "retry logic", "decline recovery", "fraud",
        "payment gateway", "payment service provider", "PSP", "orchestration",
        "card-not-present", "card not present", "CNP", "POS", "BIN sponsor",
        "principal member", "issuer enablement", "issuer acceptance",
        "scheme certification", "scheme tokenization",
    ],
    "Compliance": [
        "compliance", "regulatory", "regulation", "PCI DSS", "SOC 2",
        "ISO 27001", "ISO 9001", "GDPR", "AML", "anti-money laundering",
        "CFT", "KYC", "KYB", "sanctions screening", "PEP", "OFAC",
        "FATF", "MAS", "FCA", "SAMA", "CBUAE", "VARA",
        "FinCEN", "central bank", "audit", "auditor", "audit trail",
        "regulatory reporting", "MSB", "money services business",
        "license", "licensing", "regulatory alignment",
    ],
    "Agile": [
        "agile", "scrum", "kanban", "sprint", "sprint planning",
        "backlog refinement", "retrospective", "stand-up", "standup",
        "user story", "epic", "story points", "velocity", "burndown",
        "lean", "SAFe", "scaled agile", "scrum master", "CSM", "CSPO",
        "ACP", "PSM", "PSPO", "agile coach", "agile transformation",
    ],
    "PMO": [
        "PMO", "project management office", "PMP", "PMBOK", "PRINCE2",
        "stage gate", "project charter", "RAID", "risk register",
        "earned value", "WBS", "work breakdown structure", "Gantt",
        "MS project", "milestone", "deliverable", "scope management",
        "schedule management", "resource management", "RAID log",
        "change control", "project governance", "steering committee",
        "program management", "portfolio management",
    ],
    "Technical": [
        "REST", "RESTful", "API", "APIs", "GraphQL", "gRPC", "webhook",
        "microservice", "microservices", "monolith", "SDK", "iOS SDK",
        "Android SDK", "JavaScript", "TypeScript", "Python", "Java",
        "Node", "Node.js", "React", "Next.js", "Postgres", "PostgreSQL",
        "MongoDB", "Redis", "Kafka", "RabbitMQ", "AWS", "GCP", "Azure",
        "Kubernetes", "Docker", "CI/CD", "GitHub Actions", "Jenkins",
        "OAuth", "JWT", "SAML", "TLS", "SSL", "encryption", "hashing",
        "Jira", "Confluence", "Postman", "Swagger", "OpenAPI",
        "system design", "distributed systems", "event-driven",
        "event driven", "data pipeline", "ETL",
    ],
    "Leadership": [
        "leader", "leadership", "head of", "VP", "vice president",
        "director", "chief", "CPO", "CEO", "CTO", "CFO", "COO", "CMO",
        "executive", "founding", "founder", "co-founder",
        "team leadership", "team building", "people management",
        "1:1", "performance management", "talent development",
        "mentor", "mentoring", "coaching", "succession planning",
        "hiring", "recruiting", "growing teams", "scaled organization",
        "C-suite", "board", "board reporting", "executive communication",
        "executive presence", "stakeholder influence",
    ],
    "Analytics": [
        "analytics", "data analytics", "business intelligence", "BI",
        "Tableau", "Looker", "Power BI", "PowerBI", "Metabase",
        "Mixpanel", "Amplitude", "Heap", "Snowflake", "BigQuery",
        "Redshift", "dbt", "ETL", "data warehouse", "data lake",
        "SQL", "Python", "R", "pandas", "matplotlib", "Jupyter",
        "cohort analysis", "funnel analysis", "retention analysis",
        "churn", "LTV", "CAC", "DAU", "MAU", "WAU", "north star",
        "dashboard", "KPI tracking", "experimentation", "A/B testing",
        "statistical significance", "predictive analytics",
    ],
    "AI": [
        "AI", "artificial intelligence", "ML", "machine learning",
        "deep learning", "neural network", "LLM", "large language model",
        "GenAI", "generative AI", "RAG", "retrieval augmented generation",
        "retrieval-augmented", "vector database", "vector db", "embedding",
        "embeddings", "fine-tuning", "fine tuning", "prompt engineering",
        "OpenAI", "GPT-4", "GPT-3.5", "Claude", "Anthropic", "Gemini",
        "LangChain", "LlamaIndex", "MLOps", "AI strategy", "AI consulting",
        "AI agent", "agentic", "computer vision", "NLP", "natural language",
        "speech recognition", "recommendation engine", "model deployment",
    ],
    "Risk & Security": [
        "risk management", "risk mitigation", "operational risk",
        "credit risk", "fraud", "fraud prevention", "fraud detection",
        "transaction monitoring", "cybersecurity", "infosec",
        "information security", "security architecture", "threat modeling",
        "penetration testing", "pen testing", "vulnerability", "incident response",
        "SOC", "security operations center", "encryption", "tokenization",
        "data protection", "privacy", "PII", "GDPR", "DLP",
        "zero trust", "OWASP", "secure coding", "SAST", "DAST",
    ],
}

# Build inverted lookup
KEYWORD_CATEGORY: dict[str, str] = {}
for cat, kws in VOCAB.items():
    for kw in kws:
        KEYWORD_CATEGORY.setdefault(kw.lower(), cat)


def normalize(text: str) -> str:
    return text.lower()


def find_keyword(text_norm: str, kw: str) -> int:
    """Count occurrences of a keyword phrase, with word boundaries."""
    kw_l = kw.lower()
    # For multi-word phrases, plain substring with boundaries
    pattern = r"(?<![a-zA-Z0-9])" + re.escape(kw_l) + r"(?![a-zA-Z0-9])"
    return len(re.findall(pattern, text_norm))


def main():
    extracted = json.loads((OUT / "extracted.json").read_text())
    # Restrict to resume-type files for keyword corpus
    RESUME_CLASSES = {"role_specific_resume", "executive_resume", "master_cv",
                      "linkedin_optimization", "linkedin_headline_about",
                      "linkedin_audit", "company_assessment",
                      "application_answer", "interview_prep", "cover_letter"}

    resume_files = {h: meta for h, meta in extracted.items() if meta["class"] in RESUME_CLASSES}
    print(f"Scanning {len(resume_files)} files for keywords...")

    # For each keyword: total occurrences + set of file hashes containing it
    counts: dict[str, int] = defaultdict(int)
    files_with: dict[str, set] = defaultdict(set)
    file_class_with: dict[str, set] = defaultdict(set)

    for h, meta in resume_files.items():
        text = Path(meta["text_path"]).read_text(errors="replace")
        text_norm = normalize(text)
        for kw in KEYWORD_CATEGORY:
            n = find_keyword(text_norm, kw)
            if n > 0:
                counts[kw] += n
                files_with[kw].add(h)
                file_class_with[kw].add(meta["class"])

    total_files = len(resume_files)
    keywords_out: list[dict] = []
    for kw, total in counts.items():
        cat = KEYWORD_CATEGORY[kw]
        files_count = len(files_with[kw])
        coverage = files_count / max(total_files, 1)
        # ATS strength = log-ish score: combines breadth (coverage) + depth (avg occurrences per file)
        avg_per_file = total / max(files_count, 1)
        strength = round(coverage * 60 + min(avg_per_file, 10) * 4, 1)  # 0-100ish
        keywords_out.append({
            "keyword": kw,
            "category": cat,
            "total_occurrences": total,
            "files_count": files_count,
            "coverage_pct": round(coverage * 100, 1),
            "avg_per_file": round(avg_per_file, 2),
            "ats_strength": strength,
        })

    keywords_out.sort(key=lambda k: (-k["ats_strength"], -k["total_occurrences"]))

    # Category summaries
    cat_summary: dict[str, dict] = {}
    for cat in VOCAB:
        items = [k for k in keywords_out if k["category"] == cat]
        if not items:
            continue
        cat_summary[cat] = {
            "category": cat,
            "keyword_count": len(items),
            "top_keywords": [k["keyword"] for k in items[:10]],
            "avg_strength": round(sum(k["ats_strength"] for k in items) / len(items), 1),
            "total_occurrences": sum(k["total_occurrences"] for k in items),
        }

    # Find missing/weak — keywords in vocab with zero hits
    all_vocab = set(KEYWORD_CATEGORY.keys())
    found = set(counts.keys())
    missing = sorted(all_vocab - found)

    out = {
        "scanned_files": total_files,
        "total_keywords_found": len(keywords_out),
        "keywords": keywords_out,
        "by_category": cat_summary,
        "missing_from_vocabulary": missing,  # potential gaps
        "vocabulary_size": len(all_vocab),
    }
    (OUT / "keywords.json").write_text(json.dumps(out, indent=2))
    print(f"\n✅ Wrote {OUT/'keywords.json'}")
    print(f"   Files scanned: {total_files}")
    print(f"   Keywords found: {len(keywords_out)} / {len(all_vocab)} in vocab")
    print(f"   Missing: {len(missing)}")
    print("\n   Top 15 by ATS strength:")
    for k in keywords_out[:15]:
        print(f"     {k['ats_strength']:5.1f}  {k['category']:20s} {k['keyword']:30s}  files={k['files_count']:3d}  occ={k['total_occurrences']}")
    print("\n   Category summary:")
    for cat, s in sorted(cat_summary.items(), key=lambda x: -x[1]["total_occurrences"]):
        print(f"     {cat:20s}  {s['keyword_count']:3d} kws  total={s['total_occurrences']:5d}  avg_strength={s['avg_strength']}")


if __name__ == "__main__":
    main()
