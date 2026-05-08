-- ========================================================================
-- Phase A: Profile Foundation — combined schema + seed
-- Paste into Supabase SQL editor and click Run.
-- Idempotent: safe to re-run.
-- ========================================================================

-- ══════════════════════════════════════════════════════════════════════════
-- Profile Foundation Schema — Phase A
-- Adds profile_* tables for the Master Profile and Keyword Intelligence dashboards.
-- Safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════

-- ── profile_master ────────────────────────────────────────────────────────
-- Single-row table holding the top-level identity + summary.
CREATE TABLE IF NOT EXISTS profile_master (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    name            TEXT NOT NULL,
    headline        TEXT,
    summary         TEXT,
    location        TEXT,
    email           TEXT,
    phones          TEXT[],
    linkedin_url    TEXT,
    core_competencies TEXT[],
    technical_knowledge TEXT[],
    languages       JSONB DEFAULT '[]'::jsonb,
    ai_solutions    JSONB DEFAULT '[]'::jsonb,
    tailored_resumes_count INTEGER DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT singleton CHECK (id = 1)
);

-- ── profile_experience ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_experience (
    id              SERIAL PRIMARY KEY,
    sort_order      INTEGER NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    scope           TEXT,
    dates           TEXT,
    summary         TEXT,
    highlights      TEXT[],
    groups          JSONB DEFAULT '[]'::jsonb,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profile_experience_sort ON profile_experience(sort_order);

-- ── profile_certification ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_certification (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    full_name       TEXT,
    issuer          TEXT,
    year            INTEGER,
    sort_order      INTEGER DEFAULT 100
);

-- ── profile_education ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_education (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    details         TEXT,
    year            TEXT,
    notes           TEXT,
    sort_order      INTEGER DEFAULT 100
);

-- ── profile_keyword ───────────────────────────────────────────────────────
-- The output of the keyword extractor. One row per (keyword, category).
CREATE TABLE IF NOT EXISTS profile_keyword (
    id              SERIAL PRIMARY KEY,
    keyword         TEXT NOT NULL,
    category        TEXT NOT NULL,
    total_occurrences INTEGER DEFAULT 0,
    files_count     INTEGER DEFAULT 0,
    coverage_pct    NUMERIC(5,2) DEFAULT 0,
    avg_per_file    NUMERIC(6,2) DEFAULT 0,
    ats_strength    NUMERIC(6,2) DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (keyword, category)
);

CREATE INDEX IF NOT EXISTS idx_profile_keyword_category ON profile_keyword(category);
CREATE INDEX IF NOT EXISTS idx_profile_keyword_strength ON profile_keyword(ats_strength DESC);

-- ── profile_keyword_category ──────────────────────────────────────────────
-- Per-category aggregates for the dashboard.
CREATE TABLE IF NOT EXISTS profile_keyword_category (
    category        TEXT PRIMARY KEY,
    keyword_count   INTEGER DEFAULT 0,
    total_occurrences INTEGER DEFAULT 0,
    avg_strength    NUMERIC(5,1) DEFAULT 0,
    top_keywords    TEXT[],
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── profile_source_document ───────────────────────────────────────────────
-- Registry of all parsed source files (resumes, LinkedIn docs, etc.)
CREATE TABLE IF NOT EXISTS profile_source_document (
    id              SERIAL PRIMARY KEY,
    file_hash       TEXT NOT NULL UNIQUE,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    document_class  TEXT NOT NULL,
    char_count      INTEGER DEFAULT 0,
    file_size       INTEGER DEFAULT 0,
    source_mtime    TIMESTAMPTZ,
    is_duplicate    BOOLEAN DEFAULT FALSE,
    parsed_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_source_doc_class ON profile_source_document(document_class);

-- ── profile_recommendation ────────────────────────────────────────────────
-- AI-generated suggestions: missing keywords, weak categories, conflicts.
CREATE TABLE IF NOT EXISTS profile_recommendation (
    id              SERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,  -- missing_keyword | weak_category | conflict | quantify
    severity        TEXT DEFAULT 'medium',  -- low | medium | high
    title           TEXT NOT NULL,
    detail          TEXT,
    related_keyword TEXT,
    related_category TEXT,
    dismissed       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendation_kind ON profile_recommendation(kind);

-- ── updated_at triggers ───────────────────────────────────────────────────
DROP TRIGGER IF EXISTS update_profile_master_updated_at ON profile_master;
CREATE TRIGGER update_profile_master_updated_at
    BEFORE UPDATE ON profile_master
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_profile_experience_updated_at ON profile_experience;
CREATE TRIGGER update_profile_experience_updated_at
    BEFORE UPDATE ON profile_experience
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_profile_keyword_updated_at ON profile_keyword;
CREATE TRIGGER update_profile_keyword_updated_at
    BEFORE UPDATE ON profile_keyword
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── seed: profile_master ─────────────────────────────────────────────────

INSERT INTO profile_master (
    id, name, headline, summary, location, email, phones, linkedin_url,
    core_competencies, technical_knowledge, languages, ai_solutions, tailored_resumes_count
) VALUES (
    1,
    'RIZWAN ZAFAR',
    'Product & Program Leader · Payments · Fintech · Cross-Border Systems',
    'Payments and financial services leader with 12+ years delivering measurable outcomes across digital payments, eCommerce, fintech, and banking — spanning five countries (Pakistan, Bangladesh, Nepal, Egypt, Iraq) and three continents. I''ve built and scaled a B2B payment platform from zero to $1B+ in transaction volume, managing the full lifecycle from strategy through execution across regulated markets. My work covers product management, P&L ownership, ecosystem readiness, cross-border payment corridors, issuer enablement, advanced analytics, AI strategy, and digital transformation. I''ve deployed four production AI solutions, built project management frameworks from scratch, and managed day-to-day relationships with 50+ financial institution partners. I combine technology architecture expertise with client-facing relationship management — communicating complex information to both technical and non-technical audiences, including executive leadership. I''m hands-on, intellectually curious, and comfortable rolling up my sleeves. I bring strong analytical, problem-solving, and decision-making skills, along with deep experience in stakeholder management, negotiation, risk management, and regulatory compliance. I drive collaborative cultures, develop talent, and build trust with every stakeholder in the chain. ---',
    'Dubai, UAE',
    'rizwanzaffar.pk@gmail.com',
    ARRAY['+971-58-9683970','+92-321-2027229']::TEXT[],
    'https://linkedin.com/in/rizwanzafarpmp',
    ARRAY['Product Management & P&L Ownership','Advanced Analytics & AI Strategy / Implementation','Digital Transformation (Strategy to Execution)','Payments Ecosystem Readiness & Performance Monitoring','Cross-Border Payment Corridors & Remittance','Issuer Acceptance & Enablement Optimization','GenAI Use Case Identification & Value Modeling','Digital Maturity Assessments & Market Research','Digital Acquisition Strategies & Growth','Omnichannel Customer Journey Mapping','Product Innovation & Product Roadmap','Commercialization & Go-to-Market Strategy','Business Development & Specialist Sales Support','Thought Leadership & Marketing Initiatives','Client Relationships & Client Engagement','Workshop Facilitation & Consulting Delivery','Stakeholder Management & Negotiation','Value Propositions for Digital Banking & Fintech','Proposal Development & Revenue Modeling','Data-Driven Advisory & Decision-Making','Approval Rates, Fraud & Risk Metrics Monitoring','Regulatory Alignment, KYC, AML & Compliance','Cross-Functional Collaboration & Team Coordination','Large-Scale Capital Project Delivery','Project Management Frameworks & Best Practices (Agile, PMBOK, Hybrid)','Scope, Budget & Timeline Management','Resource Allocation & Cost Management','Risk Identification, Mitigation & Management','Post-Launch Monitoring & Continuous Improvement','Conflict Management & Trust Building','Team Mentoring & Professional Development','Presentation Skills, Storytelling & Executive Communication','Product & Technical Documentation','Operational Resilience & Scalable Solutions','']::TEXT[],
    ARRAY['RESTful APIs','ISO 8583','ISO 20022','SWIFT Messaging','MPGS','MDES Tokenization','3DS / 3DS2','PCI DSS','ISO 27001','Mobile & eCommerce Payment Technologies','Self-Service SDKs (iOS, Android, Web)','Data Platforms & Analytics Infrastructure','GenAI (LLM, RAG Architecture)','Agile (Scrum, Kanban)','PMBOK','Jira','Confluence

---']::TEXT[],
    '[{"name": "English", "level": "Fluent (exceptional interpersonal and presentation skills)"}, {"name": "Urdu", "level": "Native"}, {"name": "Arabic", "level": "Conversational"}]'::jsonb,
    '[{"title": "AI Merchant Integration Chatbot (Slack & Telegram)", "description": "Built and deployed a GenAI-powered virtual integration expert that guides onboarded merchants through API setup, troubleshoots errors, and provides real-time documentation. Developed using open source LLM models with RAG architecture. Cut integration support time by 65% \u2014 a digital transformation of the entire merchant onboarding customer journey."}, {"title": "Intelligent System Monitoring & Auto-Escalation Bot", "description": "Deployed an AI system that detects payment error spikes, runs log analysis, identifies root cause, and auto-escalates to internal teams on Slack with full diagnostics \u2014 within minutes. Cut incident response time by 70%. Data-driven decision-making in real time."}, {"title": "AI Partner Support Automation (90% Resolution)", "description": "Built a GenAI-powered support bot handling merchant payment queries \u2014 settlement, disputes, decline codes, integration issues \u2014 resolving 90% without human intervention. That''s not a prototype; it''s been running in production for months."}, {"title": "Fraud Detection & AML Pilot (Active Banking Partner)", "description": "Currently in pilot with a major banking partner to deploy AI-driven fraud transaction identification and AML compliance. Value modeling showed 40% reduction in manual review \u2014 the pilot is validating that number."}]'::jsonb,
    20
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, headline = EXCLUDED.headline, summary = EXCLUDED.summary,
    location = EXCLUDED.location, email = EXCLUDED.email, phones = EXCLUDED.phones,
    linkedin_url = EXCLUDED.linkedin_url, core_competencies = EXCLUDED.core_competencies,
    technical_knowledge = EXCLUDED.technical_knowledge, languages = EXCLUDED.languages,
    ai_solutions = EXCLUDED.ai_solutions, tailored_resumes_count = EXCLUDED.tailored_resumes_count;


-- ── seed: profile_experience ─────────────────────────────────────────────
DELETE FROM profile_experience;

INSERT INTO profile_experience (sort_order, title, company, location, scope, dates, summary, highlights, groups)
VALUES (
    0,
    'Chief Product Officer',
    'Simpaisa (B2B Fintech Platform)',
    'Dubai, UAE',
    '5 Markets (PK, BD, NP, EG, IQ)',
    'Aug 2020 – Present',
    'B2B payment infrastructure enabling the movement of money to cards, accounts, and wallets for 150+ merchants, 390M+ mobile wallets, 370M+ bank accounts across five countries. $1B+ GTV, 97% success rate. Work cross-functionally with product, technology, risk and AML, finance, legal, and client services teams.',
    ARRAY[]::TEXT[],
    '[{"label": "Ecosystem Growth & P&L Ownership", "bullets": ["Built the payment ecosystem from scratch across five markets \u2014 spanning enablement (150+ merchant integrations), acceptance (50+ issuer partners), performance (97% success rate), compliance (PCI DSS, ISO 27001, AML/CFT across five jurisdictions), and revenue outcomes ($1B+ GTV).", "Own and drive the P&L for the full product line. Every product decision ties back to commercial results. Track record of successfully managing a P&L \u2014 built from zero to $1B+ in transaction volume."]}, {"label": "Product Innovation & Roadmap", "bullets": ["Built the multi-year product roadmap in partnership with regional stakeholders \u2014 prioritizing new use cases (card-on-file, recurring billing, tokenized payments, cross-border payouts), balancing regional needs against global standards.", "Led GenAI use case identification across the organization \u2014 evaluated 20+ potential use cases, built value modeling for each (ROI, feasibility, data readiness), and deployed four solutions into production. Translated strategic recommendations into actionable roadmaps with clear milestones and KPIs."]}, {"label": "Cross-Border Payment Corridors", "bullets": ["Led remittance corridor enablement across Pakistan, Bangladesh, Nepal, Egypt, and Iraq \u2014 ensuring market readiness, regulatory alignment, and seamless technical integration.", "Integrated with banks, mobile wallets, and acquirers to build cross-border payout rails with real-time disbursement and multi-currency settlement."]}, {"label": "Ecosystem Readiness & Performance Monitoring", "bullets": ["Built monitoring infrastructure tracking approval rates (improved from 60% to 85%), fraud and risk metrics, data integrity, and overall operational health across all five markets. Maintained 99.9% uptime SLAs.", "Set up real-time dashboards that surface performance degradation before it hits merchants."]}, {"label": "Issuer Acceptance & Enablement", "bullets": ["Ran the issuer enablement programme across 50+ payment partners \u2014 conducting technical workshops, optimizing risk policies, decline code mapping, threshold tuning, and authentication flow improvements.", "Achieved 14% authorization uplift and 22% token failure reduction. Led new and emerging use case enablement for card-on-file payments, recurring billing, and tokenized transactions."]}, {"label": "Post-Launch Monitoring & Continuous Improvement", "bullets": ["Implemented smart retry logic using decline code segmentation that recovers 120K+ failed transactions monthly ($14M+ GTV).", "Set up L1/L2/L3 triage workflows, TRT commitments, and root-cause reviews on every P1 incident \u2014 reducing merchant escalations by 40%."]}, {"label": "Data-Driven Initiatives", "bullets": ["Built analytics tracking decline codes, failure segmentation, and cohort performance to improve acceptance quality, consistency, and operational resilience across endpoints.", "Drove data-driven initiatives that feed every product and advisory decision."]}, {"label": "Commercialization & Go-to-Market", "bullets": ["Led go-to-market for every product launch across five markets. Provided specialist sales support \u2014 built pitch decks, ROI models, and data-driven advisory materials that helped close deals with 50+ financial institution partners.", "Created market entry proposals for five new corridors \u2014 each included regulatory analysis, competitive landscape, partner ecosystem mapping, revenue modeling, and go-to-market strategy. Secured board funding for expansion."]}, {"label": "Client Engagement & Ecosystem SME", "bullets": ["Serve as primary relationship manager for 50+ financial institution and banking partners. Guide clients through technical, operational, and compliance requirements.", "Create presentations for internal approvals and external client facing discussions. Storytelling translates commercialization opportunities into clear technical understanding for every audience.", "Work closely with sales and account managers to plan and manage the client''s integration strategy, schedule, and deadlines.", "Facilitate workshops and stakeholder sessions on payment strategy, digital banking capabilities, and GenAI adoption.", "Use moderation and facilitation skills in every partner readiness workshop, ensuring scalable outcomes.", "Proven ability to influence and communicate effectively across regional and functional lines."]}, {"label": "Thought Leadership & Documentation", "bullets": ["Authored whitepapers, case studies, and point-of-views on AI in payments, digital transformation, digital banking trends, and fintech innovation.", "Created API documentation, integration guides, merchant onboarding playbooks, and compliance documentation across all five markets.", "Built self-service SDKs (iOS, Android, Web) with certification automation tools that reduced merchant integration time from 6 weeks to 2 weeks."]}, {"label": "Project Delivery & Frameworks", "bullets": ["Delivered the full platform build across five countries \u2014 payment gateway infrastructure, cross-border payout corridors, tokenization systems, and merchant integration platforms. Every project delivered within scope, budget, and timeline.", "Built the organization''s project management methodology from scratch \u2014 Agile sprints for product development, PMBOK-based stage gates for capital projects, and a hybrid framework for cross-functional delivery."]}, {"label": "Team Leadership & Talent Development", "bullets": ["Grew the organization from 2 to 50+ across product, engineering, compliance, and BizOps.", "Built a culture of openness, flexibility, innovation, accountability, and excellence.", "Developed talent through mentoring, career development programmes, and knowledge sharing sessions on GenAI, digital strategy, and consulting best practices.", "Manage a complex and varied workload supporting multiple ongoing product and solution development initiatives."]}, {"label": "Technical Achievements", "bullets": ["MDES tokenization integration on MPGS rails (DPAN/FPAN provisioning, credential lifecycle management, cryptographic validation)", "Smart retry logic recovering 120K+ failed transactions/month ($14M+ GTV)", "Payment success improvement from 60% to 85%", "14% authorization uplift across 50+ issuer partners", "97% overall transaction success rate, 99.9% uptime"]}]'::jsonb
);

INSERT INTO profile_experience (sort_order, title, company, location, scope, dates, summary, highlights, groups)
VALUES (
    1,
    'Project Manager — Payments & Platform Delivery',
    'Daraz (Alibaba Group)',
    'Karachi, Pakistan',
    'PK, BD, Sri Lanka',
    'Mar 2020 – Aug 2020',
    'South Asia''s leading eCommerce platform (5 countries, 50M+ users). Led ecosystem readiness and performance optimization for digital payments.',
    ARRAY['Optimized multi-rail checkout integrating cards and wallets — 15% conversion uplift, 20% reduction in false declines through 3DS2 policy optimization. Worked cross-functionally with client services, risk, and technology teams.','Aligned payment orchestration with Alibaba''s global technical standards on retry logic, failure handling, and API response code management.','Automated payout reconciliation achieving 99.5% settlement accuracy and data integrity.','Created product documentation and presentations for internal approvals and external partner discussions.','Communicated complex information to both technical and non-technical audiences across multiple geographic locations and time zones.','Strong decision-making abilities to raise issues at appropriate times.']::TEXT[],
    '[]'::jsonb
);

INSERT INTO profile_experience (sort_order, title, company, location, scope, dates, summary, highlights, groups)
VALUES (
    2,
    'Head of Product & Projects',
    'TapmadTV (OTT Platform)',
    'Karachi, Pakistan',
    '',
    'Jul 2017 – Mar 2020',
    '',
    ARRAY['Scaled the platform from 0 to 5M paid subscribers through ecosystem enablement — multi-rail payment acceptance (cards, wallets, carrier billing), issuer acceptance optimization, and seamless technical integration with payment service providers.','Designed tokenized subscription billing with Card-on-File framework.','Managed cross-border remittance operations for content licensing payments. Led regulatory requirements, KYC and compliance processes for payment integrations.','Built analytics infrastructure tracking approval rates and fraud metrics — improved subscriber retention by 30%.','Owned P&L for the subscription product line. Built the product roadmap, drove commercialization of new payment methods, and managed go-to-market strategy.','Managed a complex and varied workload across product development, partner enablement, and ongoing solution development initiatives.','Developed talent across product and engineering teams. Drove thought leadership on OTT monetization in emerging markets.']::TEXT[],
    '[]'::jsonb
);

INSERT INTO profile_experience (sort_order, title, company, location, scope, dates, summary, highlights, groups)
VALUES (
    3,
    'Project Management Roles',
    'Infinity Engineering',
    'Wing Logic LLC (UAE)',
    'CIMKO (D.R. Congo) | DS Engineering | UAE, Pakistan, D.R. Congo',
    '2012 – 2017',
    '',
    ARRAY['Led ERP implementations, infrastructure projects, and digital transformation projects using Agile and PMBOK methodologies.','Managed cross-functional teams across multiple geographic locations (UAE, Pakistan, D.R. Congo) and time zones.','Reduced project delays by 60%.','Built project management frameworks, best practices, and tools from the ground up.','Managed project risks, ensured compliance with quality standards, and maintained rigorous budgeting and scheduling.','Built strong analytical, problem-solving, and decision-making skills in high-pressure environments.','Demonstrated negotiation capabilities and stakeholder management with diverse clients and regulatory bodies.','Demonstrated flexibility and adaptability in response to changing roles, priorities, and responsibilities.']::TEXT[],
    '[]'::jsonb
);

-- ── seed: profile_certification ──────────────────────────────────────────
DELETE FROM profile_certification;

INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ('PMP', 'Project Management Professional', 0)
ON CONFLICT (name) DO NOTHING;

INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ('PMI-ACP', 'Agile Certified Practitioner', 1)
ON CONFLICT (name) DO NOTHING;

INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ('CSPO', 'Certified Scrum Product Owner', 2)
ON CONFLICT (name) DO NOTHING;

INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ('CSM', 'Certified ScrumMaster', 3)
ON CONFLICT (name) DO NOTHING;

INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ('COBIT 5', '', 4)
ON CONFLICT (name) DO NOTHING;

INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ('ITIL v3', '', 5)
ON CONFLICT (name) DO NOTHING;

-- ── seed: profile_education ──────────────────────────────────────────────
DELETE FROM profile_education;

INSERT INTO profile_education (title, details, year, notes, sort_order)
VALUES (
    'MIT — Certificate, Mastering Design Thinking',
    '',
    '',
    '',
    0
);

INSERT INTO profile_education (title, details, year, notes, sort_order)
VALUES (
    'Masters (M.Sc.), Applied Physics — University of Karachi',
    '',
    '',
    'Postgraduate / Advanced Degree. Quantitative discipline with deep technical understanding, analytical rigor, and product development foundations.',
    1
);

INSERT INTO profile_education (title, details, year, notes, sort_order)
VALUES (
    'B.Sc., Physics — University of Karachi',
    '',
    '',
    'Bachelor''s Degree.',
    2
);

-- ── seed: profile_keyword ────────────────────────────────────────────────
DELETE FROM profile_keyword;

INSERT INTO profile_keyword (keyword, category, total_occurrences, files_count, coverage_pct, avg_per_file, ats_strength) VALUES
('payment', 'Payments', 2178, 192, 94.6, 11.34, 96.7),
('payments', 'Payments', 1208, 160, 78.8, 7.55, 77.5),
('compliance', 'Compliance', 1021, 182, 89.7, 5.61, 76.2),
('regulatory', 'Compliance', 892, 180, 88.7, 4.96, 73.0),
('settlement', 'Payments', 900, 124, 61.1, 7.26, 65.7),
('merchant', 'Payments', 654, 160, 78.8, 4.09, 63.6),
('reconciliation', 'Payments', 842, 101, 49.8, 8.34, 63.2),
('agile', 'Agile', 603, 160, 78.8, 3.77, 62.4),
('fintech', 'Fintech', 550, 158, 77.8, 3.48, 60.6),
('leadership', 'Leadership', 514, 144, 70.9, 3.57, 56.8),
('scrum', 'Agile', 483, 146, 71.9, 3.31, 56.4),
('cross-border', 'Payments', 542, 137, 67.5, 3.96, 56.3),
('card', 'Payments', 507, 142, 70.0, 3.57, 56.3),
('chief', 'Leadership', 233, 166, 81.8, 1.4, 54.7),
('head of', 'Leadership', 215, 161, 79.3, 1.34, 52.9),
('pmo', 'PMO', 480, 124, 61.1, 3.87, 52.1),
('pmp', 'PMO', 228, 156, 76.8, 1.46, 52.0),
('acp', 'Agile', 188, 153, 75.4, 1.23, 50.1),
('analytics', 'Analytics', 314, 130, 64.0, 2.42, 48.1),
('cspo', 'Agile', 172, 146, 71.9, 1.18, 47.9),
('csm', 'Agile', 156, 145, 71.4, 1.08, 47.2),
('mastercard', 'Payments', 469, 77, 37.9, 6.09, 47.1),
('fx', 'Payments', 397, 72, 35.5, 5.51, 43.3),
('iso 27001', 'Compliance', 246, 117, 57.6, 2.1, 43.0),
('audit', 'Compliance', 329, 100, 49.3, 3.29, 42.7),
('cfo', 'Leadership', 94, 9, 4.4, 10.44, 42.7),
('api', 'Technical', 302, 102, 50.2, 2.96, 42.0),
('gtv', 'Payments', 304, 89, 43.8, 3.42, 40.0),
('product strategy', 'Product Management', 206, 106, 52.2, 1.94, 39.1),
('executive', 'Leadership', 225, 102, 50.2, 2.21, 39.0),
('remittance', 'Payments', 201, 106, 52.2, 1.9, 38.9),
('wallet', 'Payments', 241, 96, 47.3, 2.51, 38.4),
('cpo', 'Leadership', 211, 30, 14.8, 7.03, 37.0),
('fraud', 'Payments', 274, 76, 37.4, 3.61, 36.9),
('aml', 'Compliance', 241, 87, 42.9, 2.77, 36.8),
('product owner', 'Product Management', 116, 109, 53.7, 1.06, 36.5),
('payment gateway', 'Payments', 212, 91, 44.8, 2.33, 36.2),
('merchants', 'Payments', 212, 85, 41.9, 2.49, 35.1),
('leader', 'Leadership', 199, 87, 42.9, 2.29, 34.9),
('ai', 'AI', 236, 45, 22.2, 5.24, 34.3),
('mpgs', 'Payments', 163, 90, 44.3, 1.81, 33.8),
('issuer', 'Payments', 228, 69, 34.0, 3.3, 33.6),
('jira', 'Technical', 171, 85, 41.9, 2.01, 33.2),
('credit', 'Fintech', 217, 65, 32.0, 3.34, 32.6),
('tokenization', 'Payments', 204, 70, 34.5, 2.91, 32.3),
('cards', 'Payments', 173, 76, 37.4, 2.28, 31.6),
('product management', 'Product Management', 174, 72, 35.5, 2.42, 30.9),
('confluence', 'Technical', 114, 83, 40.9, 1.37, 30.0),
('orchestration', 'Payments', 166, 66, 32.5, 2.52, 29.6),
('iso 20022', 'Payments', 134, 76, 37.4, 1.76, 29.5),
('pci dss', 'Payments', 174, 54, 26.6, 3.22, 28.8),
('r', 'Analytics', 114, 77, 37.9, 1.48, 28.7),
('visa', 'Payments', 154, 63, 31.0, 2.44, 28.4),
('cft', 'Compliance', 154, 63, 31.0, 2.44, 28.4),
('stakeholder management', 'Product Management', 119, 74, 36.5, 1.61, 28.3),
('go-to-market', 'Product Management', 125, 67, 33.0, 1.87, 27.3),
('authorization', 'Payments', 123, 68, 33.5, 1.81, 27.3),
('product roadmap', 'Product Management', 108, 69, 34.0, 1.57, 26.7),
('transaction volume', 'Payments', 100, 71, 35.0, 1.41, 26.6),
('kyc', 'Compliance', 137, 57, 28.1, 2.4, 26.5),
('risk management', 'Risk & Security', 100, 70, 34.5, 1.43, 26.4),
('mdes', 'Payments', 134, 54, 26.6, 2.48, 25.9),
('project management office', 'PMO', 111, 64, 31.5, 1.73, 25.9),
('checkout', 'Payments', 88, 71, 35.0, 1.24, 25.9),
('kafka', 'Technical', 55, 10, 4.9, 5.5, 25.0),
('financial services', 'Fintech', 113, 58, 28.6, 1.95, 24.9),
('vp', 'Leadership', 98, 21, 10.3, 4.67, 24.9),
('central bank', 'Compliance', 118, 52, 25.6, 2.27, 24.4),
('product lifecycle', 'Product Management', 92, 60, 29.6, 1.53, 23.9),
('corridor', 'Payments', 108, 52, 25.6, 2.08, 23.7),
('genai', 'AI', 68, 14, 6.9, 4.86, 23.6),
('instant payment', 'Payments', 17, 3, 1.5, 5.67, 23.6),
('authentication', 'Payments', 95, 57, 28.1, 1.67, 23.5),
('sprint', 'Agile', 105, 52, 25.6, 2.02, 23.4),
('churn', 'Analytics', 87, 58, 28.6, 1.5, 23.1),
('audit trail', 'Compliance', 46, 9, 4.4, 5.11, 23.1),
('product manager', 'Product Management', 110, 44, 21.7, 2.5, 23.0),
('pmbok', 'PMO', 78, 60, 29.6, 1.3, 22.9),
('apis', 'Technical', 100, 50, 24.6, 2.0, 22.8),
('acquiring', 'Payments', 107, 36, 17.7, 2.97, 22.5),
('board', 'Leadership', 84, 52, 25.6, 1.62, 21.8),
('bnpl', 'Fintech', 95, 40, 19.7, 2.38, 21.3),
('3ds2', 'Payments', 87, 45, 22.2, 1.93, 21.0),
('hiring', 'Leadership', 85, 25, 12.3, 3.4, 21.0),
('bi', 'Analytics', 67, 53, 26.1, 1.26, 20.7),
('agentic', 'AI', 24, 5, 2.5, 4.8, 20.7),
('llm', 'AI', 36, 8, 3.9, 4.5, 20.4),
('retry logic', 'Payments', 83, 42, 20.7, 1.98, 20.3),
('ceo', 'Leadership', 80, 25, 12.3, 3.2, 20.2),
('program management', 'PMO', 79, 39, 19.2, 2.03, 19.6),
('scheme', 'Payments', 76, 38, 18.7, 2.0, 19.2),
('digital banking', 'Fintech', 75, 39, 19.2, 1.92, 19.2),
('data analytics', 'Analytics', 64, 46, 22.7, 1.39, 19.2),
('cto', 'Leadership', 58, 16, 7.9, 3.62, 19.2),
('acquirer', 'Payments', 72, 39, 19.2, 1.85, 18.9),
('velocity', 'Agile', 61, 44, 21.7, 1.39, 18.6),
('gtm', 'Product Management', 62, 43, 21.2, 1.44, 18.5),
('tokenized', 'Payments', 61, 42, 20.7, 1.45, 18.2),
('raid', 'PMO', 61, 20, 9.9, 3.05, 18.1),
('lean', 'Agile', 51, 45, 22.2, 1.13, 17.8)
ON CONFLICT (keyword, category) DO UPDATE SET total_occurrences = EXCLUDED.total_occurrences, files_count = EXCLUDED.files_count, coverage_pct = EXCLUDED.coverage_pct, avg_per_file = EXCLUDED.avg_per_file, ats_strength = EXCLUDED.ats_strength;

INSERT INTO profile_keyword (keyword, category, total_occurrences, files_count, coverage_pct, avg_per_file, ats_strength) VALUES
('psp', 'Payments', 62, 36, 17.7, 1.72, 17.5),
('user stories', 'Product Management', 61, 37, 18.2, 1.65, 17.5),
('financial infrastructure', 'Fintech', 55, 18, 8.9, 3.06, 17.5),
('auditor', 'Compliance', 33, 9, 4.4, 3.67, 17.3),
('decline code', 'Payments', 60, 31, 15.3, 1.94, 16.9),
('openai', 'AI', 22, 6, 3.0, 3.67, 16.4),
('dashboard', 'Analytics', 52, 20, 9.9, 2.6, 16.3),
('cbuae', 'Compliance', 4, 1, 0.5, 4.0, 16.3),
('system design', 'Technical', 46, 16, 7.9, 2.88, 16.2),
('competitive analysis', 'Product Management', 49, 36, 17.7, 1.36, 16.1),
('fraud detection', 'Risk & Security', 53, 31, 15.3, 1.71, 16.0),
('team leadership', 'Leadership', 43, 39, 19.2, 1.1, 15.9),
('financial inclusion', 'Fintech', 46, 17, 8.4, 2.71, 15.8),
('swift', 'Payments', 32, 10, 4.9, 3.2, 15.8),
('card-on-file', 'Payments', 46, 35, 17.2, 1.31, 15.6),
('operational risk', 'Risk & Security', 51, 25, 12.3, 2.04, 15.5),
('sprint planning', 'Agile', 44, 35, 17.2, 1.26, 15.4),
('3ds', 'Payments', 49, 28, 13.8, 1.75, 15.3),
('issuer acceptance', 'Payments', 14, 4, 2.0, 3.5, 15.2),
('event-driven', 'Technical', 44, 18, 8.9, 2.44, 15.1),
('ltv', 'Analytics', 30, 10, 4.9, 3.0, 15.0),
('regulatory reporting', 'Compliance', 45, 26, 12.8, 1.73, 14.6),
('ms project', 'PMO', 44, 23, 11.3, 1.91, 14.5),
('tableau', 'Analytics', 44, 22, 10.8, 2.0, 14.5),
('product vision', 'Product Management', 41, 31, 15.3, 1.32, 14.5),
('user research', 'Product Management', 41, 24, 11.8, 1.71, 13.9),
('licensing', 'Compliance', 37, 20, 9.9, 1.85, 13.3),
('director', 'Leadership', 35, 20, 9.9, 1.75, 12.9),
('risk mitigation', 'Risk & Security', 35, 22, 10.8, 1.59, 12.9),
('credit risk', 'Risk & Security', 35, 21, 10.3, 1.67, 12.9),
('mentoring', 'Leadership', 35, 20, 9.9, 1.75, 12.9),
('milestone', 'PMO', 31, 27, 13.3, 1.15, 12.6),
('langchain', 'AI', 6, 2, 1.0, 3.0, 12.6),
('feature prioritization', 'Product Management', 30, 27, 13.3, 1.11, 12.4),
('claude', 'AI', 18, 7, 3.4, 2.57, 12.4),
('ml', 'AI', 32, 21, 10.3, 1.52, 12.3),
('lending', 'Fintech', 25, 11, 5.4, 2.27, 12.3),
('swagger', 'Technical', 3, 1, 0.5, 3.0, 12.3),
('openapi', 'Technical', 3, 1, 0.5, 3.0, 12.3),
('sepa', 'Payments', 3, 1, 0.5, 3.0, 12.3),
('okr', 'Product Management', 30, 25, 12.3, 1.2, 12.2),
('tpv', 'Payments', 27, 13, 6.4, 2.08, 12.2),
('mvp', 'Product Management', 23, 10, 4.9, 2.3, 12.2),
('approval rate', 'Payments', 29, 26, 12.8, 1.12, 12.1),
('portfolio management', 'PMO', 30, 21, 10.3, 1.43, 11.9),
('coaching', 'Leadership', 28, 15, 7.4, 1.87, 11.9),
('open banking', 'Fintech', 25, 12, 5.9, 2.08, 11.9),
('smart retry', 'Payments', 27, 25, 12.3, 1.08, 11.7),
('issuing', 'Payments', 28, 17, 8.4, 1.65, 11.6),
('card network', 'Payments', 8, 3, 1.5, 2.67, 11.6),
('a/b testing', 'Product Management', 27, 16, 7.9, 1.69, 11.5),
('experimentation', 'Analytics', 20, 9, 4.4, 2.22, 11.5),
('c-suite', 'Leadership', 26, 18, 8.9, 1.44, 11.1),
('rag', 'AI', 19, 9, 4.4, 2.11, 11.1),
('distributed systems', 'Technical', 14, 6, 3.0, 2.33, 11.1),
('safe', 'Agile', 22, 12, 5.9, 1.83, 10.9),
('customer journey', 'Product Management', 21, 11, 5.4, 1.91, 10.9),
('dpan', 'Payments', 24, 19, 9.4, 1.26, 10.7),
('fpan', 'Payments', 24, 19, 9.4, 1.26, 10.7),
('fraud prevention', 'Risk & Security', 23, 14, 6.9, 1.64, 10.7),
('digital wallet', 'Payments', 19, 10, 4.9, 1.9, 10.6),
('issuer enablement', 'Payments', 19, 10, 4.9, 1.9, 10.6),
('dau', 'Analytics', 5, 2, 1.0, 2.5, 10.6),
('ach', 'Payments', 5, 2, 1.0, 2.5, 10.6),
('executive presence', 'Leadership', 5, 2, 1.0, 2.5, 10.6),
('cybersecurity', 'Risk & Security', 5, 2, 1.0, 2.5, 10.6),
('mau', 'Analytics', 5, 2, 1.0, 2.5, 10.6),
('north star', 'Analytics', 5, 2, 1.0, 2.5, 10.6),
('discover', 'Payments', 13, 6, 3.0, 2.17, 10.4),
('product-market fit', 'Product Management', 11, 5, 2.5, 2.2, 10.3),
('vts', 'Payments', 7, 3, 1.5, 2.33, 10.2),
('mobile wallet', 'Payments', 21, 20, 9.9, 1.05, 10.1),
('kyb', 'Compliance', 21, 20, 9.9, 1.05, 10.1),
('incident response', 'Risk & Security', 21, 17, 8.4, 1.24, 10.0),
('webhook', 'Technical', 15, 8, 3.9, 1.88, 9.9),
('privacy', 'Risk & Security', 15, 8, 3.9, 1.88, 9.9),
('value proposition', 'Product Management', 18, 11, 5.4, 1.64, 9.8),
('real-time payments', 'Payments', 19, 13, 6.4, 1.46, 9.7),
('steering committee', 'PMO', 19, 14, 6.9, 1.36, 9.6),
('rest', 'Technical', 19, 14, 6.9, 1.36, 9.6),
('team building', 'Leadership', 19, 17, 8.4, 1.12, 9.5),
('product portfolio', 'Product Management', 19, 15, 7.4, 1.27, 9.5),
('interchange', 'Payments', 16, 10, 4.9, 1.6, 9.4),
('deliverable', 'PMO', 14, 8, 3.9, 1.75, 9.4),
('product analytics', 'Product Management', 18, 14, 6.9, 1.29, 9.3),
('sdk', 'Technical', 18, 14, 6.9, 1.29, 9.3),
('sql', 'Analytics', 15, 9, 4.4, 1.67, 9.3),
('transaction monitoring', 'Risk & Security', 18, 16, 7.9, 1.12, 9.2),
('regulatory alignment', 'Compliance', 17, 13, 6.4, 1.31, 9.1),
('product launch', 'Product Management', 16, 11, 5.4, 1.45, 9.1),
('kanban', 'Agile', 16, 11, 5.4, 1.45, 9.1),
('information security', 'Risk & Security', 16, 11, 5.4, 1.45, 9.1),
('regulation', 'Compliance', 11, 6, 3.0, 1.83, 9.1),
('cohort analysis', 'Analytics', 17, 16, 7.9, 1.06, 9.0),
('machine learning', 'AI', 13, 8, 3.9, 1.62, 8.9),
('cac', 'Analytics', 12, 7, 3.4, 1.71, 8.9),
('prompt engineering', 'AI', 9, 5, 2.5, 1.8, 8.7),
('iso 8583', 'Payments', 14, 10, 4.9, 1.4, 8.6),
('emv', 'Payments', 4, 2, 1.0, 2.0, 8.6),
('microservices', 'Technical', 15, 14, 6.9, 1.07, 8.4)
ON CONFLICT (keyword, category) DO UPDATE SET total_occurrences = EXCLUDED.total_occurrences, files_count = EXCLUDED.files_count, coverage_pct = EXCLUDED.coverage_pct, avg_per_file = EXCLUDED.avg_per_file, ats_strength = EXCLUDED.ats_strength;

INSERT INTO profile_keyword (keyword, category, total_occurrences, files_count, coverage_pct, avg_per_file, ats_strength) VALUES
('pos', 'Payments', 12, 8, 3.9, 1.5, 8.4),
('product backlog', 'Product Management', 12, 8, 3.9, 1.5, 8.4),
('change control', 'PMO', 10, 6, 3.0, 1.67, 8.4),
('restful', 'Technical', 10, 6, 3.0, 1.67, 8.4),
('product discovery', 'Product Management', 14, 11, 5.4, 1.27, 8.3),
('risk register', 'PMO', 2, 1, 0.5, 2.0, 8.3),
('bacs', 'Payments', 2, 1, 0.5, 2.0, 8.3),
('co-founder', 'Leadership', 2, 1, 0.5, 2.0, 8.3),
('deep learning', 'AI', 2, 1, 0.5, 2.0, 8.3),
('story points', 'Agile', 2, 1, 0.5, 2.0, 8.3),
('ssl', 'Technical', 2, 1, 0.5, 2.0, 8.3),
('buy now pay later', 'Fintech', 13, 10, 4.9, 1.3, 8.2),
('embedding', 'AI', 14, 14, 6.9, 1.0, 8.1),
('burndown', 'Agile', 12, 9, 4.4, 1.33, 8.0),
('azure', 'Technical', 10, 7, 3.4, 1.43, 7.8),
('product requirements', 'Product Management', 9, 6, 3.0, 1.5, 7.8),
('javascript', 'Technical', 5, 3, 1.5, 1.67, 7.6),
('chargeback', 'Payments', 5, 3, 1.5, 1.67, 7.6),
('aws', 'Technical', 10, 8, 3.9, 1.25, 7.4),
('sanctions screening', 'Compliance', 11, 11, 5.4, 1.0, 7.3),
('business intelligence', 'Analytics', 11, 11, 5.4, 1.0, 7.3),
('react', 'Technical', 6, 4, 2.0, 1.5, 7.2),
('sama', 'Compliance', 6, 4, 2.0, 1.5, 7.2),
('license', 'Compliance', 10, 9, 4.4, 1.11, 7.1),
('agile transformation', 'Agile', 8, 6, 3.0, 1.33, 7.1),
('anthropic', 'AI', 7, 5, 2.5, 1.4, 7.1),
('prd', 'Product Management', 7, 5, 2.5, 1.4, 7.1),
('founder', 'Leadership', 7, 5, 2.5, 1.4, 7.1),
('executive communication', 'Leadership', 9, 9, 4.4, 1.0, 6.7),
('postman', 'Technical', 3, 2, 1.0, 1.5, 6.6),
('backlog refinement', 'Agile', 3, 2, 1.0, 1.5, 6.6),
('kpi tracking', 'Analytics', 7, 6, 3.0, 1.17, 6.4),
('scheme rules', 'Payments', 6, 5, 2.5, 1.2, 6.3),
('security architecture', 'Risk & Security', 5, 4, 2.0, 1.25, 6.2),
('user journey', 'Product Management', 4, 3, 1.5, 1.33, 6.2),
('retrospective', 'Agile', 4, 3, 1.5, 1.33, 6.2),
('user story', 'Agile', 4, 3, 1.5, 1.33, 6.2),
('stablecoin', 'Fintech', 4, 3, 1.5, 1.33, 6.2),
('fca', 'Compliance', 4, 3, 1.5, 1.33, 6.2),
('embeddings', 'AI', 4, 3, 1.5, 1.33, 6.2),
('ai strategy', 'AI', 4, 3, 1.5, 1.33, 6.2),
('product positioning', 'Product Management', 7, 7, 3.4, 1.0, 6.1),
('board reporting', 'Leadership', 7, 7, 3.4, 1.0, 6.1),
('encryption', 'Technical', 7, 7, 3.4, 1.0, 6.1),
('mixpanel', 'Analytics', 6, 6, 3.0, 1.0, 5.8),
('performance management', 'Leadership', 5, 5, 2.5, 1.0, 5.5),
('gdpr', 'Compliance', 5, 5, 2.5, 1.0, 5.5),
('data protection', 'Risk & Security', 5, 5, 2.5, 1.0, 5.5),
('embedded finance', 'Fintech', 5, 5, 2.5, 1.0, 5.5),
('vulnerability', 'Risk & Security', 5, 5, 2.5, 1.0, 5.5),
('scrum master', 'Agile', 5, 5, 2.5, 1.0, 5.5),
('natural language', 'AI', 4, 4, 2.0, 1.0, 5.2),
('amplitude', 'Analytics', 4, 4, 2.0, 1.0, 5.2),
('funnel analysis', 'Analytics', 4, 4, 2.0, 1.0, 5.2),
('anti-money laundering', 'Compliance', 4, 4, 2.0, 1.0, 5.2),
('data pipeline', 'Technical', 4, 4, 2.0, 1.0, 5.2),
('ai agent', 'AI', 3, 3, 1.5, 1.0, 4.9),
('founding', 'Leadership', 3, 3, 1.5, 1.0, 4.9),
('prince2', 'PMO', 3, 3, 1.5, 1.0, 4.9),
('ci/cd', 'Technical', 3, 3, 1.5, 1.0, 4.9),
('resource management', 'PMO', 3, 3, 1.5, 1.0, 4.9),
('coo', 'Leadership', 3, 3, 1.5, 1.0, 4.9),
('stakeholder influence', 'Leadership', 2, 2, 1.0, 1.0, 4.6),
('soc 2', 'Compliance', 2, 2, 1.0, 1.0, 4.6),
('soc', 'Risk & Security', 2, 2, 1.0, 1.0, 4.6),
('gantt', 'PMO', 2, 2, 1.0, 1.0, 4.6),
('mentor', 'Leadership', 2, 2, 1.0, 1.0, 4.6),
('talent development', 'Leadership', 2, 2, 1.0, 1.0, 4.6),
('1:1', 'Leadership', 2, 2, 1.0, 1.0, 4.6),
('looker', 'Analytics', 2, 2, 1.0, 1.0, 4.6),
('postgresql', 'Technical', 2, 2, 1.0, 1.0, 4.6),
('mongodb', 'Technical', 2, 2, 1.0, 1.0, 4.6),
('metabase', 'Analytics', 2, 2, 1.0, 1.0, 4.6),
('amex', 'Payments', 2, 2, 1.0, 1.0, 4.6),
('node', 'Technical', 2, 2, 1.0, 1.0, 4.6),
('project governance', 'PMO', 2, 2, 1.0, 1.0, 4.6),
('discovery to delivery', 'Product Management', 2, 2, 1.0, 1.0, 4.6),
('wbs', 'PMO', 1, 1, 0.5, 1.0, 4.3),
('regtech', 'Fintech', 1, 1, 0.5, 1.0, 4.3),
('python', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('java', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('authorization rate', 'Payments', 1, 1, 0.5, 1.0, 4.3),
('penetration testing', 'Risk & Security', 1, 1, 0.5, 1.0, 4.3),
('people management', 'Leadership', 1, 1, 0.5, 1.0, 4.3),
('fatf', 'Compliance', 1, 1, 0.5, 1.0, 4.3),
('recruiting', 'Leadership', 1, 1, 0.5, 1.0, 4.3),
('voice of customer', 'Product Management', 1, 1, 0.5, 1.0, 4.3),
('cmo', 'Leadership', 1, 1, 0.5, 1.0, 4.3),
('north star metric', 'Product Management', 1, 1, 0.5, 1.0, 4.3),
('standup', 'Agile', 1, 1, 0.5, 1.0, 4.3),
('graphql', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('gcp', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('heap', 'Analytics', 1, 1, 0.5, 1.0, 4.3),
('snowflake', 'Analytics', 1, 1, 0.5, 1.0, 4.3),
('bigquery', 'Analytics', 1, 1, 0.5, 1.0, 4.3),
('data warehouse', 'Analytics', 1, 1, 0.5, 1.0, 4.3),
('statistical significance', 'Analytics', 1, 1, 0.5, 1.0, 4.3),
('rtp', 'Payments', 1, 1, 0.5, 1.0, 4.3),
('agentic commerce', 'Fintech', 1, 1, 0.5, 1.0, 4.3),
('generative ai', 'AI', 1, 1, 0.5, 1.0, 4.3)
ON CONFLICT (keyword, category) DO UPDATE SET total_occurrences = EXCLUDED.total_occurrences, files_count = EXCLUDED.files_count, coverage_pct = EXCLUDED.coverage_pct, avg_per_file = EXCLUDED.avg_per_file, ats_strength = EXCLUDED.ats_strength;

INSERT INTO profile_keyword (keyword, category, total_occurrences, files_count, coverage_pct, avg_per_file, ats_strength) VALUES
('scheme certification', 'Payments', 1, 1, 0.5, 1.0, 4.3),
('payment service provider', 'Payments', 1, 1, 0.5, 1.0, 4.3),
('scaled organization', 'Leadership', 1, 1, 0.5, 1.0, 4.3),
('node.js', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('power bi', 'Analytics', 1, 1, 0.5, 1.0, 4.3),
('microservice', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('project charter', 'PMO', 1, 1, 0.5, 1.0, 4.3),
('product kpi', 'Product Management', 1, 1, 0.5, 1.0, 4.3),
('ios sdk', 'Technical', 1, 1, 0.5, 1.0, 4.3),
('android sdk', 'Technical', 1, 1, 0.5, 1.0, 4.3)
ON CONFLICT (keyword, category) DO UPDATE SET total_occurrences = EXCLUDED.total_occurrences, files_count = EXCLUDED.files_count, coverage_pct = EXCLUDED.coverage_pct, avg_per_file = EXCLUDED.avg_per_file, ats_strength = EXCLUDED.ats_strength;

-- ── seed: profile_keyword_category ───────────────────────────────────────
DELETE FROM profile_keyword_category;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Product Management', 33, 1575, 14.7,
    ARRAY['product strategy','product owner','product management','stakeholder management','go-to-market','product roadmap','product lifecycle','product manager','gtm','user stories']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Fintech', 14, 1225, 17.5,
    ARRAY['fintech','credit','financial services','bnpl','digital banking','financial infrastructure','financial inclusion','lending','open banking','buy now pay later']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Payments', 69, 12289, 24.3,
    ARRAY['payment','payments','settlement','merchant','reconciliation','cross-border','card','mastercard','fx','gtv']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Compliance', 24, 3395, 21.3,
    ARRAY['compliance','regulatory','iso 27001','audit','aml','cft','kyc','central bank','audit trail','auditor']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Agile', 19, 1940, 21.7,
    ARRAY['agile','scrum','acp','cspo','csm','sprint','velocity','lean','sprint planning','safe']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'PMO', 19, 1199, 15.4,
    ARRAY['pmo','pmp','project management office','pmbok','program management','raid','ms project','milestone','portfolio management','steering committee']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Technical', 35, 993, 10.7,
    ARRAY['api','jira','confluence','kafka','apis','system design','event-driven','swagger','openapi','distributed systems']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Leadership', 34, 2335, 17.3,
    ARRAY['leadership','chief','head of','cfo','executive','cpo','leader','vp','board','hiring']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Analytics', 28, 893, 11.5,
    ARRAY['analytics','r','churn','bi','data analytics','dashboard','ltv','tableau','experimentation','dau']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'AI', 19, 522, 12.2,
    ARRAY['ai','genai','agentic','llm','openai','langchain','claude','ml','rag','machine learning']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    'Risk & Security', 16, 390, 10.6,
    ARRAY['risk management','fraud detection','operational risk','risk mitigation','credit risk','fraud prevention','cybersecurity','incident response','privacy','transaction monitoring']::TEXT[]
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;

-- ── seed: profile_source_document ────────────────────────────────────────
DELETE FROM profile_source_document;

INSERT INTO profile_source_document (file_hash, file_path, file_name, document_class, char_count, file_size, source_mtime, is_duplicate) VALUES
('d085be561b49cbb0', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Rizwan_Zafar_Resume_MAI.docx', 'Rizwan_Zafar_Resume_MAI.docx', 'role_specific_resume', 7825, 23281, '2026-04-05T17:42:53.776908+00:00', FALSE),
('2f9774f925447470', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Founding_CPO_B2B_Aviation_v2.docx', 'Rizwan_Zafar_Founding_CPO_B2B_Aviation_v2.docx', 'role_specific_resume', 7244, 18843, '2026-03-31T19:43:04.992275+00:00', FALSE),
('11e0db67c9163a69', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Tabby_Senior_PM_CORRECTED.docx', 'Rizwan_Zafar_Tabby_Senior_PM_CORRECTED.docx', 'role_specific_resume', 7508, 10789, '2026-03-31T19:43:05.038385+00:00', FALSE),
('47cf64fe719722e5', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar.docx', 'Rizwan_Zafar.docx', 'role_specific_resume', 6396, 17784, '2026-03-31T19:43:04.882373+00:00', FALSE),
('7bede043499415b8', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Tabby_Senior_PM.docx', 'Rizwan_Zafar_Tabby_Senior_PM.docx', 'role_specific_resume', 6613, 17474, '2026-03-31T19:43:05.035631+00:00', FALSE),
('93c07e4cf86c21d6', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Executive_Resume.docx.pdf', 'Rizwan_Zafar_Executive_Resume.docx.pdf', 'executive_resume', 10208, 127235, '2026-03-31T19:43:04.983909+00:00', FALSE),
('cb7b8c14d4540393', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Program_Project_Manager_Resume_2025.docx', 'Rizwan_Zafar_Program_Project_Manager_Resume_2025.docx', 'role_specific_resume', 6354, 19289, '2026-04-03T09:31:02.205825+00:00', FALSE),
('bf665264eabfacbb', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_TOP_1_PERCENT_Resume_1.docx', 'Rizwan_Zafar_TOP_1_PERCENT_Resume_1.docx', 'role_specific_resume', 15971, 14364, '2026-03-31T19:43:05.032209+00:00', FALSE),
('ca9fee084b1a3e7d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Resume_AXIAN_CPO.docx', 'Rizwan_Zafar_Resume_AXIAN_CPO.docx', 'role_specific_resume', 13100, 14398, '2026-03-31T19:43:05.006659+00:00', FALSE),
('2f1da8650a8d1387', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Director_SME_Resume.docx', 'Rizwan_Zafar_Director_SME_Resume.docx', 'role_specific_resume', 7800, 18672, '2026-03-31T19:43:04.886075+00:00', FALSE),
('0028afd5968f4b36', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Hala_KSA_Final.docx', 'Rizwan_Zafar_Hala_KSA_Final.docx', 'role_specific_resume', 9408, 11431, '2026-03-31T19:43:04.995450+00:00', FALSE),
('35dcfd5c5765fc4b', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Executive_Resume.docx', 'Rizwan_Zafar_Executive_Resume.docx', 'executive_resume', 7407, 2669092, '2026-03-31T19:43:04.960207+00:00', FALSE),
('27a6d29f490bed6e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Senior_PM_Optimized.docx', 'Rizwan_Zafar_Senior_PM_Optimized.docx', 'role_specific_resume', 7614, 39859, '2026-03-31T19:43:05.024908+00:00', FALSE),
('11a7a6bed2c2bc6a', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Product_Manager_Resume_2025.docx', 'Rizwan_Zafar_Product_Manager_Resume_2025.docx', 'role_specific_resume', 5502, 18220, '2026-04-03T08:53:14.425503+00:00', FALSE),
('41b3cdcc82bed24d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Senior_PM.docx', 'Rizwan_Zafar_Senior_PM.docx', 'role_specific_resume', 7616, 30187, '2026-03-31T19:43:05.016352+00:00', FALSE),
('66c6ac5f2222b129', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Product_Manager_Resume.docx', 'Rizwan_Zafar_Product_Manager_Resume.docx', 'role_specific_resume', 11148, 41270, '2026-03-31T19:43:05.003696+00:00', FALSE),
('9639e862cd698741', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Resume_CPO.docx', 'Rizwan_Zafar_Resume_CPO.docx', 'role_specific_resume', 10952, 22241, '2026-03-31T19:43:05.096661+00:00', FALSE),
('e6b3883db405322c', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Head_of_Product_FinTech_Riyadh.docx', 'Rizwan_Zafar_Head_of_Product_FinTech_Riyadh.docx', 'role_specific_resume', 7372, 11721, '2026-03-31T19:43:04.998614+00:00', FALSE),
('216566b24c71d466', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Resumes_General/Rizwan_Zafar_Salla_Product_Manager.docx', 'Rizwan_Zafar_Salla_Product_Manager.docx', 'role_specific_resume', 11521, 41387, '2026-03-31T19:43:05.011550+00:00', FALSE),
('53635730b1e87a49', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_TPM_ATS.docx', 'Rizwan_Zafar_Visa_TPM_ATS.docx', 'role_specific_resume', 6065, 10975, '2026-03-31T19:43:04.838568+00:00', FALSE),
('ec114bae6cdac8cd', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_TPM_COMPREHENSIVE.docx', 'Rizwan_Zafar_Visa_TPM_COMPREHENSIVE.docx', 'role_specific_resume', 12084, 13099, '2026-03-31T19:43:04.851727+00:00', FALSE),
('f91166cdc48f2271', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_TPM.pdf', 'Rizwan_Zafar_Visa_TPM.pdf', 'role_specific_resume', 10250, 65471, '2026-03-31T19:43:04.832567+00:00', FALSE),
('efdc1ab20c7faeb3', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Visa_TPM_Interview_Prep_Guide.docx', 'Visa_TPM_Interview_Prep_Guide.docx', 'interview_prep', 23432, 23830, '2026-03-31T19:43:04.875464+00:00', FALSE),
('8258dee3321d24a1', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_TPM_v2.docx', 'Rizwan_Zafar_Visa_TPM_v2.docx', 'role_specific_resume', 9980, 19449, '2026-03-31T19:43:04.861561+00:00', FALSE),
('d667ea7217f7314a', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_TPM_ATS.pdf', 'Rizwan_Zafar_Visa_TPM_ATS.pdf', 'role_specific_resume', 6281, 58184, '2026-03-31T19:43:04.845759+00:00', FALSE),
('8d35a02abf9c4b46', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_Cover_Letter.docx', 'Rizwan_Zafar_Visa_Cover_Letter.docx', 'cover_letter', 4539, 10023, '2026-03-31T19:43:04.824272+00:00', FALSE),
('05d40e8b4f384479', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Visa/Rizwan_Zafar_Visa_TPM_Resume.docx', 'Rizwan_Zafar_Visa_TPM_Resume.docx', 'role_specific_resume', 7330, 11282, '2026-03-31T19:43:04.857213+00:00', FALSE),
('df527930ed881b58', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_2026_Strategic_Product_Roadmap.docx', 'SimPaisa_2026_Strategic_Product_Roadmap.docx', 'simpaisa_org_doc', 39134, 36768, '2026-03-31T19:43:13.962731+00:00', FALSE),
('034d9d919d49288c', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Organizational_Blueprint_v3.docx', 'SimPaisa_Organizational_Blueprint_v3.docx', 'simpaisa_org_doc', 21765, 24807, '2026-03-31T19:43:14.331981+00:00', FALSE),
('982129b5a3698708', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SIMPAISA.docx', 'SIMPAISA.docx', 'simpaisa_org_doc', 106422, 136252, '2026-03-31T19:43:14.342789+00:00', FALSE),
('726ba4fc7bec5a7a', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Complete_Organizational_Blueprint_v4.docx', 'SimPaisa_Complete_Organizational_Blueprint_v4.docx', 'simpaisa_org_doc', 34342, 38190, '2026-03-31T19:43:14.349016+00:00', FALSE),
('91590e7c946ad627', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Complete_Blueprint_v6_FINAL.docx', 'SimPaisa_Complete_Blueprint_v6_FINAL.docx', 'simpaisa_org_doc', 99715, 105527, '2026-03-31T19:43:14.354763+00:00', FALSE),
('9741320c73424869', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_OrgDesign_JobDescriptions.docx', 'SimPaisa_OrgDesign_JobDescriptions.docx', 'simpaisa_org_doc', 30563, 46790, '2026-03-31T19:43:14.359991+00:00', FALSE),
('05a8dc276c09c89b', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Blueprint_v6_Part1.docx', 'SimPaisa_Blueprint_v6_Part1.docx', 'simpaisa_org_doc', 25614, 28300, '2026-03-31T19:43:14.364149+00:00', FALSE),
('7e1de4696187471a', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Comprehensive_Operating_Model_v2.docx', 'SimPaisa_Comprehensive_Operating_Model_v2.docx', 'simpaisa_org_doc', 17713, 45921, '2026-03-31T19:43:14.372144+00:00', FALSE),
('f11eba650dee481a', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Complete_Implementation_Blueprint_v5.docx', 'SimPaisa_Complete_Implementation_Blueprint_v5.docx', 'simpaisa_org_doc', 35524, 39741, '2026-03-31T19:43:14.377445+00:00', FALSE),
('3163cfc0b8c4a6cc', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/SimPaisa/SimPaisa_Strategic_Transformation_Plan_2026.docx', 'SimPaisa_Strategic_Transformation_Plan_2026.docx', 'simpaisa_org_doc', 17976, 33781, '2026-03-31T19:43:14.382920+00:00', FALSE),
('28c9588d5d37c1c1', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_Resume.docx', 'Rizwan_Zafar_Mastercard_Resume.docx', 'role_specific_resume', 7646, 18848, '2026-03-31T19:43:04.757907+00:00', FALSE),
('e530dbaf1a2fceea', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan Zafar Resume.pdf', 'Rizwan Zafar Resume.pdf', 'role_specific_resume', 7483, 111186, '2026-03-31T19:43:05.123685+00:00', FALSE),
('a7fbc34cb7e6311d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_v2_2.docx', 'Rizwan_Zafar_Mastercard_v2_2.docx', 'role_specific_resume', 7896, 11683, '2026-03-31T19:43:04.787000+00:00', FALSE),
('a3df390e5e45bdb6', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_TPM.pdf', 'Rizwan_Zafar_TPM.pdf', 'role_specific_resume', 10250, 65471, '2026-03-31T19:43:04.815677+00:00', FALSE),
('c13188d761c0810e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Mastercard_Complete_Prep_Package.docx', 'Mastercard_Complete_Prep_Package.docx', 'interview_prep', 12458, 15432, '2026-03-31T19:43:04.717491+00:00', FALSE),
('a0837fe8e882a3b8', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_Resume_v2.docx', 'Rizwan_Zafar_Mastercard_Resume_v2.docx', 'role_specific_resume', 7317, 11413, '2026-03-31T19:43:04.763665+00:00', FALSE),
('017a4358c6a71e3f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_Resume_v3.docx', 'Rizwan_Zafar_Mastercard_Resume_v3.docx', 'role_specific_resume', 8568, 11959, '2026-03-31T19:43:04.770025+00:00', FALSE),
('0ebd58e4b304ca07', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_MCPO.pdf', 'Rizwan_Zafar_MCPO.pdf', 'role_specific_resume', 8268, 70659, '2026-03-31T19:43:05.065880+00:00', FALSE),
('66092e1a5778ee14', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_Advisors_ATS.docx', 'Rizwan_Zafar_Mastercard_Advisors_ATS.docx', 'role_specific_resume', 6395, 10880, '2026-03-31T19:43:04.745436+00:00', FALSE),
('e3d7b66c86f39b58', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_MCPO.docx', 'Rizwan_Zafar_MCPO.docx', 'role_specific_resume', 8030, 19648, '2026-03-31T19:43:05.059855+00:00', FALSE),
('b2b3321732078f35', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan Zafar Resume.docx', 'Rizwan Zafar Resume.docx', 'role_specific_resume', 7379, 24616, '2026-03-31T19:43:05.113999+00:00', FALSE),
('def74b677ba3a09e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_MDES_Product_Manager_Mastercard.docx', 'Rizwan_Zafar_MDES_Product_Manager_Mastercard.docx', 'role_specific_resume', 7540, 19274, '2026-03-31T19:43:04.723570+00:00', FALSE),
('50f3de5507c0b925', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_AUTHENTIC.docx', 'Rizwan_Zafar_Mastercard_AUTHENTIC.docx', 'role_specific_resume', 6791, 10475, '2026-03-31T19:43:04.736683+00:00', FALSE)
ON CONFLICT (file_hash) DO NOTHING;

INSERT INTO profile_source_document (file_hash, file_path, file_name, document_class, char_count, file_size, source_mtime, is_duplicate) VALUES
('0e9eb5a507f1207d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Mastercard_GAPFILLED.docx', 'Rizwan_Zafar_Mastercard_GAPFILLED.docx', 'role_specific_resume', 7808, 11655, '2026-03-31T19:43:04.751910+00:00', FALSE),
('9747f20e82a0f459', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/MC/Rizwan_Zafar_Resume_Mastercard.docx', 'Rizwan_Zafar_Resume_Mastercard.docx', 'role_specific_resume', 8699, 31951, '2026-03-31T19:43:04.793833+00:00', FALSE),
('2a008584d2e67677', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_Wise_ATS.docx', 'Rizwan_Zafar_Wise_ATS.docx', 'role_specific_resume', 7773, 12463, '2026-03-31T19:43:05.199569+00:00', FALSE),
('f2bebe976d2ef55c', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_Wise_Premium.docx', 'Rizwan_Zafar_Wise_Premium.docx', 'role_specific_resume', 7489, 12527, '2026-03-31T19:43:05.205105+00:00', FALSE),
('05b0958abc3f9d04', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_FINAL_v2.docx', 'Rizwan_Zafar_FINAL_v2.docx', 'role_specific_resume', 9055, 13256, '2026-03-31T19:43:05.192869+00:00', FALSE),
('3271ad58b07a3c9d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_Clean.docx', 'Rizwan_Zafar_Clean.docx', 'role_specific_resume', 7803, 16468, '2026-03-31T19:43:05.214981+00:00', FALSE),
('14e3003996763f0e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_Wise_Analysis.docx', 'Rizwan_Zafar_Wise_Analysis.docx', 'role_specific_resume', 10504, 14505, '2026-03-31T19:43:05.209068+00:00', FALSE),
('65394d5e7907cfac', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_FINAL_Wise.docx', 'Rizwan_Zafar_FINAL_Wise.docx', 'role_specific_resume', 8435, 12669, '2026-03-31T19:43:05.181309+00:00', FALSE),
('8b31a543bd3a1fa1', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Job_Specific/Rizwan_Zafar_.docx', 'Rizwan_Zafar_.docx', 'role_specific_resume', 7803, 18873, '2026-03-31T19:43:05.222414+00:00', FALSE),
('c1db3f75bc95c60f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/mastercard_hr_question_bank.docx', 'mastercard_hr_question_bank.docx', 'role_specific_resume', 28547, 24723, '2026-03-31T19:43:14.534349+00:00', FALSE),
('9cce238b7b21c8e2', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/mastercard_interview_complete_guide.docx', 'mastercard_interview_complete_guide.docx', 'role_specific_resume', 141902, 65134, '2026-03-31T19:43:14.544785+00:00', FALSE),
('41b0927f4b7d24a2', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/Mastercard_Interview_Preparation_Guide.md', 'Mastercard_Interview_Preparation_Guide.md', 'interview_prep', 33040, 33132, '2026-03-31T19:43:14.550914+00:00', FALSE),
('5bdda032d4a311fe', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/mastercard_interview_prep.md', 'mastercard_interview_prep.md', 'interview_prep', 28753, 29357, '2026-03-31T19:43:14.557763+00:00', FALSE),
('58518344b8783770', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/FINAL_5_NeilMeyer_Assessment_3DayPlan_PodcastScript.docx', 'FINAL_5_NeilMeyer_Assessment_3DayPlan_PodcastScript.docx', 'company_assessment', 20975, 20008, '2026-03-31T19:43:14.562755+00:00', FALSE),
('a92c2b73e2e7a2ca', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/Complete_Product_Management_Guide.docx', 'Complete_Product_Management_Guide.docx', 'role_specific_resume', 36773, 30780, '2026-03-31T19:43:14.576907+00:00', FALSE),
('e285923cb5adc878', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/Mastercard_Referral_Messages_v3.docx', 'Mastercard_Referral_Messages_v3.docx', 'role_specific_resume', 6175, 10903, '2026-03-31T19:43:14.586785+00:00', FALSE),
('f9a9185e134cc402', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/FINAL_2_Concepts_Encyclopedia_UseCases.docx', 'FINAL_2_Concepts_Encyclopedia_UseCases.docx', 'role_specific_resume', 121559, 54139, '2026-03-31T19:43:14.593398+00:00', FALSE),
('a400312deabfd056', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/FAQs+Cheat+Sheet.pdf', 'FAQs+Cheat+Sheet.pdf', 'role_specific_resume', 5753, 2426158, '2026-03-31T19:43:14.662253+00:00', FALSE),
('471568c147fdbb6e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/FINAL_1_Complete_QA_Bank_85Questions.docx', 'FINAL_1_Complete_QA_Bank_85Questions.docx', 'interview_prep', 77366, 41713, '2026-03-31T19:43:14.689103+00:00', FALSE),
('40b69f4466a07a8e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/Lecture+Summary.pdf', 'Lecture+Summary.pdf', 'role_specific_resume', 39265, 774306, '2026-03-31T19:43:14.713279+00:00', FALSE),
('49be9d18ee99d559', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Resume/Interview_Prep/Wise_Vol6_GapTable_Podcast_Flashcards.docx', 'Wise_Vol6_GapTable_Podcast_Flashcards.docx', 'role_specific_resume', 30886, 27480, '2026-03-31T19:43:14.719814+00:00', FALSE),
('3564240dbccd9d19', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_ResumeMVPC.docx', 'Rizwan_Zafar_ResumeMVPC.docx', 'role_specific_resume', 6734, 23134, '2026-04-03T23:02:09.796618+00:00', FALSE),
('06ceecb2ea7dad1d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Talabat_Business_Analyst.docx', 'Rizwan_Zafar_Resume_Talabat_Business_Analyst.docx', 'role_specific_resume', 8428, 22867, '2026-04-09T09:28:42.796650+00:00', FALSE),
('6d2a2b7a4b8a0144', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_MC_Analytics_AI_Director.docx', 'Rizwan_Zafar_Resume_MC_Analytics_AI_Director.docx', 'role_specific_resume', 7960, 22949, '2026-04-16T22:40:09.516927+00:00', FALSE),
('80776b1a8d241b4c', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Mastercard_AI_Consultant_Assessment.md', 'Mastercard_AI_Consultant_Assessment.md', 'company_assessment', 6914, 6928, '2026-04-05T14:42:13.494032+00:00', FALSE),
('7b53f5373b9e2a8f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Reap_Application_Answers_Both.md', 'Reap_Application_Answers_Both.md', 'application_answer', 8109, 8139, '2026-04-10T10:47:29.155837+00:00', FALSE),
('68a3bfe4633d7a08', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_MC_Product_Portfolio.docx', 'Rizwan_Zafar_Resume_MC_Product_Portfolio.docx', 'role_specific_resume', 7547, 13859, '2026-04-10T23:24:09.591105+00:00', FALSE),
('1e12fe901d74957e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Optimization_Final_Rizwan_Zafar.docx', 'LinkedIn_Optimization_Final_Rizwan_Zafar.docx', 'linkedin_optimization', 34368, 42575, '2026-03-25T13:12:22.022356+00:00', FALSE),
('b9f9aae688dc3bd3', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Visa_Direct_v3.docx', 'Rizwan_Zafar_Resume_Visa_Direct_v3.docx', 'role_specific_resume', 11286, 15182, '2026-04-17T15:50:57.629288+00:00', FALSE),
('675a76039299f2f2', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_MC_Services_BD_NonBank.docx', 'Rizwan_Zafar_Resume_MC_Services_BD_NonBank.docx', 'role_specific_resume', 7590, 13726, '2026-04-10T23:07:48.589176+00:00', FALSE),
('228c4dda19844b13', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Worldpay_Solution_Consulting.docx', 'Rizwan_Zafar_Resume_Worldpay_Solution_Consulting.docx', 'role_specific_resume', 9151, 15468, '2026-03-31T15:59:09.236130+00:00', FALSE),
('2acf43754640fea6', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-March-16-2026.html', 'LinkedIn-Post-March-16-2026.html', 'linkedin_post', 4381, 8277, '2026-03-16T04:06:17.820695+00:00', FALSE),
('edf082023efb98f0', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Mastercard_VP_Commerce_Media.docx', 'Rizwan_Zafar_Resume_Mastercard_VP_Commerce_Media.docx', 'role_specific_resume', 6734, 23123, '2026-04-03T23:01:36.164096+00:00', FALSE),
('7cbe34080c7ef248', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Audit_Report_V2_Rizwan_Zafar.docx', 'LinkedIn_Audit_Report_V2_Rizwan_Zafar.docx', 'linkedin_audit', 47888, 41190, '2026-03-23T14:53:36.188327+00:00', FALSE),
('82617ec7b7aa8028', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Post_March_12_2026.html', 'LinkedIn_Post_March_12_2026.html', 'linkedin_post', 3594, 5576, '2026-03-12T18:35:26.897054+00:00', FALSE),
('7f2c9d405200a865', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Agile_Product_Owner_Digital_Cards.docx', 'Rizwan_Zafar_Resume_Agile_Product_Owner_Digital_Cards.docx', 'role_specific_resume', 9296, 14660, '2026-04-09T07:15:22.658702+00:00', FALSE),
('47da22e2d31a3f69', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_MC_GenAI_Consulting.docx', 'Rizwan_Zafar_Resume_MC_GenAI_Consulting.docx', 'role_specific_resume', 6827, 22553, '2026-04-12T12:44:53.514592+00:00', FALSE),
('ef67e05d974e754d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Full_Optimization_Rizwan_Zafar.docx', 'LinkedIn_Full_Optimization_Rizwan_Zafar.docx', 'linkedin_optimization', 50779, 40672, '2026-03-23T17:46:22.552630+00:00', FALSE),
('cceca75e29df1e14', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Reap_Application_Answer_Payments_Flow.md', 'Reap_Application_Answer_Payments_Flow.md', 'application_answer', 4382, 4396, '2026-04-10T10:46:07.944559+00:00', FALSE),
('6692de59917f963e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Visa_VCA_Digital.docx', 'Rizwan_Zafar_Resume_Visa_VCA_Digital.docx', 'role_specific_resume', 7002, 21996, '2026-04-16T22:16:58.783070+00:00', FALSE),
('b283c1f226ee4f19', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-March-10-2026.html', 'LinkedIn-Post-March-10-2026.html', 'linkedin_post', 4040, 6155, '2026-03-10T04:03:48.507953+00:00', FALSE),
('4d06577206d1ef7d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Mastercard_AI_Consultant.docx', 'Rizwan_Zafar_Resume_Mastercard_AI_Consultant.docx', 'role_specific_resume', 8190, 14104, '2026-04-05T14:41:24.555301+00:00', FALSE),
('1767126b4dece94d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Revolut_Head_of_Product.docx', 'Rizwan_Zafar_Resume_Revolut_Head_of_Product.docx', 'role_specific_resume', 7688, 22692, '2026-04-09T11:03:26.402827+00:00', FALSE),
('1c34c65064a88a2d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Executive_Resume.docx', 'Rizwan_Zafar_Executive_Resume.docx', 'executive_resume', 9448, 15829, '2026-03-30T11:57:01.000798+00:00', FALSE),
('a0a9bb78b41a6b61', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Headline_About_Final_Rizwan_Zafar.docx', 'LinkedIn_Headline_About_Final_Rizwan_Zafar.docx', 'linkedin_headline_about', 21179, 21476, '2026-03-23T16:54:47.548053+00:00', FALSE),
('c00b6638c5a3e70e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Mastercard_AI_Consultant_V2.docx', 'Rizwan_Zafar_Resume_Mastercard_AI_Consultant_V2.docx', 'role_specific_resume', 9224, 23512, '2026-04-06T12:56:56.217398+00:00', FALSE),
('c18edbc49d53f416', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_StanChart_Digital_Banking.docx', 'Rizwan_Zafar_Resume_StanChart_Digital_Banking.docx', 'role_specific_resume', 8706, 15391, '2026-03-31T15:59:13.401158+00:00', FALSE),
('7ee0f9cc23be6d17', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-Agentic-Commerce-March-11-2026.html', 'LinkedIn-Post-Agentic-Commerce-March-11-2026.html', 'linkedin_post', 3437, 5596, '2026-03-11T04:03:03.142908+00:00', FALSE),
('25bd6e043f8e2a5b', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-March-15-2026.html', 'LinkedIn-Post-March-15-2026.html', 'linkedin_post', 3827, 7741, '2026-03-15T04:07:25.293070+00:00', FALSE),
('5050e3c028ccd7a4', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Reap_Application_Answer_Payments_Product.md', 'Reap_Application_Answer_Payments_Product.md', 'application_answer', 3317, 3331, '2026-04-10T10:38:12.288802+00:00', FALSE)
ON CONFLICT (file_hash) DO NOTHING;

INSERT INTO profile_source_document (file_hash, file_path, file_name, document_class, char_count, file_size, source_mtime, is_duplicate) VALUES
('d7c7505c74fa7af5', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/SimPaisa_OrgDesign_JobDescriptions.docx', 'SimPaisa_OrgDesign_JobDescriptions.docx', 'simpaisa_org_doc', 30318, 46678, '2026-04-23T22:33:36.869987+00:00', FALSE),
('33277fd52ffd04e6', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Post_Package_2026-03-24.md', 'LinkedIn_Post_Package_2026-03-24.md', 'linkedin_post', 8803, 8833, '2026-03-23T21:56:04.135803+00:00', FALSE),
('c22aed157c52eebb', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Mastercard_AI_Consultant_V3.docx', 'Rizwan_Zafar_Resume_Mastercard_AI_Consultant_V3.docx', 'role_specific_resume', 7829, 23288, '2026-04-05T17:41:11.816523+00:00', FALSE),
('bf983e0a2c50ff22', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Post_March_17_2026.html', 'LinkedIn_Post_March_17_2026.html', 'linkedin_post', 4077, 6174, '2026-03-17T11:33:46.230695+00:00', FALSE),
('0d272f63a9abc816', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Reap_Application_Answers_100w.md', 'Reap_Application_Answers_100w.md', 'application_answer', 1642, 1650, '2026-04-10T10:57:38.938989+00:00', FALSE),
('9c0fe5595a00da45', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_V2.docx', 'Rizwan_Zafar_Resume_V2.docx', 'role_specific_resume', 7002, 22000, '2026-04-16T22:17:38.263155+00:00', FALSE),
('f0dbcfc4096c5def', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_myZoi_Senior_Program_Manager.docx', 'Rizwan_Zafar_Resume_myZoi_Senior_Program_Manager.docx', 'role_specific_resume', 8443, 23556, '2026-04-09T10:22:54.655133+00:00', FALSE),
('b894ae129460c26f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Tabby_Senior_PM.docx', 'Rizwan_Zafar_Resume_Tabby_Senior_PM.docx', 'role_specific_resume', 7410, 22686, '2026-04-12T12:53:26.530707+00:00', FALSE),
('060e039d59734b97', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Mastercard_Strategy.docx', 'Rizwan_Zafar_Resume_Mastercard_Strategy.docx', 'role_specific_resume', 9186, 15529, '2026-03-31T15:59:02.258169+00:00', FALSE),
('cdd2ad1f0b40b490', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Target_Roles_Deep_Analysis.md', 'Target_Roles_Deep_Analysis.md', 'target_roles', 14475, 14549, '2026-03-30T19:12:45.466492+00:00', FALSE),
('1ff4b2c620c68354', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_HOP.docx', 'Rizwan_Zafar_HOP.docx', 'role_specific_resume', 8181, 23202, '2026-04-17T13:17:54.225309+00:00', FALSE),
('43adf2b575cf5830', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_TerraPay_Head_Payments.docx', 'Rizwan_Zafar_TerraPay_Head_Payments.docx', 'role_specific_resume', 4450, 11149, '2026-03-29T14:39:22.508694+00:00', FALSE),
('8a4fa8df8f07a019', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Master_CV.md', 'Rizwan_Zafar_Master_CV.md', 'master_cv', 16051, 16125, '2026-04-26T12:29:15.287850+00:00', FALSE),
('83c082ff2143297c', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-Mar-13-2026.html', 'LinkedIn-Post-Mar-13-2026.html', 'linkedin_post', 3111, 4660, '2026-03-13T06:57:36.397791+00:00', FALSE),
('11701b33c910d81f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_CPO_Fintech_Payments.docx', 'Rizwan_Zafar_CPO_Fintech_Payments.docx', 'role_specific_resume', 3700, 10947, '2026-03-29T14:39:30.583203+00:00', FALSE),
('72a7357aac79b2fb', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Bloomberg_VP_Product.docx', 'Rizwan_Zafar_Bloomberg_VP_Product.docx', 'role_specific_resume', 3231, 10446, '2026-03-29T14:39:41.306086+00:00', FALSE),
('97f24a48341ce981', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Recruiter_Analysis_March2026.md', 'Recruiter_Analysis_March2026.md', 'recruiter_analysis', 16084, 16436, '2026-03-29T15:14:50.295959+00:00', FALSE),
('48564f2ff3c1fe3f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Visa_Direct.docx', 'Rizwan_Zafar_Resume_Visa_Direct.docx', 'role_specific_resume', 10596, 14956, '2026-04-16T21:59:33.247440+00:00', FALSE),
('4998f4c99ae5152e', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Fintech_LinkedIn_Articles_Rizwan_Zafar.docx', 'Fintech_LinkedIn_Articles_Rizwan_Zafar.docx', 'linkedin_article', 57566, 45353, '2026-03-23T23:04:29.574021+00:00', FALSE),
('b162c85024eb9082', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Post_2026-03-23.md', 'LinkedIn_Post_2026-03-23.md', 'linkedin_post', 4766, 4776, '2026-03-23T06:29:57.595036+00:00', FALSE),
('b4e603eab80b6698', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-Stablecoin-Infrastructure-Mar10.html', 'LinkedIn-Post-Stablecoin-Infrastructure-Mar10.html', 'linkedin_post', 3157, 4904, '2026-03-09T19:41:53.593161+00:00', FALSE),
('41802406f72dd649', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Cover_Letter_Reap_Payments.docx', 'Rizwan_Zafar_Cover_Letter_Reap_Payments.docx', 'cover_letter', 2697, 10164, '2026-04-10T11:05:09.694364+00:00', FALSE),
('bdbe0eb9bf5d599f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn-Post-March-14-2026.html', 'LinkedIn-Post-March-14-2026.html', 'linkedin_post', 3742, 8555, '2026-03-14T15:23:32.701199+00:00', FALSE),
('1308438cf04ef211', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Top1_Final_Rizwan_Zafar.docx', 'LinkedIn_Top1_Final_Rizwan_Zafar.docx', 'linkedin_optimization', 24883, 37147, '2026-03-25T13:12:21.979566+00:00', FALSE),
('a099c43efcd3d542', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Manager_PO_Digital_Transformation.docx', 'Rizwan_Zafar_Resume_Manager_PO_Digital_Transformation.docx', 'role_specific_resume', 9659, 23290, '2026-04-09T10:10:45.192440+00:00', FALSE),
('4e66de56178de1d0', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Reap_Payments_Product.docx', 'Rizwan_Zafar_Resume_Reap_Payments_Product.docx', 'role_specific_resume', 8319, 22936, '2026-04-10T10:34:13.587455+00:00', FALSE),
('5cab561aaea9166b', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Mastercard_VP_Commerce_Media_Deep_Assessment.md', 'Mastercard_VP_Commerce_Media_Deep_Assessment.md', 'company_assessment', 15418, 15418, '2026-04-03T21:39:44.446440+00:00', FALSE),
('4da6e845c79bfb95', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Post_2026-03-22.md', 'LinkedIn_Post_2026-03-22.md', 'linkedin_post', 4999, 5041, '2026-03-22T04:41:51.023170+00:00', FALSE),
('f7857eb641f59639', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_Head_of_Projects.docx', 'Rizwan_Zafar_Resume_Head_of_Projects.docx', 'role_specific_resume', 8181, 23203, '2026-04-17T13:17:40.355172+00:00', FALSE),
('8589548943c0990d', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Audit_V3_Rizwan_Zafar.docx', 'LinkedIn_Audit_V3_Rizwan_Zafar.docx', 'linkedin_audit', 36638, 35187, '2026-03-23T15:10:47.912690+00:00', FALSE),
('2e486f4193ad249c', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/LinkedIn_Audit_Report_Rizwan_Zafar.docx', 'LinkedIn_Audit_Report_Rizwan_Zafar.docx', 'linkedin_audit', 47960, 39567, '2026-03-23T14:13:21.788143+00:00', FALSE),
('eacaf78036d39b58', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Resume_MC.docx', 'Rizwan_Zafar_Resume_MC.docx', 'role_specific_resume', 7960, 22957, '2026-04-16T22:40:23.349191+00:00', FALSE),
('6fd7610c83833ee0', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/Rizwan_Zafar.docx', 'Rizwan_Zafar.docx', 'role_specific_resume', 7920, 19139, '2026-03-22T12:57:28.954864+00:00', FALSE),
('40781c8b7982805f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/Rizwan_Zafar_Interview_Prep.docx', 'Rizwan_Zafar_Interview_Prep.docx', 'interview_prep', 31016, 22146, '2026-03-20T21:53:53.920623+00:00', FALSE),
('e00c617687451be3', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/FINAL_1_Complete_QA_Bank_85Questions_1.docx', 'FINAL_1_Complete_QA_Bank_85Questions_1.docx', 'interview_prep', 77366, 57425, '2026-03-28T15:43:33.238606+00:00', FALSE),
('8fb6903c9905aff5', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/Rizwan_Zafar_Top5_Resume.docx', 'Rizwan_Zafar_Top5_Resume.docx', 'role_specific_resume', 10610, 13569, '2026-03-20T21:51:37.916747+00:00', FALSE),
('1fb11e7e31836dd7', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/Wise_Interview_MasterPrep_Rizwan.md', 'Wise_Interview_MasterPrep_Rizwan.md', 'interview_prep', 86038, 86514, '2026-04-13T18:28:59.434982+00:00', FALSE),
('4de40978f5805763', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/Rizwan_Zafar_Learning_Pack.docx', 'Rizwan_Zafar_Learning_Pack.docx', 'role_specific_resume', 32224, 23215, '2026-03-22T12:17:50.137105+00:00', FALSE),
('ae9ad8c2d2edcc76', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/Wise_Interview_MasterPrep_FINAL.docx', 'Wise_Interview_MasterPrep_FINAL.docx', 'interview_prep', 41543, 30962, '2026-04-13T20:01:04.007477+00:00', FALSE),
('d5b143d17842355f', '/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/wise/files/FINAL_3_CaseStudies_15Stories_MockQA.docx', 'FINAL_3_CaseStudies_15Stories_MockQA.docx', 'role_specific_resume', 51597, 35724, '2026-03-28T10:44:10+00:00', FALSE),
('a0b2cbe19299faf0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/rizwanzafar resume iqy.pdf', 'rizwanzafar resume iqy.pdf', 'role_specific_resume', 5746, 40183, '2025-06-30T21:42:44+00:00', FALSE),
('e00db846f97f6742', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan_Zafar_CV_.docx', 'Rizwan_Zafar_CV_.docx', 'role_specific_resume', 6126, 12591, '2026-05-07T05:01:50.776705+00:00', FALSE),
('32ff27ad4030d959', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan_Zafar_Visa_TPM_v2.pdf', 'Rizwan_Zafar_Visa_TPM_v2.pdf', 'role_specific_resume', 10250, 65471, '2026-01-13T14:34:51.664862+00:00', FALSE),
('424e940c7a2e06dc', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan_Zafar_TPM.pdf', 'Rizwan_Zafar_TPM.pdf', 'role_specific_resume', 10250, 65454, '2026-01-14T13:25:00.783938+00:00', FALSE),
('f2f192a6ba82dda0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan_Zafar_Resume_AXIAN_CPO.docx', 'Rizwan_Zafar_Resume_AXIAN_CPO.docx', 'role_specific_resume', 10952, 22237, '2025-11-18T06:29:03.796587+00:00', FALSE),
('4bd02e41533823bf', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar .pdf', 'Rizwan Zafar .pdf', 'role_specific_resume', 9373, 83850, '2024-03-29T02:08:47+00:00', FALSE),
('b07a1f899c2bf4a3', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar Resume Visa.docx', 'Rizwan Zafar Resume Visa.docx', 'role_specific_resume', 6662, 24878, '2025-11-08T12:26:08.394619+00:00', FALSE),
('e998bd7d5a69edc0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/rizwan zafar resume .docx', 'rizwan zafar resume .docx', 'role_specific_resume', 5670, 18112, '2024-06-26T22:49:55+00:00', FALSE),
('bff82a2c84db59f6', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar .docx', 'Rizwan Zafar .docx', 'role_specific_resume', 8118, 17199, '2024-05-13T11:20:36+00:00', FALSE),
('932a40d4d2560ed6', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Profile.pdf', 'Profile.pdf', 'role_specific_resume', 9796, 71536, '2024-11-03T22:02:03+00:00', FALSE)
ON CONFLICT (file_hash) DO NOTHING;

INSERT INTO profile_source_document (file_hash, file_path, file_name, document_class, char_count, file_size, source_mtime, is_duplicate) VALUES
('ff1bcf107c043df8', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Head-of-Product.docx', 'Head-of-Product.docx', 'role_specific_resume', 7532, 18007, '2024-05-13T21:07:28+00:00', FALSE),
('389d1531fad5f188', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar Resume D.docx', 'Rizwan Zafar Resume D.docx', 'role_specific_resume', 7381, 24643, '2025-10-21T05:27:44.910141+00:00', FALSE),
('dc6f9a1ebe91a06c', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/resume-document MPGS.docx', 'resume-document MPGS.docx', 'role_specific_resume', 5670, 18089, '2024-06-26T22:45:29+00:00', FALSE),
('db543e2f7d112b84', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard positioning .docx', 'Mastercard positioning .docx', 'role_specific_resume', 3578, 18574, '2024-11-24T15:27:49+00:00', FALSE),
('f51ef535cf3ff8e1', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/rizwanzafar resume B.pdf', 'rizwanzafar resume B.pdf', 'role_specific_resume', 10262, 51087, '2024-09-02T22:14:11+00:00', FALSE),
('ee22ec9dd6f35396', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar Resume Visa.pdf', 'Rizwan Zafar Resume Visa.pdf', 'role_specific_resume', 6803, 116269, '2025-11-08T12:28:06.133479+00:00', FALSE),
('51fa0b4173e559f7', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar Resume T&E.pdf', 'Rizwan Zafar Resume T&E.pdf', 'role_specific_resume', 6803, 116269, '2025-11-08T12:28:38.977462+00:00', FALSE),
('da238919aecaa3d6', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/rizwanzafar resume_ta.docx', 'rizwanzafar resume_ta.docx', 'role_specific_resume', 6963, 19887, '2024-11-29T01:18:25+00:00', FALSE),
('e2634c87c3508141', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/resume-MCP.pdf', 'resume-MCP.pdf', 'role_specific_resume', 6443, 77369, '2024-05-13T23:41:48+00:00', FALSE),
('7c13fcaba8362cfe', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Tell me about your self.docx', 'Tell me about your self.docx', 'role_specific_resume', 96072, 73480, '2024-11-24T15:23:39+00:00', FALSE),
('1f031ad9b0c4f1fa', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Rizwan Zafar Resume .pdf', 'Rizwan Zafar Resume .pdf', 'role_specific_resume', 6803, 116397, '2025-11-08T12:26:35.276272+00:00', FALSE),
('f237525f31df5dbf', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Tell me about your self.pdf', 'Tell me about your self.pdf', 'role_specific_resume', 34739, 131294, '2024-11-17T16:19:38+00:00', FALSE),
('dcdb7b69a4f6d03d', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/rizwanzafar resume_hala.docx', 'rizwanzafar resume_hala.docx', 'role_specific_resume', 8506, 20415, '2024-11-28T21:35:32+00:00', FALSE),
('01a46e37fd633217', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/untitled folder/Roadmap Q3-Q4-2024 - PayIns.pdf', 'Roadmap Q3-Q4-2024 - PayIns.pdf', 'role_specific_resume', 4494, 95049, '2024-09-22T12:15:09+00:00', FALSE),
('4ec1d7511ef177cb', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/untitled folder/Roadmap Q3-Q4-2024 - Portal.pdf', 'Roadmap Q3-Q4-2024 - Portal.pdf', 'role_specific_resume', 3261, 70251, '2024-09-22T12:15:02+00:00', FALSE),
('b450ce1e12ca2700', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/untitled folder/Roadmap Q3-Q4-2024 - PayOuts.pdf', 'Roadmap Q3-Q4-2024 - PayOuts.pdf', 'role_specific_resume', 3466, 79357, '2024-09-22T12:15:06+00:00', FALSE),
('ab2067d822afab45', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/bl/resume-document (2).pdf', 'resume-document (2).pdf', 'role_specific_resume', 7741, 79206, '2024-06-12T16:12:42+00:00', FALSE),
('31d9e56bda7d784a', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar pmo.docx', 'Rizwan zafar pmo.docx', 'role_specific_resume', 4968, 21616, '2025-05-11T20:35:44+00:00', FALSE),
('4809d7f897504afc', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar pmp .pdf', 'Rizwan zafar pmp .pdf', 'role_specific_resume', 5801, 34175, '2025-06-14T18:47:36+00:00', FALSE),
('f3481e7e49171601', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar pmp .docx', 'Rizwan zafar pmp .docx', 'role_specific_resume', 4811, 22787, '2025-06-14T18:47:21+00:00', FALSE),
('20c61d6a27396fc8', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_PMPBB.pdf', 'Rizwan zafar_PMPBB.pdf', 'role_specific_resume', 5696, 39388, '2025-05-17T22:55:54+00:00', FALSE),
('2e8686522b321666', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_MPMCL.pdf', 'Rizwan zafar_MPMCL.pdf', 'role_specific_resume', 5767, 34576, '2025-05-17T23:16:01+00:00', FALSE),
('38425e49690e25e9', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Simpaisa Integration Manual 3.0.3 (Collection).pdf', 'Simpaisa Integration Manual 3.0.3 (Collection).pdf', 'simpaisa_org_doc', 45557, 1409798, '2024-10-31T12:51:14+00:00', FALSE),
('acfb092a3fd09cea', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_PMBC.pdf', 'Rizwan zafar_PMBC.pdf', 'role_specific_resume', 6139, 33917, '2025-05-19T21:46:14+00:00', FALSE),
('2c5c4b710aace3b1', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_DAPM.pdf', 'Rizwan zafar_DAPM.pdf', 'role_specific_resume', 5855, 39565, '2025-05-17T22:14:17+00:00', FALSE),
('e37c8a303108808e', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_CPD.pdf', 'Rizwan zafar_CPD.pdf', 'role_specific_resume', 5495, 38840, '2025-05-17T22:50:32+00:00', FALSE),
('ac55eaa7d3789c69', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_cops.docx', 'Rizwan zafar_cops.docx', 'role_specific_resume', 4603, 22952, '2025-05-17T22:13:52+00:00', FALSE),
('0e6f4eb22e595028', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_SME.pdf', 'Rizwan zafar_SME.pdf', 'role_specific_resume', 6214, 40120, '2025-05-20T22:28:44+00:00', FALSE),
('5cf3f030806b8075', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_PMPBB.docx', 'Rizwan zafar_PMPBB.docx', 'role_specific_resume', 4570, 23804, '2025-05-17T22:55:44+00:00', FALSE),
('e7adaad8ddded758', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_mc.docx', 'Rizwan zafar_mc.docx', 'role_specific_resume', 4830, 23073, '2025-05-11T23:58:42+00:00', FALSE),
('589d6055ea3ebd11', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_lean.pdf', 'Rizwan zafar_lean.pdf', 'role_specific_resume', 6946, 40650, '2025-05-19T22:35:03+00:00', FALSE),
('a9ee54ad5ec84a7d', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_DAPM.docx', 'Rizwan zafar_DAPM.docx', 'role_specific_resume', 4603, 22954, '2025-05-17T22:14:08+00:00', FALSE),
('7fc1356bb67d72b2', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_al.docx', 'Rizwan zafar_al.docx', 'role_specific_resume', 4739, 22673, '2025-05-11T23:48:18+00:00', FALSE),
('240dfd2cd41dfa77', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_SME.docx', 'Rizwan zafar_SME.docx', 'role_specific_resume', 5180, 24745, '2025-05-20T22:28:39+00:00', FALSE),
('4db4f64c0d5a10c1', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_MPMCL.docx', 'Rizwan zafar_MPMCL.docx', 'role_specific_resume', 4806, 24458, '2025-05-17T23:15:55+00:00', FALSE),
('adb19e02cc59a105', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_po.docx', 'Rizwan zafar_po.docx', 'role_specific_resume', 4667, 22465, '2025-05-11T23:18:37+00:00', FALSE),
('69ef656d087fff4e', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_d360.pdf', 'Rizwan zafar_d360.pdf', 'role_specific_resume', 6109, 34243, '2025-05-19T21:55:40+00:00', FALSE),
('c893b6370d4fd037', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Technical SLA (3).docx', 'Technical SLA (3).docx', 'role_specific_resume', 9780, 41596, '2024-09-23T14:05:52+00:00', FALSE),
('38ee328e7a40a2e2', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/rizwanzafar resume.docx', 'rizwanzafar resume.docx', 'role_specific_resume', 8318, 20283, '2024-11-02T23:16:27+00:00', FALSE),
('5851a19a9cfa582c', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_d360.docx', 'Rizwan zafar_d360.docx', 'role_specific_resume', 4946, 23578, '2025-05-19T21:55:32+00:00', FALSE),
('e0f25a7770e7519a', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_CPD.docx', 'Rizwan zafar_CPD.docx', 'role_specific_resume', 4446, 23264, '2025-05-17T22:50:26+00:00', FALSE),
('b91aedaf827e1cd3', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Simpaisa APIs (CKO).pdf', 'Simpaisa APIs (CKO).pdf', 'simpaisa_org_doc', 6242, 200241, '2024-10-29T15:15:03+00:00', FALSE),
('fc13e49a8f66b87c', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Strategy day.docx', 'Strategy day.docx', 'role_specific_resume', 1306, 16513, '2024-09-26T01:15:41+00:00', FALSE),
('86e54b3616254d24', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_PMBC.docx', 'Rizwan zafar_PMBC.docx', 'role_specific_resume', 4910, 23370, '2025-05-19T21:46:09+00:00', FALSE),
('f7c638001770e761', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar_lean.docx', 'Rizwan zafar_lean.docx', 'role_specific_resume', 5880, 24501, '2025-05-19T22:34:55+00:00', FALSE),
('123ddb1de472ffec', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar ps.docx', 'Rizwan zafar ps.docx', 'role_specific_resume', 4667, 22461, '2025-05-11T23:17:59+00:00', FALSE),
('18f8b7fe78aaab22', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/Rizwan zafar product .docx', 'Rizwan zafar product .docx', 'role_specific_resume', 4589, 21983, '2025-05-11T23:06:44+00:00', FALSE),
('34e89f76fb2b8fac', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/Rizwan zafar pmo.docx', 'Rizwan zafar pmo.docx', 'role_specific_resume', 7256, 21995, '2025-05-09T02:24:10+00:00', FALSE),
('14718a7e29580e51', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/rizwanzafar resume pmc.docx', 'rizwanzafar resume pmc.docx', 'role_specific_resume', 5126, 19870, '2025-05-22T22:42:17+00:00', FALSE),
('a1d347aec5fc8aef', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/rizwanzafar_resume.docx', 'rizwanzafar_resume.docx', 'role_specific_resume', 7970, 22621, '2025-03-16T18:49:41+00:00', FALSE)
ON CONFLICT (file_hash) DO NOTHING;

INSERT INTO profile_source_document (file_hash, file_path, file_name, document_class, char_count, file_size, source_mtime, is_duplicate) VALUES
('c8e6ecce247f642d', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/rizwanzafar resume pmc.pdf', 'rizwanzafar resume pmc.pdf', 'role_specific_resume', 6119, 42644, '2025-05-22T22:42:23+00:00', FALSE),
('c72765cf718f467e', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/rizwanzafar resume.pdf', 'rizwanzafar resume.pdf', 'role_specific_resume', 9778, 47221, '2024-12-28T21:41:07+00:00', FALSE),
('24ef8837c52a3cd6', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/Doc2.docx', 'Doc2.docx', 'role_specific_resume', 1430, 16434, '2025-04-28T11:07:15+00:00', FALSE),
('daa7ac2626922a57', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/rizwanzafar resume.docx', 'rizwanzafar resume.docx', 'role_specific_resume', 8404, 21348, '2024-12-28T17:00:06+00:00', FALSE),
('d1d6652a0cc5d6c0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Mastercard/PM/rizwanzafar_resumePM.docx', 'rizwanzafar_resumePM.docx', 'role_specific_resume', 8570, 23085, '2025-04-27T20:37:06+00:00', FALSE),
('2afa41e65b0c7bb0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Network/rizwanzafar_resume.docx', 'rizwanzafar_resume.docx', 'role_specific_resume', 8381, 23122, '2025-03-23T02:24:05+00:00', FALSE),
('c9f77333d48aa018', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Network/rizwanzafar_resume.pdf', 'rizwanzafar_resume.pdf', 'role_specific_resume', 9765, 54789, '2025-03-23T02:24:12+00:00', FALSE),
('a072d91e01585e79', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Network/Cover letter.pdf', 'Cover letter.pdf', 'role_specific_resume', 2225, 19082, '2025-03-23T02:29:37+00:00', FALSE),
('a7656559fee0f5a0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/Network/Hiring Manager.docx', 'Hiring Manager.docx', 'role_specific_resume', 2225, 14805, '2025-03-23T02:29:41+00:00', FALSE),
('80ed1fde3423d0e9', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/resume1/Rizwan_Zafar_Custom_Resume.docx', 'Rizwan_Zafar_Custom_Resume.docx', 'role_specific_resume', 6232, 13827, '2025-08-14T20:33:09+00:00', FALSE),
('01241aa85925d2ed', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/resume1/Rizwan_Zafar_Resume.pdf', 'Rizwan_Zafar_Resume.pdf', 'role_specific_resume', 5267, 72423, '2025-08-14T21:07:45+00:00', FALSE),
('a9ee713a07dd2f46', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/HOP DM/Rizwan zafar resume .pdf', 'Rizwan zafar resume .pdf', 'role_specific_resume', 7798, 85269, '2024-05-13T22:40:50+00:00', FALSE),
('d6ff5d5a69437e50', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/visa/rizwanzafar resume.pdf', 'rizwanzafar resume.pdf', 'role_specific_resume', 6824, 57177, '2025-02-16T21:41:15+00:00', FALSE),
('4cd7b8e5ce0a35f8', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/visa/rizwanzafar resume.docx', 'rizwanzafar resume.docx', 'role_specific_resume', 5451, 20360, '2025-02-16T21:41:07+00:00', FALSE),
('cccebffab7daa08d', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/visa/Resume /Rizwan Zafar Resume.pdf', 'Rizwan Zafar Resume.pdf', 'role_specific_resume', 6976, 110126, '2025-09-14T16:38:47+00:00', FALSE),
('1a0509d132dc27eb', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/visa/Resume /simpaisa-testing guide 20250908[67].docx', 'simpaisa-testing guide 20250908[67].docx', 'simpaisa_org_doc', 707, 303981, '2025-09-15T15:12:00+00:00', FALSE),
('3be0b7cd02b1d093', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/visa/Resume /Rizwan Zafar Resume.docx', 'Rizwan Zafar Resume.docx', 'role_specific_resume', 6783, 23811, '2025-09-14T16:38:36+00:00', FALSE),
('422ad16dadf006db', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/MB/resume-Project-Management-Professional.pdf', 'resume-Project-Management-Professional.pdf', 'role_specific_resume', 7758, 78145, '2024-05-20T11:13:26+00:00', FALSE),
('de1607ae496cc968', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/KSA/rizwanzafar resume.pdf', 'rizwanzafar resume.pdf', 'role_specific_resume', 7729, 69180, '2024-12-04T06:46:54+00:00', FALSE),
('74ace4dbd2c4f3ed', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/KSA/Chief Product Officer (CPO) JD.pdf', 'Chief Product Officer (CPO) JD.pdf', 'role_specific_resume', 4853, 75623, '2024-12-04T05:09:14+00:00', FALSE),
('e73d5aa94aaa99d0', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/KSA/rizwanzafar resume.docx', 'rizwanzafar resume.docx', 'role_specific_resume', 6431, 20178, '2024-12-04T06:46:56+00:00', FALSE),
('7229a9a5fb9f9d14', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/1qy/rizwanzafar resume iqy.pdf', 'rizwanzafar resume iqy.pdf', 'role_specific_resume', 5733, 39752, '2025-06-30T21:46:48+00:00', FALSE),
('8ed81cefafdd8017', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/1qy/rizwanzafar resume iqy.docx', 'rizwanzafar resume iqy.docx', 'role_specific_resume', 4547, 19965, '2025-07-15T08:16:54+00:00', FALSE),
('d3a5b7a27118ec6f', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/1qy/BRD Simpaisa X YAP.docx', 'BRD Simpaisa X YAP.docx', 'simpaisa_org_doc', 31224, 581122, '2025-07-06T20:39:10+00:00', FALSE),
('dcf0d54030fb437a', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/1qy/VP of Product - Qlub (Dubai).pdf', 'VP of Product - Qlub (Dubai).pdf', 'role_specific_resume', 2969, 64039, '2025-06-30T13:48:46+00:00', FALSE),
('d728e9ef8081b2f1', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/1qy/rizwanzafar resume .pdf', 'rizwanzafar resume .pdf', 'role_specific_resume', 5756, 39887, '2025-06-30T21:47:54+00:00', FALSE),
('239b503c795b8a60', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/RAK/resume-document (1).pdf', 'resume-document (1).pdf', 'role_specific_resume', 8134, 101803, '2024-06-10T15:36:21+00:00', FALSE),
('04ef926cb2ba8dbe', '/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/RAK/resume-Senior-Vice-President-&amp;-Head-of-Innovation.pdf', 'resume-Senior-Vice-President-&amp;-Head-of-Innovation.pdf', 'role_specific_resume', 8162, 78560, '2024-05-17T23:26:35+00:00', FALSE),
('766de0c2321c97e7', '/Users/rizwanzafar/Desktop/cv-rizwan-zafar visa.pdf', 'cv-rizwan-zafar visa.pdf', 'role_specific_resume', 5809, 79155, '2026-05-02T13:30:25.596317+00:00', FALSE),
('f1bbe9367c8248c2', '/Users/rizwanzafar/Desktop/cv-rizwan-zafar-MN.pdf', 'cv-rizwan-zafar-MN.pdf', 'role_specific_resume', 6180, 80315, '2026-05-02T13:25:34.391893+00:00', FALSE),
('8049798e4338a0f9', '/Users/rizwanzafar/Desktop/cv-rizwan-zafar-network-international-issuer-processing-2026-05-02.pdf', 'cv-rizwan-zafar-network-international-issuer-processing-2026-05-02.pdf', 'role_specific_resume', 5907, 86851, '2026-05-02T13:27:52.163919+00:00', FALSE),
('6e40bfc3f67ed445', '/Users/rizwanzafar/Desktop/cv-rizwan-zafar-salt.pdf', 'cv-rizwan-zafar-salt.pdf', 'role_specific_resume', 5737, 80563, '2026-05-02T13:27:41.606753+00:00', FALSE),
('4f351e1d67f539a5', '/Users/rizwanzafar/Desktop/cv-rizwan-zafar-visa-digital-manager-vca-2026-05-02.pdf', 'cv-rizwan-zafar-visa-digital-manager-vca-2026-05-02.pdf', 'role_specific_resume', 6821, 78814, '2026-05-02T14:57:41.944528+00:00', FALSE)
ON CONFLICT (file_hash) DO NOTHING;

-- ── reload PostgREST schema cache ────────────────────────────────────────
NOTIFY pgrst, 'reload schema';