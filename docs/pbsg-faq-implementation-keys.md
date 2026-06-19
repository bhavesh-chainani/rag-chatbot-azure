# PBSG FAQ implementation keys

This file converts [pbsg-faq-document-checklist.md](pbsg-faq-document-checklist.md) into an implementation-ready key map for `PBSG_GENERAL_ENQUIRY_FAQS`.

## Conventions
- Key names use `snake_case` to match the current FAQ catalog style.
- **Verbatim** means one standalone deterministic FAQ entry can be implemented for that question intent.
- **Changed** means the document question should map into a safer merged or reframed FAQ key.
- **Do not add** means no deterministic FAQ key should be created for it.

## Status legend
- **Verbatim**
- **Changed**
- **Do not add**

---

## A. About Pro Bono SG
- `pbsg_about` — **Verbatim**
  - Source: `What is Pro Bono SG?`
- `pbsg_operating_hours` — **Verbatim**
  - Source: `What are your operating hours?`
- `pbsg_location_walk_in` — **Verbatim**
  - Source: `What is your address? Can I walk in?`
- `pbsg_appointment_wait_time` — **Verbatim**
  - Source: `How long is the waiting time for my appointment?`

## B. Services and Scope
- `legal_guidance_vs_representation` — **Verbatim**
  - Source: `What is the difference between legal guidance and legal representation?`
- `phone_or_email_guidance` — **Verbatim**
  - Source: `Can you give legal guidance over the phone or by email?`
- `pbsg_representation_availability` — **Verbatim**
  - Source: `Can you represent me in court?`
- `pbsg_business_scope` — **Changed**
  - Source: `Can you help with business matters?`
  - Implement as: `Can PBSG help with business matters / non-profit matters?`

## C. Means Testing and Common Financial Terms
- `pbsg_eligibility_overview` — **Changed**
  - Source: `How do you decide if I qualify?`
  - Implement as: `PBSG eligibility overview`
- `pbsg_foreigner_eligibility` — **Changed**
  - Source: `Can I still get help if I am not a Singapore Citizen or PR?`
  - Implement as: `PBSG eligibility for foreigners / non-SC-PR overview`
- `pbsg_overseas_eligibility` — **Changed**
  - Source: `Can I still get help if I am overseas?`
  - Implement as: `PBSG overseas applicant overview`
- `pchi` — **Verbatim**
  - Source: `What is PCHI?`
- `annual_value` — **Verbatim**
  - Source: `What is Annual Value?`
- `savings_and_non_cpf_investments` — **Verbatim**
  - Source: `How are savings and non-CPF investments calculated?`
- `pbsg_rejected_application_options` — **Changed**
  - Source: `I have been rejected by PBSG. Is there any way to appeal for legal help, or can you help if I just miss the means test by a bit?`
  - Implement as: `PBSG rejected application / write-in / contact options`

## D. Legal Aid Bureau (LAB)
- `lab_about` — **Verbatim**
  - Source: `What is LAB?`
- `lab_vs_pbsg` — **Verbatim**
  - Source: `What is the difference between LAB and PBSG?`
- `lab_who_can_apply` — **Verbatim**
  - Source: `Who can apply for LAB?`
- `lab_means_test` — **Verbatim**
  - Source: `What is LAB’s means test?`
- `lab_merits_test` — **Verbatim**
  - Source: `What is LAB’s merits test?`
- `lab_scope_overview` — **Changed**
  - Source: `What types of cases can LAB help with?`
  - Implement as: `LAB scope overview`
- `lab_exclusions_overview` — **Changed**
  - Source: `What types of cases does LAB not handle?`
  - Implement as: `LAB scope exclusions overview`
- **Do not add**
  - Source: `Can LAB help with business-related cases?`
- **Do not add**
  - Source: `Can LAB help if the case is urgent or there is a deadline very soon?`
- `lab_apply` — **Verbatim**
  - Source: `How do I apply for LAB?`
- `lab_appointment_and_documents` — **Verbatim**
  - Source: `What happens at the LAB appointment and what documents should the enquirer bring?`
- `lab_processing_time` — **Verbatim**
  - Source: `How long does LAB take to process an application?`
- `lab_cost` — **Verbatim**
  - Source: `Does the enquirer have to pay for LAB?`
- `lab_existing_lawyer` — **Changed**
  - Source: `Can the enquirer still apply for LAB if the enquirer already has a lawyer?`
  - Implement as: `Can I apply for LAB if I already have a lawyer?`
- `lab_contact` — **Verbatim**
  - Source: `What are LAB’s contact details?`

## E. Public Defender’s Office (PDO)
- `pdo_about` — **Verbatim**
  - Source: `What is PDO?`
- `pdo_vs_pbsg` — **Verbatim**
  - Source: `What is the difference between PDO and PBSG?`
- `pdo_qualifies` — **Verbatim**
  - Source: `Who qualifies for PDO?`
- `pdo_exclusions_overview` — **Changed**
  - Source: `What offences does PDO not handle?`
  - Implement as: `PDO exclusions overview`
- `pdo_exclusions_overview` — **Changed**
  - Source: `Which Acts are excluded under PDO for gambling, organised crime, and terrorism?`
  - Implement as: `PDO exclusions overview`
- `pdo_exclusions_overview` — **Changed**
  - Source: `Which regulatory Acts are excluded under PDO?`
  - Implement as: `PDO exclusions overview`
- `pdo_means_test` — **Verbatim**
  - Source: `What is PDO’s means test?`
- `pdo_merits_test` — **Verbatim**
  - Source: `What is PDO’s merits test?`
- `pdo_apply` — **Verbatim**
  - Source: `How does the enquirer apply for PDO?`
- `pdo_contact` — **Verbatim**
  - Source: `What are PDO’s contact details?`

## F. Legal Assistance Scheme for Capital Offences (LASCO)
- `lasco_about` — **Verbatim**
  - Source: `What is LASCO?`
- `lasco_how_it_works` — **Verbatim**
  - Source: `How does LASCO work?`
- `lasco_apply` — **Verbatim**
  - Source: `How does the enquirer apply for LASCO?`
- `capital_offence_overview` — **Changed**
  - Source: `What are capital offences?`
  - Implement as: `What are capital offences? (high-level overview only)`

## G. Clinics and General Legal Guidance
- `clinic_about` — **Verbatim**
  - Source: `What is a clinic?`
- `clinic_cost` — **Verbatim**
  - Source: `How much do clinics cost?`
- `clinic_eligibility_overview` — **Changed**
  - Source: `Who is eligible for clinics?`
  - Implement as: `Clinic eligibility overview`
- `clinic_locations_modes` — **Changed**
  - Source: `What locations and modes are available for clinics?`
  - Implement as: `Clinic locations / timings / modes overview`
- `clinic_apply` — **Verbatim**
  - Source: `How do I apply for clinics?`
- `clinic_documents` — **Verbatim**
  - Source: `What documents should the enquirer prepare for a clinic application?`
- `clinic_timing_flexibility` — **Changed**
  - Source: `What if the enquirer cannot make the usual clinic timings?`
  - Implement as: `Clinic accommodations / alternate timing requests`
- `clinic_accessibility_support` — **Changed**
  - Source: `What if the enquirer cannot travel to the clinic and does not know how to use video call?`
  - Implement as: `Clinic accessibility / video-call support overview`
- `clinic_application_timeline` — **Changed**
  - Source: `What happens after the enquirer applies for clinics?`
  - Implement as: `Clinic application timeline / next steps`
- `clinic_status_followup` — **Changed**
  - Source: `I have already submitted a clinic application. What is the status now?`
  - Implement as: `Clinic status / follow-up overview`
- **Do not add**
  - Source: `What should the enquirer do if there is a court hearing or deadline in the meantime?`

## H. Community Law Centres
- `community_law_centre_about` — **Verbatim**
  - Source: `What are the Community Law Centres? Can I apply?`
- `community_law_centre_walk_in` — **Verbatim**
  - Source: `Can I walk in to the Community Law Centres?`
- `community_law_centre_phone` — **Verbatim**
  - Source: `Is there a phone number for the Community Law Centres?`
- `community_law_centre_hours` — **Changed**
  - Source: `When are Community Law Centre consultations usually held?`
  - Implement as: `Community Law Centre hours / consultations overview`
- `community_law_centre_vs_cdc` — **Verbatim**
  - Source: `What is the difference between the CDC clinics and the Community Law Centres?`

## I. Transnational Family Care Centre (TFCC)
- `tfcc_about` — **Verbatim**
  - Source: `What is the Transnational Family Care Centre (TFCC)?`
- `tfcc_eligibility` — **Changed**
  - Source: `Who may apply to TFCC?`
  - Implement as: `TFCC eligibility overview`
- `tfcc_partner_support` — **Changed**
  - Source: `What if the enquirer also needs financial, medical, or social assistance?`
  - Implement as: `TFCC partner-support / non-legal support overview`
- `tfcc_low_income` — **Changed**
  - Source: `What is considered “low-income” for TFCC?`
  - Implement as: `TFCC low-income / affordability overview`

## J. Migrant Workers’ Law Centre (MWLC)
- `mwlc_about` — **Verbatim**
  - Source: `What is the Migrant Workers’ Law Centre @ Migrant Workers’ Centre?`
- `mwlc_contact` — **Verbatim**
  - Source: `How do I contact MWLC?`
- `mwlc_apply` — **Verbatim**
  - Source: `How does a migrant worker apply for help from MWLC?`
- `mwlc_partner_support` — **Changed**
  - Source: `What if the migrant worker also needs non-legal help?`
  - Implement as: `MWLC non-legal support / partner-support overview`

## K. Inclusive Justice Law Centre (IJLC)
- `ijlc_about` — **Verbatim**
  - Source: `What is the Inclusive Justice Law Centre?`
- `ijlc_apply` — **Verbatim**
  - Source: `How does someone apply for IJLC-related help?`

## L. Family Justice Support Scheme (FJSS)
- `fjss_about` — **Verbatim**
  - Source: `What is FJSS?`
- `fjss_cost` — **Verbatim**
  - Source: `How much does FJSS cost?`
- `fjss_eligibility` — **Changed**
  - Source: `What are the eligibility criteria for FJSS?`
  - Implement as: `FJSS eligibility overview`
- `fjss_means_test_pro_bono` — **Verbatim**
  - Source: `What is the means test for FJSS Pro Bono?`
- `fjss_means_test_modest_means` — **Verbatim**
  - Source: `What is the means test for FJSS Modest Means?`
- `fjss_merits_test` — **Verbatim**
  - Source: `What is the merits test for FJSS?`
- `fjss_existing_lawyer` — **Changed**
  - Source: `Can I still apply for FJSS if I am currently represented by a lawyer?`
  - Implement as: `FJSS existing-lawyer overview`
- `fjss_apply` — **Verbatim**
  - Source: `How does someone apply for FJSS?`
- `fjss_application_support` — **Changed**
  - Source: `What if the enquirer does not know how to use the internet for an FJSS application?`
  - Implement as: `FJSS application support / offline help overview`
- `fjss_missing_documents` — **Changed**
  - Source: `What if the enquirer does not have the FJSS documents requested?`
  - Implement as: `FJSS missing-documents guidance`
- `fjss_application_timeline` — **Changed**
  - Source: `What happens after an FJSS application is submitted?`
  - Implement as: `FJSS application timeline / next steps`
- `fjss_status_followup` — **Changed**
  - Source: `I have already submitted my FJSS application. What is the status now?`
  - Implement as: `FJSS status / follow-up overview`
- **Do not add**
  - Source: `What should the enquirer do if there is a court hearing or deadline while the FJSS application is pending?`
- `fjss_pending_application_representation_status` — **Changed**
  - Source: `Can PBSG write to the court to say that an FJSS application is being processed?`
  - Implement as: `Pending FJSS application / court-letter / representation-status overview`

## M. Criminal Legal Aid Scheme (CLAS)
- `clas_about` — **Verbatim**
  - Source: `What is CLAS?`
- `clas_cost` — **Verbatim**
  - Source: `How much does CLAS cost?`
- `clas_eligibility` — **Changed**
  - Source: `What are the main eligibility criteria for CLAS?`
  - Implement as: `CLAS eligibility overview`
- `clas_exclusions_overview` — **Changed**
  - Source: `What offences does CLAS not handle?`
  - Implement as: `CLAS exclusions overview`
- `clas_exclusions_overview` — **Changed**
  - Source: `Which Acts are excluded under CLAS for gambling, organised crime, and terrorism?`
  - Implement as: `CLAS exclusions overview`
- `clas_exclusions_overview` — **Changed**
  - Source: `Which regulatory Acts are excluded under CLAS?`
  - Implement as: `CLAS exclusions overview`
- `clas_means_test` — **Verbatim**
  - Source: `What is the means test for CLAS?`
- `clas_merits_test` — **Verbatim**
  - Source: `What is CLAS’ merits test?`
- `clas_existing_lawyer` — **Changed**
  - Source: `Can I still apply for CLAS if I am currently represented by a lawyer?`
  - Implement as: `CLAS existing-lawyer overview`
- `clas_apply` — **Verbatim**
  - Source: `How does someone apply for CLAS?`
- `clas_application_support` — **Changed**
  - Source: `What if the enquirer does not know how to use the internet for a CLAS application?`
  - Implement as: `CLAS application support / offline help overview`
- `clas_missing_documents` — **Changed**
  - Source: `What if the enquirer does not have the CLAS documents requested?`
  - Implement as: `CLAS missing-documents guidance`
- `clas_application_timeline` — **Changed**
  - Source: `What happens after a CLAS application is submitted?`
  - Implement as: `CLAS application timeline / next steps`
- `clas_status_followup` — **Changed**
  - Source: `I have already submitted my CLAS application. What is the status now?`
  - Implement as: `CLAS status / follow-up overview`
- **Do not add**
  - Source: `What should the enquirer do if there is a court hearing or deadline while the CLAS application is pending?`
- `clas_pending_application_representation_status` — **Changed**
  - Source: `Can PBSG write to the court to say that a CLAS application is being processed?`
  - Implement as: `Pending CLAS application / court-letter / representation-status overview`

## N. Other Services and Practical Matters
- `referral_to_lab_or_pdo` — **Verbatim**
  - Source: `Why are you referring me to LAB or PDO?`
- `lab_pdo_first_port_of_call` — **Changed**
  - Source: `Can I choose not to go to LAB or PDO and ask PBSG to help instead?`
  - Implement as: `LAB / PDO first-port-of-call and PBSG fallback overview`
- `drafting_documents_or_letters` — **Verbatim**
  - Source: `Can you help draft documents or send letters for me?`
- `will_or_lpa_services` — **Verbatim**
  - Source: `Can you help me prepare a will or Lasting Power of Attorney?`
- `notarise_or_commissioning` — **Verbatim**
  - Source: `Can you notarise documents or act as Commissioner for Oaths?`
- `accompaniment_policy` — **Verbatim**
  - Source: `Can someone accompany me to my appointment?`
- `interpretation_support` — **Verbatim**
  - Source: `Can you provide interpretation?`
- `minor_support_escalation` — **Changed**
  - Source: `I am under 18 years old. Can you help me?`
  - Implement as: `Minor / under-18 handling and staff-escalation overview`
- `confidentiality` — **Verbatim**
  - Source: `Are my information and case confidential?`
- `why_we_need_information` — **Verbatim**
  - Source: `Why do you need so much information from me?`
- `if_pbsg_cannot_help` — **Changed**
  - Source: `What should I do if PBSG cannot help me?`
  - Implement as: `If PBSG cannot assist / next-steps overview`
- `lawyer_recommendations` — **Verbatim**
  - Source: `Can you recommend me a lawyer?`
- `prior_lawyer_concerns` — **Changed**
  - Source: `Can you help me if I had a lawyer previously but the lawyer did not handle the case well?`
  - Implement as: `Previous-lawyer concerns / reassessment overview`
- `lawyer_dispute` — **Verbatim**
  - Source: `Can you help me with a dispute I have with my lawyer?`
- `translation_for_court_use` — **Verbatim**
  - Source: `Can you help translate my document for court use?`
- `non_profit_support` — **Changed**
  - Source: `I am calling on behalf of a non-profit and need help. Can PBSG help?`
  - Implement as: `Non-profit / charity / social-enterprise support overview`
- `missed_appointment_rescheduling` — **Verbatim**
  - Source: `I missed my appointment or interview. Can I get a new appointment?`

## O. General Enquiries, Public Channels, and Difficult Conversations
- `volunteer_or_internship` — **Verbatim**
  - Source: `How can I volunteer or sign up for an internship?`
- `donate` — **Verbatim**
  - Source: `How can I donate?`
- `talks_and_events` — **Verbatim**
  - Source: `How can I register for talks and events?`
- `frontliner_role_boundary` — **Changed**
  - Source: `“You are all lawyers, right?”`
  - Implement as: `Are PBSG frontliners lawyers? / role-boundary overview`
- **Do not add**
  - Source: `“Can you hurry up?”`
- `complaints` — **Verbatim**
  - Source: `“Can I complain?”`

---

## Explicit non-FAQ items to keep out of the deterministic FAQ matcher
These are intentionally **not** assigned implementation keys:
- `I registered for a legal talk and have questions about CPD points or the recording of the talk.`
- `I am looking for a staff member, or I am a volunteer lawyer with a query.`
- `Just tell me what to do.`
- `I don’t trust LAB/PDO.`
- `I am in danger now.`
- `I want to hurt myself.`
- `I need help with shelter, food, or finances.`

These should remain outside the deterministic FAQ path because they are better handled by escalation, routing, or dedicated conversational logic.
