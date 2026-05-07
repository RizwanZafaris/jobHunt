# Mode: evaluate
# Evaluate a job description against Rizwan's profile

## Purpose
Deep-evaluate a single JD for fit, extract key signals, and produce a structured report.

## Scoring Rubric (1-5 → A-F)

| Score | Grade | Meaning |
|-------|-------|---------|
| 85-100 | A+ | Dream role — drop everything, apply today |
| 75-84 | A | Excellent fit — fast-track application |
| 65-74 | B | Strong fit — tailor and apply |
| 50-64 | C | Decent fit — worth a tailored application |
| 40-49 | D | Partial fit — apply only if pipeline is dry |
| < 40 | F | Pass — move on |

## Evaluation Criteria

### Must-Have Signals (each worth 20 pts max)
1. **Domain match**: Fintech / Payments / Digital Banking / BNPL / Wallets
2. **Seniority match**: Head of Product / CPO / Senior PM / Technical PM / PMO
3. **Location fit**: UAE / KSA / Qatar / Bahrain / UK / Remote
4. **Experience level**: 10-15+ years acceptable, Rizwan has 14 years
5. **Compensation**: AED 35K/month minimum (or equivalent)

### Strong Signals (each worth 10 pts max)
- Enterprise integrations experience valued
- Programme governance / PMO skills required
- Agile / Scrum / SAFe methodology
- Cross-functional team leadership
- Stakeholder management at board level
- Certifications valued (PMP, PMI-ACP, CSPO, CSM)

### Bonus Signals (each worth 5 pts)
- Open to Pakistani national
- Relocation assistance offered
- Fast-growing company (Series A-D)
- Known brand / Fortune 500
- Mentions TikTok, Uber, or similar enterprise clients

### Red Flags (deduct points)
- Requires local UAE experience only (-15)
- Requires Arabic fluency (-20)
- Entry/mid-level role despite seniority title (-20)
- Pure engineering role (no PM/PMO component) (-30)
- Crypto-only without fintech context (-10)
- UAE Nationals preferred or required (-30)
- GCC Nationals preferred (-20)
- Role is junior/graduate programme despite company being Tier-1 (Mastercard, Visa, etc.) (-25)
- London/Amsterdam/NYC only role with no remote option and Rizwan not relocating (-15)

### Geography Intelligence Rules
- Mastercard/Visa Dubai posts: assume UAE Nationals preferred unless explicitly stated otherwise → apply -30
- Mastercard/Visa London/Amsterdam/Singapore posts: NO nationality penalty, score normally
- Adyen roles: only score >60 if role is in Dubai or explicitly remote-friendly
- Stripe roles: score based on product fit; note that interview bar is very high (add note, don't penalise)

## Output Format

```json
{
  "role_title": "...",
  "company": "...",
  "grade": "A|B|C|D|F",
  "score": 85,
  "domain_fit": "Payments/Fintech",
  "seniority_fit": "exact|above|below",
  "location_fit": "UAE|KSA|QA|UK|Remote",
  "strengths": ["...", "..."],
  "gaps": [
    {"area": "...", "severity": "critical|important|minor", "bridgeable": true}
  ],
  "key_requirements": ["..."],
  "missing_keywords": ["..."],
  "tailoring_priorities": ["..."],
  "company_hooks": ["..."],
  "red_flags": ["..."],
  "recommended_action": "apply_now|tailor_and_apply|pass",
  "cover_angle": "one sentence hook for cover email"
}
```
