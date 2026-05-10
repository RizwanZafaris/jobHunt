/**
 * Mock /network data — used until the FastAPI router is wired into
 * api/server.py and dashboard/src/lib/api.ts grows the corresponding
 * functions. The shapes match agents/referral_graph.py exactly so the
 * swap is one-line per fetch site.
 *
 * TODO: replace with /network/* endpoints — see api/NETWORK.md
 */

import type {
  ImportSummary,
  IntroDraft,
  Person,
  ReferralPath,
  TargetCoverage,
} from '@/lib/types/network'

// Person bank — referenced by id from paths/coverage below.
const PEOPLE: Record<string, Person> = {
  me: {
    id: 'p_me',
    full_name: 'Rizwan Zafar',
    linkedin_url: 'https://www.linkedin.com/in/rizwanzafar/',
    headline: 'Technical Programme Lead / CPO — Fintech & Payments',
    source: 'me',
  },
  sarah: {
    id: 'p_sarah',
    full_name: 'Sarah Lin',
    linkedin_url: 'https://www.linkedin.com/in/sarah-lin/',
    headline: 'Product Lead, Risk — ex Marqeta',
    current_company: 'Marqeta',
    current_role: 'Group PM, Risk & Fraud',
    source: 'linkedin_csv',
  },
  bob: {
    id: 'p_bob',
    full_name: 'Bob Patel',
    linkedin_url: 'https://www.linkedin.com/in/bob-patel/',
    headline: 'Engineering Manager, Payments — Stripe',
    current_company: 'Stripe',
    current_role: 'Engineering Manager, Payments',
    source: 'linkedin_csv',
  },
  alice: {
    id: 'p_alice',
    full_name: 'Alice Hwang',
    linkedin_url: 'https://www.linkedin.com/in/alice-hwang/',
    headline: 'Group PM, Disputes — Stripe',
    current_company: 'Stripe',
    current_role: 'Group PM, Disputes',
    source: 'inferred',
  },
  raj: {
    id: 'p_raj',
    full_name: 'Raj Mehta',
    linkedin_url: 'https://www.linkedin.com/in/raj-mehta/',
    headline: 'PMM, Acquiring — Adyen',
    current_company: 'Adyen',
    current_role: 'Product Marketing Manager, Acquiring',
    source: 'linkedin_csv',
  },
  imani: {
    id: 'p_imani',
    full_name: 'Imani Cole',
    linkedin_url: 'https://www.linkedin.com/in/imani-cole/',
    headline: 'Director of Product — Mastercard',
    current_company: 'Mastercard',
    current_role: 'Director of Product',
    source: 'manual',
  },
  jose: {
    id: 'p_jose',
    full_name: 'José Ramírez',
    headline: 'PM, Issuing Platforms — ex Marqeta',
    current_company: 'Marqeta',
    current_role: 'PM, Issuing Platforms',
    source: 'linkedin_csv',
  },
  maya: {
    id: 'p_maya',
    full_name: 'Maya Santos',
    headline: 'Senior PM, Payments Risk — Adyen',
    current_company: 'Adyen',
    current_role: 'Senior PM, Payments Risk',
    source: 'linkedin_csv',
  },
}

const PATHS: ReferralPath[] = [
  // 1-hop: me → Sarah at Marqeta. Strongest path.
  {
    target_employee: PEOPLE.sarah,
    target_company_id: 'tc_marqeta',
    target_company_name: 'Marqeta',
    hop_count: 1,
    total_strength: 0.78,
    path: [PEOPLE.me, PEOPLE.sarah],
    edge_kinds: ['me_first_degree'],
  },
  // 2-hop: me → Bob → Alice at Stripe.
  {
    target_employee: PEOPLE.alice,
    target_company_id: 'tc_stripe',
    target_company_name: 'Stripe',
    hop_count: 2,
    total_strength: 0.65,
    path: [PEOPLE.me, PEOPLE.bob, PEOPLE.alice],
    edge_kinds: ['me_first_degree', 'colleague'],
  },
  // 1-hop: me → Raj at Adyen.
  {
    target_employee: PEOPLE.raj,
    target_company_id: 'tc_adyen',
    target_company_name: 'Adyen',
    hop_count: 1,
    total_strength: 0.7,
    path: [PEOPLE.me, PEOPLE.raj],
    edge_kinds: ['me_first_degree'],
  },
  // 2-hop: me → Sarah → José at Marqeta — alternative path.
  {
    target_employee: PEOPLE.jose,
    target_company_id: 'tc_marqeta',
    target_company_name: 'Marqeta',
    hop_count: 2,
    total_strength: 0.55,
    path: [PEOPLE.me, PEOPLE.sarah, PEOPLE.jose],
    edge_kinds: ['me_first_degree', 'colleague'],
  },
  // 1-hop: me → Imani at Mastercard.
  {
    target_employee: PEOPLE.imani,
    target_company_id: 'tc_mastercard',
    target_company_name: 'Mastercard',
    hop_count: 1,
    total_strength: 0.62,
    path: [PEOPLE.me, PEOPLE.imani],
    edge_kinds: ['me_first_degree'],
  },
]

export const MOCK_TARGET_COVERAGE: TargetCoverage[] = [
  {
    target_company_id: 'tc_marqeta',
    name: 'Marqeta',
    paths_count_1hop: 1,
    paths_count_2hop: 1,
    top_path: PATHS[0],
  },
  {
    target_company_id: 'tc_stripe',
    name: 'Stripe',
    paths_count_1hop: 0,
    paths_count_2hop: 1,
    top_path: PATHS[1],
  },
  {
    target_company_id: 'tc_adyen',
    name: 'Adyen',
    paths_count_1hop: 1,
    paths_count_2hop: 0,
    top_path: PATHS[2],
  },
  {
    target_company_id: 'tc_mastercard',
    name: 'Mastercard',
    paths_count_1hop: 1,
    paths_count_2hop: 0,
    top_path: PATHS[4],
  },
  {
    target_company_id: 'tc_visa',
    name: 'Visa',
    paths_count_1hop: 0,
    paths_count_2hop: 0,
    top_path: null,
  },
]

export const MOCK_TOP_INTRO_PATHS: ReferralPath[] = [...PATHS]
  .sort((a, b) => b.total_strength - a.total_strength)
  .slice(0, 5)

export const MOCK_PEOPLE: Person[] = Object.values(PEOPLE).filter(
  (p) => p.source !== 'me',
)

/** Simulate /network/import/linkedin-csv. */
export const MOCK_IMPORT_SUMMARY: ImportSummary = {
  imported: 312,
  skipped: 4,
  edges_created: 312,
  employments_created: 268,
  errors: [],
}

/** Simulate IntroEmailAgent.draft_intro_email. */
export const MOCK_INTRO_DRAFT: IntroDraft = {
  mode: 'intro_email',
  subject: 'Quick warm-intro ask: Stripe Disputes team',
  body:
    "Hey Bob,\n\n" +
    "Could you forward an intro to Alice Hwang on Stripe's Disputes team? " +
    "I'm exploring the Group PM, Disputes role and you and Alice still " +
    "overlap on the platform side.\n\n" +
    "Forward-able one-liner: Rizwan ran payments product at SimPaisa " +
    "($1B+ TPV, scaled disputes ops 4x in 18 months) and is moving from " +
    "Pakistan to UAE/UK.\n\n" +
    "If easier, happy to send a separate forwardable note.",
  length_words: 71,
  tone_notes:
    "Direct ask, leverages your real overlap with Alice; gives Bob a copy-paste line.",
  cost_usd: 0.0182,
  model: 'claude-opus-4-7',
  provider: 'anthropic',
}
