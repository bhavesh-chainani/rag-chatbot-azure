# Golden Set authoring guide (policy staff)

This guide is for **non-technical policy staff** who maintain the Pro Bono SG hotline golden set. You edit **Word**; IT runs the **publish pipeline** so the chatbot and routing logic stay in sync.

## Roles

| Role | Responsibility |
|------|----------------|
| **Policy author** | Edit the official Word golden set; submit for publish |
| **Publisher (IT/vendor)** | Approve, run the automated pipeline, verify staging, promote to production |

Target turnaround: **1–2 business days** after approval.

## Where to work

1. Use the **SharePoint / OneDrive** library your organisation configures (see your IT contact for the exact site).
2. Work only on the **approved template** — same section layout as `data/2026.04.16 PBSG_Golden_Set_General_Enquiries_v3.docx` in this repository.
3. **Do not** edit JSON files, Azure Portal, or the intern chat screen to change policy.

### SharePoint library layout (recommended)

| Library / folder | Purpose |
|------------------|---------|
| `Golden Set / Drafts` | Work in progress; version history enabled |
| `Golden Set / Approved` | File copied here when ready for IT publish |
| `Golden Set / Changelog` | Optional list: date, author, summary, version label |

**Submit for publish:** move or copy the approved `.docx` into `Approved`, then use your site’s **Request publish** action (approval flow or email to IT). Authors do not run deployment commands.

## Document rules

### Entry headers (required)

Each topic must start on its own line with this exact pattern:

```text
GEN3-T01 — First Contact — Representation & Scope Gating
```

- `GEN3-Txx` id must be unique.
- Use an em dash `—` between id and topic title (not a hyphen).

### Sections inside each entry (required order)

Use these **exact** section headings (copy from the template):

1. `Query` — sample intern scenario (optional but helpful for search)
2. `Variations` — bullet list of alternate phrasings
3. `Part A — Intern Briefing` — background the intern reads before questions
4. `Part B — Branching Questions` — triage questions and `If Yes/No/Not Sure` branches
5. `Part C — Routing Recommendation` — route blocks (`Route A (Name):` …)
6. `Guardrails` — bullet list of safety/policy limits

**Do not** mention `Part C` inside Part B prose unless you mean the real Part C section — the builder stops Part B at the full heading `Part C — Routing Recommendation`.

### Part B branching

- Label questions `Q1:`, `Q2:`, … (parentheticals allowed, e.g. `Q5 (SGC/PR path):`).
- Use bullet lines: `If Yes → …`, `If No → …`, `If Not Sure → …` (or `If Q4 = Not Sure → …`).
- Handoffs to other entries must use real ids: `GEN3-T02`, `GEN3-T03`, `GEN3-T04`, `GEN3-T06` (not placeholders like `GEN3-T-FAM`).
- For means-test questions involving PCHI, savings, housing, marginal thresholds, or hardship exceptions, the JSON must also include `means_test_structured` metadata. Policy authors should keep the Word prose clear, and IT/vendor maintainers should update the structured metadata described in [means_test_structured_authoring.md](means_test_structured_authoring.md) before publishing.

### Part C routing

- Start each route with `Route X (Short name):` then steps.
- For **intern-facing route cards** (read-aloud script, steps), also fill the **Route cards appendix** — see [routing_structured_authoring.md](routing_structured_authoring.md).

### What not to change without IT

- Entry ids already live in production (renaming breaks routing links).
- Cross-entry handoff wording that references backend resume points (e.g. GEN3-T02 → GEN3-T06 → resume parent question).
- File names used by automation: keep the approved master name or tell IT when renaming.

## Version labelling

When submitting for publish, record in the changelog:

- **Version label** — e.g. `v3.05 (22 May 2026)`
- **Source file name** — e.g. `2026.05.22 PBSG_Golden_Set_General_Enquiries_v3.docx`
- **Summary** — what topics or routes changed

IT sets `PBSG_GOLDEN_SET_VERSION` and `PBSG_GOLDEN_SET_PUBLISHED_AT` in Azure when publishing so interns can see what is live (see chat header link when configured).

## After you submit

IT will:

1. Convert Word → JSON (`scripts/build_pbsg_golden_set_json.py`)
2. Merge route cards if supplied (`scripts/merge_routing_structured.py`)
3. Run automated tests and optional eval on **staging**
4. Re-index search and deploy the backend
5. Promote to production after smoke checks

## Rollback

If a publish causes problems, IT reverts to the previous approved Word file in SharePoint, re-runs the pipeline, and redeploys. Keep at least one prior approved copy in `Approved` or version history.

## Help

- Technical pipeline: [golden_set_publish_pipeline.md](golden_set_publish_pipeline.md)
- Route cards appendix: [routing_structured_authoring.md](routing_structured_authoring.md)
- Data ingestion overview: [data_ingestion.md](data_ingestion.md)
