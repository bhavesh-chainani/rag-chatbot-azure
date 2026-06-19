# PBSG FAQ regex and matcher plan

This file turns [pbsg-faq-implementation-keys.md](pbsg-faq-implementation-keys.md) into implementation guidance for `PBSG_GENERAL_ENQUIRY_FAQS` matching.

## Goals
- Keep FAQ matching deterministic.
- Make first-turn FAQ matching and mid-stream interrupt matching align.
- Avoid stealing routing traffic from substantive legal narratives.
- Prefer narrow, question-shaped patterns over broad nouns.

## Matching principles

### 1. Prefer question-shaped triggers
Safe patterns usually include one of:
- `what is`
- `how do i`
- `how does`
- `can i`
- `can you`
- `what happens`
- `what are`
- `difference between`
- `who can`
- `who qualifies`
- `how much`
- `where`
- `contact`

### 2. Pair question-shapes with named schemes or operational phrases
Good combinations:
- `what is lab`
- `how do i apply for pdo`
- `what is a clinic`
- `what is tfcc`
- `what documents should i prepare for a clinic`
- `what are your operating hours`

### 3. Avoid raw single-word triggers
Do **not** rely on lone words such as:
- `apply`
- `documents`
- `urgent`
- `help`
- `clinic`
- `business`
- `status`
- `hours`
- `court`
- `deadline`

### 4. Favor merged keys when the source questions form one safe intent family
Example:
- PDO exclusions should be one merged key, not three independent regex families.
- CLAS exclusions should be one merged key, not three independent regex families.
- Clinic status / timeline / follow-up should stay in distinct but tightly scoped operational clusters.

### 5. Require specificity for routing-adjacent topics
For any FAQ that overlaps with real case facts, the matcher should usually require:
- a question frame, and
- a named scheme/service, and sometimes
- an operational noun phrase.

For example:
- safe: `can i apply for lab if i already have a lawyer`
- unsafe: `i already have a lawyer`

### 6. Keep volatile operational facts answer-side, not matcher-side
It is fine for the answer to mention addresses, hours, or contact info.
The matcher should still remain narrow and intent-based.

---

## Recommended top-level intent vocabulary
The coarse FAQ-intent gate should remain conservative and only admit clear FAQ/admin phrasing.

Recommended additions / retained signals:
- `what is`
- `what are`
- `how do i`
- `how does`
- `how much`
- `what happens`
- `difference between`
- `who can`
- `who qualifies`
- `operating hours`
- `opening hours`
- `walk in`
- `waiting time`
- `contact details`
- `phone number`
- `hotline`
- `annual value`
- `pchi`
- `savings`
- `investments`
- `means test`
- `merits test`
- `clinic`
- `application status`
- `status now`
- `confidential`
- `volunteer`
- `donate`
- `talks and events`

Avoid adding broad nouns like `deadline`, `business`, `documents`, `court`, or `help` to the global intent gate unless paired more specifically at the entry level.

---

## Matcher groups and recommended pattern shapes

## Group 1: Core organisation / operational FAQs
These are the safest and can use straightforward patterns.

### `pbsg_about`
Use patterns like:
- `what(?:'s| is) (?:pro bono sg|pbsg|pro bono singapore)`
- `tell me about (?:pro bono sg|pbsg)`
- `who (?:is|are) (?:pro bono sg|pbsg)`

### `pbsg_operating_hours`
Use patterns like:
- `what are your operating hours`
- `what are your opening hours`
- `what time (?:are you|is pbsg) open`
- `pbsg (?:operating|opening) hours`

Avoid:
- plain `hours`

### `pbsg_location_walk_in`
Use patterns like:
- `what is your address`
- `where (?:is|are) (?:pbsg|pro bono sg)`
- `can i walk in`
- `do you accept walk[- ]?ins`
- `where is the help centre`

Avoid:
- plain `address`
- plain `counter`

### `pbsg_appointment_wait_time`
Use patterns like:
- `how long is the waiting time`
- `how long do i have to wait for (?:my )?appointment`
- `when will my appointment be`

Avoid:
- plain `wait`

---

## Group 2: Service-boundary FAQs

### `legal_guidance_vs_representation`
Use patterns like:
- `difference between legal guidance and legal representation`
- `what is the difference between legal guidance and legal representation`
- `what does legal guidance mean`
- `what does legal representation mean`

### `phone_or_email_guidance`
Use patterns like:
- `can you give legal guidance over the phone`
- `can you give legal guidance by email`
- `can i get legal guidance over the phone`
- `do you give legal advice by email`

Note: answer should keep the no-legal-advice boundary.

### `pbsg_representation_availability`
Use patterns like:
- `can you represent me in court`
- `can pbsg represent me`
- `will pbsg represent me`

### `pbsg_business_scope`
Use patterns like:
- `can pbsg help with business matters`
- `do you help with business matters`
- `can you help with non-profit matters`
- `can you help with charity matters`
- `can you help with social enterprise matters`

Avoid:
- plain `business`

---

## Group 3: Eligibility and financial-term FAQs
These overlap with routing, so patterns must stay very question-shaped.

### `pbsg_eligibility_overview`
Use patterns like:
- `how do you decide if i qualify`
- `how do you decide eligibility`
- `how do you assess eligibility`
- `what do you consider when deciding if i qualify`

### `pbsg_foreigner_eligibility`
Use patterns like:
- `can i still get help if i am not a singapore citizen or pr`
- `can foreigners get help from pbsg`
- `can a non singapore citizen get help`

### `pbsg_overseas_eligibility`
Use patterns like:
- `can i still get help if i am overseas`
- `can someone overseas apply`
- `can i apply from overseas`

### `pchi`
Use patterns like:
- `what is pchi`
- `what does pchi mean`
- `what is per capita household income`

### `annual_value`
Use patterns like:
- `what is annual value`
- `what does annual value mean`
- `how do you calculate annual value`

### `savings_and_non_cpf_investments`
Use patterns like:
- `how are savings and non cpf investments calculated`
- `what counts as savings`
- `what counts as non cpf investments`

### `pbsg_rejected_application_options`
Use patterns like:
- `i was rejected by pbsg`
- `can i appeal after being rejected by pbsg`
- `what if i just miss the means test`
- `what can i do if pbsg rejected me`

Because this is somewhat routing-adjacent, prefer requiring `pbsg` / `means test` / `rejected` combinations.

---

## Group 4: LAB FAQs

### Safe standalone keys
- `lab_about`
- `lab_vs_pbsg`
- `lab_who_can_apply`
- `lab_means_test`
- `lab_merits_test`
- `lab_apply`
- `lab_appointment_and_documents`
- `lab_processing_time`
- `lab_cost`
- `lab_contact`

Pattern guidance:
- always require `lab` or `legal aid bureau`
- for `apply`, require `apply` + `lab`
- for `documents`, require `lab` + `documents` or `lab appointment`
- for `processing time`, require `lab` + `process|processing|how long`
- for `cost`, require `lab` + `pay|cost|fee`

### `lab_scope_overview`
Use patterns like:
- `what types of cases can lab help with`
- `what cases can lab handle`
- `what does lab cover`

### `lab_exclusions_overview`
Use patterns like:
- `what types of cases does lab not handle`
- `what can lab not help with`
- `what does lab not cover`

### `lab_existing_lawyer`
Use patterns like:
- `can i apply for lab if i already have a lawyer`
- `can i still apply for lab if i have a lawyer`

Do not match:
- `i already have a lawyer`

---

## Group 5: PDO FAQs

### Safe standalone keys
- `pdo_about`
- `pdo_vs_pbsg`
- `pdo_qualifies`
- `pdo_means_test`
- `pdo_merits_test`
- `pdo_apply`
- `pdo_contact`

Always require `pdo` or `public defender(?:'s)? office`.

### `pdo_exclusions_overview`
Use patterns like:
- `what offences does pdo not handle`
- `what offences are excluded under pdo`
- `which acts are excluded under pdo`
- `what regulatory acts are excluded under pdo`
- `what does pdo not cover`

Keep this merged under one key so users asking about excluded offences or excluded Acts land in the same FAQ answer.

---

## Group 6: LASCO FAQs

### Standalone keys
- `lasco_about`
- `lasco_how_it_works`
- `lasco_apply`

### `capital_offence_overview`
Use patterns like:
- `what are capital offences`
- `what is a capital offence`
- `what counts as a capital offence`

Important answer constraint:
- keep answer high-level
- do not let patterning or answer imply charge-by-charge classification for a live matter

---

## Group 7: Clinics / legal-guidance FAQs
This is one of the highest collision areas because words like `clinic`, `apply`, `documents`, and `status` appear in substantive user narratives.

### Safe standalone keys
- `clinic_about`
- `clinic_cost`
- `clinic_apply`
- `clinic_documents`

Pattern guidance:
- require `clinic` / `legal guidance` in the same query
- for `documents`, require `clinic` + `documents`
- for `apply`, require `clinic` + `apply|application`

### Changed keys
- `clinic_eligibility_overview`
- `clinic_locations_modes`
- `clinic_timing_flexibility`
- `clinic_accessibility_support`
- `clinic_application_timeline`
- `clinic_status_followup`

Pattern examples:
- `who is eligible for clinics`
- `what locations and modes are available for clinics`
- `what if i cannot make the usual clinic timings`
- `what if i cannot travel to the clinic`
- `what happens after i apply for clinics`
- `what is the status of my clinic application`

Avoid:
- plain `status`
- plain `documents`
- plain `apply`

---

## Group 8: Community Law Centres / TFCC / MWLC / IJLC
These should usually require the named centre in the pattern.

### Community Law Centre keys
- `community_law_centre_about`
- `community_law_centre_walk_in`
- `community_law_centre_phone`
- `community_law_centre_hours`
- `community_law_centre_vs_cdc`

Require phrases such as:
- `community law centre`
- `community law centres`
- `cdc clinics`

### TFCC keys
- `tfcc_about`
- `tfcc_eligibility`
- `tfcc_partner_support`
- `tfcc_low_income`

Require `tfcc` or `transnational family care centre`.

### MWLC keys
- `mwlc_about`
- `mwlc_contact`
- `mwlc_apply`
- `mwlc_partner_support`

Require `mwlc` or `migrant workers' law centre`.

### IJLC keys
- `ijlc_about`
- `ijlc_apply`

Require `ijlc` or `inclusive justice law centre`.

---

## Group 9: FJSS / CLAS scheme FAQs
These are routing-adjacent because they concern legal representation schemes. Keep all matchers scheme-specific.

### FJSS keys
- `fjss_about`
- `fjss_cost`
- `fjss_eligibility`
- `fjss_means_test_pro_bono`
- `fjss_means_test_modest_means`
- `fjss_merits_test`
- `fjss_existing_lawyer`
- `fjss_apply`
- `fjss_application_support`
- `fjss_missing_documents`
- `fjss_application_timeline`
- `fjss_status_followup`
- `fjss_pending_application_representation_status`

Require `fjss` or `family justice support scheme` in nearly every pattern.

Examples:
- `what is fjss`
- `how much does fjss cost`
- `what is the means test for fjss pro bono`
- `what is the status of my fjss application`
- `can pbsg write to the court for an fjss application`

### CLAS keys
- `clas_about`
- `clas_cost`
- `clas_eligibility`
- `clas_exclusions_overview`
- `clas_means_test`
- `clas_merits_test`
- `clas_existing_lawyer`
- `clas_apply`
- `clas_application_support`
- `clas_missing_documents`
- `clas_application_timeline`
- `clas_status_followup`
- `clas_pending_application_representation_status`

Require `clas` or `criminal legal aid scheme` in nearly every pattern.

Examples:
- `what is clas`
- `what offences does clas not handle`
- `which acts are excluded under clas`
- `what is the status of my clas application`
- `can pbsg write to the court for a clas application`

---

## Group 10: Other services / practical matters

### Safe standalone keys
- `referral_to_lab_or_pdo`
- `drafting_documents_or_letters`
- `will_or_lpa_services`
- `notarise_or_commissioning`
- `accompaniment_policy`
- `interpretation_support`
- `confidentiality`
- `why_we_need_information`
- `lawyer_recommendations`
- `lawyer_dispute`
- `translation_for_court_use`
- `missed_appointment_rescheduling`
- `volunteer_or_internship`
- `donate`
- `talks_and_events`
- `complaints`

### Changed keys
- `lab_pdo_first_port_of_call`
- `minor_support_escalation`
- `if_pbsg_cannot_help`
- `prior_lawyer_concerns`
- `non_profit_support`
- `frontliner_role_boundary`

Pattern guidance examples:
- `why are you referring me to lab or pdo`
- `can i choose not to go to lab or pdo`
- `i am under 18 years old can you help me`
- `what should i do if pbsg cannot help me`
- `can you recommend me a lawyer`
- `i am calling on behalf of a non profit and need help`
- `are pbsg frontliners lawyers`

For `minor_support_escalation`, prefer requiring phrases like `under 18`, `minor`, `child`, not just `young`.

For `non_profit_support`, require `non-profit|charity|social enterprise|ground-up initiative` rather than broad `business` alone.

---

## Mid-stream interrupt alignment recommendation
To keep first-turn and interrupt matching in sync:

### Recommended approach
Where possible, interrupt detection should use the same catalog-backed FAQ match helper rather than maintaining a separate large keyword list.

If a full helper reuse is not practical immediately, then:
- keep the interrupt gate conservative
- add only high-signal named schemes and operational phrases
- avoid introducing generic words that could classify factual answers as side questions

High-signal interrupt vocabulary candidates:
- `lab`, `pdo`, `lasco`, `clas`, `fjss`, `tfcc`, `mwlc`, `ijlc`
- `pro bono sg`, `pbsg`
- `pchi`, `annual value`, `means test`, `merits test`
- `clinic`, `community law centre`
- `contact details`, `operating hours`, `walk in`, `waiting time`
- `confidential`, `interpretation`, `volunteer`, `donate`

Avoid adding to interrupt keywords:
- `deadline`
- `urgent`
- `court`
- `business`
- `documents`
- `apply`
- `status`

unless tightly paired with scheme names in the actual FAQ-entry patterns.

---

## Suggested implementation order
1. Add the safest groups first:
   - Group 1 core organisation
   - Group 4 LAB
   - Group 5 PDO
   - Group 6 LASCO
2. Add clinic / centre / scheme groups with tighter patterns:
   - Groups 7, 8, 9
3. Add practical-matter FAQs:
   - Group 10
4. Align interrupt logic
5. Add positive and negative regression tests

---

## Testing recommendations tied to regex design
For every high-collision key, add at least one negative test.

Examples:
- `Applicant wants to apply for divorce and custody help.` should **not** match `clinic_apply`, `lab_apply`, or `fjss_apply`.
- `Applicant has documents for a criminal charge.` should **not** match `clinic_documents` or `clas_missing_documents`.
- `Applicant has a court hearing tomorrow.` should **not** match any deadline-related excluded FAQ.
- `Applicant runs a business and has debt.` should **not** match `pbsg_business_scope` unless clearly asked as a scope question.
- `I already have a lawyer.` should **not** match `lab_existing_lawyer`, `fjss_existing_lawyer`, or `clas_existing_lawyer` unless paired with an application question.

The design goal is: **a substantive case narrative should still route as a case, not as an FAQ.**
