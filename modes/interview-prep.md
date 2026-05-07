# Mode: interview-prep
# Generate company-specific interview preparation for Rizwan

## Purpose
Deep interview prep for a specific company + role combination.
Produces a comprehensive markdown document covering all aspects of the interview process.

## 6-Step Interview Prep Process

### Step 1: Interview Process Research
- Scrape Glassdoor interview reviews for this company
- Check Blind, Reddit (r/cscareerquestions, r/ProductManagement)
- Look at LinkedIn for people who've been interviewed there
- Find out: number of rounds, format (case study / behavioral / take-home), typical duration

### Step 2: Process Overview
Produce a round-by-round breakdown:
```
Round 1: HR Screen (30 min, phone)
  - Standard background check
  - Comp expectations
  - "Why us?" question
Round 2: Hiring Manager Interview (45-60 min, video)
  - Product thinking
  - Leadership experience
  - Technical depth
Round 3: Panel / Loop (3-4 interviews, half day)
  - Cross-functional stakeholders
  - Case study / product exercise
  - Culture fit
Round 4: Executive Interview (CEO/CPO, 30 min)
  - Vision alignment
  - Strategic thinking
Final: References + Offer
```

### Step 3: Likely Questions Bank

**Behavioral questions (STAR format):**
- Leadership: "Tell me about a time you led a team through a major challenge"
- Conflict: "Describe a time you disagreed with a stakeholder and how you handled it"
- Failure: "Tell me about a product that failed. What did you learn?"
- Influence: "How have you driven alignment without direct authority?"
- Prioritization: "How do you prioritize when you have more work than capacity?"
- Data: "Tell me about a time you used data to change a product direction"
- Scale: "Describe how you've scaled a product from 0 to X"

**Product/Strategic questions:**
- "How would you improve [company's main product]?"
- "What metrics would you track for [their core feature]?"
- "How would you approach pricing for [their BNPL product]?"
- "What would your 30/60/90 day plan look like?"
- "What do you see as our biggest competitive threat?"

**Programme/PMO questions (if Technical PM role):**
- "How do you run a SteerCo?"
- "Walk me through your RAID log process"
- "How do you manage a programme with 50+ cross-functional dependencies?"
- "Describe your approach to vendor management"

**Role-specific questions based on JD:**
(Generated dynamically from JD keywords)

### Step 4: STAR Story Mappings
For each behavioral question, map to Rizwan's best story:

| Theme | Best Story | Key Metric |
|-------|-----------|------------|
| Scale | SimPaisa $1B TPV | 0→$1B in 4 years |
| Enterprise delivery | TikTok/Uber integrations | 4 Fortune 500, on time |
| Team leadership | 40+ engineers, 12 squads | Cross-functional at scale |
| Product failure/learning | [specific example] | [learning outcome] |
| Data-driven | Daraz checkout A/B | 22% abandonment reduction |
| Stakeholder management | Board reporting at SimPaisa | Monthly SteerCo ownership |
| Regulatory compliance | PCI-DSS/KYC at SimPaisa | SBP-compliant infrastructure |

### Step 5: Technical Checklist
For product/payments roles:
- [ ] Know the company's payment flow end-to-end
- [ ] Understand their regulatory context (SCA, PCI-DSS, open banking)
- [ ] Review their tech stack (from research)
- [ ] Prepare product metrics framework (GMV, TPV, take-rate, churn)
- [ ] Study their competitors and differentiation
- [ ] Know their recent news / funding / product launches
- [ ] Prepare questions to ask them (see Step 6)

### Step 6: Questions to Ask Them
Always prepare 5-7 thoughtful questions:
1. "What does success look like for this role in 90 days?"
2. "What's the biggest product challenge you're trying to solve right now?"
3. "How does product interface with engineering and design here?"
4. "What does the roadmap planning process look like?"
5. "What's the team's biggest strength and where do you want to improve?"
6. "How has the product strategy evolved since [recent funding/news]?"
7. "What's the technical debt situation and how does product factor into addressing it?"

## Output Format
Save as: `output/interview_prep/{company}-{role}-{date}.md`

Structure:
```markdown
# Interview Prep: {Role} @ {Company}
Generated: {date}

## Company Quick Brief
[5-bullet overview from company knowledge]

## Interview Process
[Round-by-round from research]

## Likely Questions + Suggested Answers
[Question → STAR story mapping]

## Technical Checklist
[Checkbox list]

## Red Flag Prep
[Questions that probe weaknesses, with bridging responses]

## Negotiation Script
[Comp discussion framework]

## Questions to Ask
[5-7 questions with rationale]

## 30/60/90 Day Plan
[Role-specific entry plan]
```
