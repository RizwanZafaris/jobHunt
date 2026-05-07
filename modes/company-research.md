# Mode: company-research
# Build deep company knowledge for a target company

## Purpose
Research a company thoroughly so the CompanyAgent becomes an expert reviewer.
Knowledge is stored in Supabase and stays fresh for 24 hours.

## Research Sections

### 1. overview
- Company description, founding year, HQ, stage (startup/scaleup/enterprise)
- Core product/service
- Business model (B2C/B2B/B2B2C)
- Number of employees (approx)
- Key markets served

### 2. news
- Latest funding rounds (last 12 months)
- Leadership changes (CEO, CPO, CTO hires/departures)
- Product launches
- Acquisitions / partnerships
- Press coverage (notable stories)

### 3. funding
- Total funding raised
- Latest round amount and date
- Lead investors
- Valuation if known
- Stage: Seed / Series A / B / C / D / Pre-IPO / Public

### 4. culture
- Glassdoor rating and key themes from reviews
- LinkedIn employee count trend (growing/shrinking)
- Engineering culture signals (blog, GitHub, tech talks)
- DEI and remote work policies
- Common complaints from ex-employees

### 5. tech_stack
- Frontend / Backend / Mobile technologies
- Payment infrastructure (own rails vs. third-party)
- Cloud provider (AWS/GCP/Azure)
- Key engineering challenges they're solving
- APIs and integrations

### 6. strategy
- Current strategic priorities (from job descriptions, blog posts, announcements)
- OKRs / goals mentioned publicly
- Geographic expansion plans
- Product roadmap signals

### 7. challenges
- Industry challenges specific to this company
- Regulatory challenges (if fintech)
- Competition threats
- Scaling challenges
- Customer acquisition / retention issues

### 8. competitors
- Top 3-5 direct competitors
- How this company differentiates
- Market position (leader / challenger / niche)

## Research Sources (priority order)
1. Company official website and blog
2. LinkedIn company page (employee count, recent posts)
3. Crunchbase / PitchBook (funding data)
4. Glassdoor (culture intel)
5. TechCrunch / Wamda / Fintech Futures / MENA Bytes (news)
6. Job descriptions currently posted (signals strategy)
7. GitHub (tech stack signals)

## Output Quality Requirements
- Each section must be 150-400 words
- Must include specific facts (numbers, dates, names)
- No generic filler ("they are a leading company...")
- Flag if data is older than 6 months
- Include source URLs for verification
