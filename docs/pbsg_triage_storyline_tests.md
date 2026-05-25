# PBSG Triage Storyline Test Set

This test set is designed for manual regression review and future automated eval conversion. It focuses on topic selection, deterministic routing, fact extraction, one-question follow-up behaviour, urgent/vulnerability overlays, and intern-facing wording. It does not define legal advice outcomes.

## 1. First Contact

### TC-FIRST-CONTACT-001
- **Scenario Title:** Bare greeting should start first contact
- **User Storyline:** Hi, can someone help me?
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** Caller has not described a legal matter; no urgency, vulnerability, representation, or matter type facts.
- **Expected Missing Information:** Whether there is a legal matter; representation status; whether caller is the affected person; personal/business nature; matter type.
- **Expected Chatbot Behaviour:** Default to first-contact triage and ask the first useful opening question. It must not select `GEN3-T13` or another specialty topic.
- **Expected Route Or Next Question:** Ask `GEN3-T01 Q1`: whether the applicant is currently represented by a lawyer on the same matter, or ask for a short description before proceeding if the app requires situation text first.
- **Regression Risks:** Greeting may trigger a vulnerable-applicant entry; chatbot may hallucinate a topic; chatbot may provide a generic concierge answer instead of triage.
- **Suggested Follow-Up Turns:** `No lawyer, I just need help with divorce.` Expected: proceed through known first-contact facts and hand off to `GEN3-T03` when Q5 is reached.

### TC-FIRST-CONTACT-002
- **Scenario Title:** Vague legal problem but not just greeting
- **User Storyline:** The applicant says he has a problem and wants a lawyer, but he has not said what happened. He sounds worried and keeps saying it is urgent but gives no date.
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** Applicant wants legal help; matter type unknown; no concrete urgent deadline or safety fact; no representation fact.
- **Expected Missing Information:** Current lawyer status; whether caller is self; personal vs business; prior advice; matter type.
- **Expected Chatbot Behaviour:** Run first-contact triage, not urgent triage based only on the word urgent.
- **Expected Route Or Next Question:** Ask `GEN3-T01 Q1` or one concise situation-clarifying question if no matter details exist.
- **Regression Risks:** Chatbot may over-trigger `GEN3-T06`; chatbot may ask multiple questions at once; chatbot may route before knowing any matter type.

### TC-FIRST-CONTACT-003
- **Scenario Title:** Calling for brother who can self-help
- **User Storyline:** I am calling for my brother because he is shy to call. He is 32, speaks English, can use his phone, and can call PBSG himself. He needs help with some debt letter but he is not here with me now.
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** Caller is a third party; affected person is adult and able to self-help; legal issue appears civil debt; affected person not on call.
- **Expected Missing Information:** Representation status is not stated, but Q2 can already terminate if the person can contact PBSG directly.
- **Expected Chatbot Behaviour:** Auto-extract third-party caller and ability to self-help; stop at Q2 route.
- **Expected Route Or Next Question:** `GEN3-T01 Route B` with direct-contact script for the brother.
- **Regression Risks:** Chatbot may continue asking debt questions unnecessarily; chatbot may treat caller as applicant; chatbot may skip the self-help ability check.

### TC-FIRST-CONTACT-004
- **Scenario Title:** Calling for elderly mother who cannot self-help
- **User Storyline:** I am helping my mother. She is 82, cannot hear well on the phone, and does not know how to use email. The issue is a neighbour dispute about noise and she has no lawyer.
- **Expected Primary Topic:** `GEN3-T01` with `GEN3-T13` overlay expected.
- **Facts Already Present:** Caller is on behalf of mother; mother appears unable to self-help without assistance; no current lawyer; civil/neighbour matter; elderly/access cue.
- **Expected Missing Information:** Whether the matter is personal/business; prior advice; citizenship or PR for civil stream; representation vs guidance.
- **Expected Chatbot Behaviour:** Continue first-contact Q3 rather than Route B, while noting low/high vulnerability facts for supported handling.
- **Expected Route Or Next Question:** Ask whether the matter is personal or business/commercial; do not ask if the mother can self-help again.
- **Regression Risks:** Chatbot may wrongly route to `Route B`; chatbot may over-classify elderly status alone as high vulnerability; chatbot may ignore the accessibility issue.

### TC-FIRST-CONTACT-005
- **Scenario Title:** Nonprofit organisation needs corporate help
- **User Storyline:** Caller says she is from a small registered charity. They have no lawyer and need help reviewing a service agreement for their free youth programme. She is calling for the organisation, not her own personal issue.
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** No lawyer; caller represents an organisation; business/commercial/corporate issue; nonprofit/charity.
- **Expected Missing Information:** Prior advice is not needed once Q3 nonprofit branch is clear.
- **Expected Chatbot Behaviour:** Distinguish nonprofit from for-profit and route to nonprofit legal services.
- **Expected Route Or Next Question:** `GEN3-T01 Route C`.
- **Regression Risks:** Chatbot may reject all organisation matters as commercial; chatbot may ask irrelevant personal means-test questions.

### TC-FIRST-CONTACT-006
- **Scenario Title:** For-profit shop contract dispute
- **User Storyline:** Applicant owns a small bubble tea shop and wants PBSG to sue a supplier who delivered faulty machines. The supplier contract is under the shop's company name. No lawyer yet.
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** No lawyer; for-profit business/commercial dispute; company contract; not personal capacity.
- **Expected Missing Information:** None needed for Q3 branch.
- **Expected Chatbot Behaviour:** Stop at business/commercial gate and avoid civil legal clinic means testing.
- **Expected Route Or Next Question:** `GEN3-T01 Route D`.
- **Regression Risks:** Chatbot may treat a small business as a personal civil debt; chatbot may provide legal advice about breach of contract.

## 2. Criminal

### TC-CRIMINAL-001
- **Scenario Title:** Capital offence should route to LASCO
- **User Storyline:** Applicant's son was charged with murder. The family is asking whether PBSG can get him a lawyer. They say the charge sheet mentions death penalty.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Criminal matter; charged in court; capital offence/punishable with death.
- **Expected Missing Information:** Means, nationality, and court deadline are not needed once capital offence is clear.
- **Expected Chatbot Behaviour:** Select criminal stream and route directly under the capital offence branch.
- **Expected Route Or Next Question:** `GEN3-T02 Route A`.
- **Regression Risks:** Chatbot may ask CLAS means questions despite capital offence; chatbot may mention PBSG CLAS eligibility incorrectly.

### TC-CRIMINAL-002
- **Scenario Title:** SGC charged, no PDO application
- **User Storyline:** Applicant is a Singapore Citizen charged for shop theft in State Courts. Court is next month, no lawyer, and he has not applied to PDO because he did not know it exists.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Criminal matter; non-capital implied; no near deadline; charged in court; Singapore Citizen; has not applied to PDO.
- **Expected Missing Information:** Means test is not needed before PDO-first route.
- **Expected Chatbot Behaviour:** Auto-extract SGC and no PDO application; route to PDO first.
- **Expected Route Or Next Question:** `GEN3-T02 Route B`.
- **Regression Risks:** Chatbot may send SGC directly to CLAS; chatbot may ask irrelevant PBSG means questions; chatbot may omit PDO-first wording.

### TC-CRIMINAL-003
- **Scenario Title:** PR with PDO application processing
- **User Storyline:** Applicant is a PR and has been charged for a non-capital offence. She already submitted her PDO application last week and wants PBSG to chase it because she is anxious.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Criminal matter; non-capital; charged; PR; PDO application is already processing.
- **Expected Missing Information:** Deadline not stated; if no deadline cue, not urgent.
- **Expected Chatbot Behaviour:** Treat PDO as handling and direct questions to PDO.
- **Expected Route Or Next Question:** `GEN3-T02 Route C`, unless a concrete deadline within 14 days is later supplied.
- **Regression Risks:** Chatbot may route to PDO-first instead of PDO-is-handling; chatbot may promise to chase PDO.

### TC-CRIMINAL-004
- **Scenario Title:** Foreigner eligible for CLAS standard intake
- **User Storyline:** Applicant is a Malaysian work permit holder charged in court for a non-capital theft offence. Next court mention is in six weeks. He rents an HDB room, earns $1,800, supports his wife and child, and has about $2,000 savings.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Foreigner; charged in court; non-capital; no deadline within 14 days; PCHI is $600 for three-person household; non-private housing; savings below threshold.
- **Expected Missing Information:** Age is not stated, but savings are below both under-60 and 60+ thresholds.
- **Expected Chatbot Behaviour:** Auto-extract means facts and route to CLAS standard intake without repeating known financial questions.
- **Expected Route Or Next Question:** `GEN3-T02 Route E`.
- **Regression Risks:** Chatbot may ask for nationality or PCHI again; chatbot may treat work permit holder as PDO eligible; chatbot may assess merits.

### TC-CRIMINAL-005
- **Scenario Title:** Police investigation but not charged
- **User Storyline:** Applicant is a foreigner. Police called him for an interview about a fight at work, but he says he has not been charged in court and has no court papers. He wants a lawyer to go with him.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Criminal/police investigation; foreigner; not charged in court; no court date stated.
- **Expected Missing Information:** Civil/guidance eligibility details after handoff.
- **Expected Chatbot Behaviour:** Do not route to CLAS because charging in court is absent; hand off to civil/guidance stream.
- **Expected Route Or Next Question:** Proceed to `GEN3-T04`, starting with citizenship/residency and guidance eligibility as needed.
- **Regression Risks:** Chatbot may route to CLAS based on police investigation alone; chatbot may give advice about police interview attendance.

### TC-CRIMINAL-006
- **Scenario Title:** Criminal charge with court date within 14 days
- **User Storyline:** Applicant is an Indonesian cleaner charged for a non-capital offence. Her next State Courts date is this Friday, she has no lawyer, and she is very worried. She has been charged already.
- **Expected Primary Topic:** `GEN3-T02` with `GEN3-T06` overlay expected.
- **Facts Already Present:** Criminal matter; foreigner; charged; non-capital implied; court date within 14 days; no lawyer.
- **Expected Missing Information:** Urgent legal deadline details; after urgent leg, means facts for CLAS.
- **Expected Chatbot Behaviour:** Trigger urgent concurrent route before continuing the criminal parent flow.
- **Expected Route Or Next Question:** `GEN3-T02 Route D`, run `GEN3-T06` for deadline, then resume `GEN3-T02 Q3` and continue from known charged status.
- **Regression Risks:** Chatbot may skip `GEN3-T06`; chatbot may incorrectly restart at `GEN3-T01` after urgent triage; chatbot may ask safety crisis questions without safety facts.

## 3. Family Justice

### TC-FAMILY-001
- **Scenario Title:** SGC divorce, no LAB application
- **User Storyline:** Applicant is a Singapore Citizen. Her husband wants a divorce and she needs a lawyer for custody and maintenance. No violence, no urgent hearing, and she has not applied to LAB.
- **Expected Primary Topic:** `GEN3-T03`
- **Facts Already Present:** Family/matrimonial matter; SGC; no urgent violence/deadline; no LAB application.
- **Expected Missing Information:** Means details not needed before LAB-first branch.
- **Expected Chatbot Behaviour:** Route SGC applicant to LAB first.
- **Expected Route Or Next Question:** `GEN3-T03 Route B`.
- **Regression Risks:** Chatbot may route directly to FJSS; chatbot may ask child nationality even though applicant is SGC.

### TC-FAMILY-002
- **Scenario Title:** PR with LAB already handling
- **User Storyline:** Applicant is a PR in a divorce case. She applied to LAB and says LAB has accepted the application and is still processing. She asks whether PBSG can also give her another lawyer.
- **Expected Primary Topic:** `GEN3-T03`
- **Facts Already Present:** Matrimonial matter; PR; LAB application accepted/processing; no urgent facts.
- **Expected Missing Information:** None needed for LAB-is-handling branch.
- **Expected Chatbot Behaviour:** Direct LAB queries back to LAB and avoid duplicate PBSG representation routing.
- **Expected Route Or Next Question:** `GEN3-T03 Route C`.
- **Regression Risks:** Chatbot may ask FJSS means questions; chatbot may imply PBSG can appoint another lawyer.

### TC-FAMILY-003
- **Scenario Title:** Foreigner with Singapore Citizen child under 21
- **User Storyline:** Applicant is a Vietnamese mother going through divorce in Singapore. She is not a PR or citizen. Her child is a Singapore Citizen and is 8 years old. She lives in a rented HDB room, earns $1,200, and has very little savings.
- **Expected Primary Topic:** `GEN3-T03`
- **Facts Already Present:** Matrimonial matter; foreigner; has Singapore Citizen child under 21; income and housing likely within FJSS pro bono means; savings low.
- **Expected Missing Information:** Household size for exact PCHI may be needed if not inferable; urgency not indicated.
- **Expected Chatbot Behaviour:** Ask only for missing means detail if needed; do not ask whether the applicant is SGC/PR again.
- **Expected Route Or Next Question:** Ask household size for PCHI, or route `GEN3-T03 Route D` if enough means facts are accepted.
- **Regression Risks:** Chatbot may ask the wrong Q4 about applicant nationality instead of child citizenship; chatbot may hand off to civil prematurely.

### TC-FAMILY-004
- **Scenario Title:** Foreigner without qualifying Singapore Citizen child
- **User Storyline:** Applicant is a foreign spouse seeking divorce. She is on an LTVP, has no children, and says there is no violence or urgent date yet. She wants to know if PBSG can represent her.
- **Expected Primary Topic:** `GEN3-T03`
- **Facts Already Present:** Matrimonial matter; foreigner; no Singapore Citizen child under 21; no urgency.
- **Expected Missing Information:** Civil/guidance eligibility details after handoff.
- **Expected Chatbot Behaviour:** Follow foreigner path to Q4 and then hand off to civil/guidance stream, not FJSS.
- **Expected Route Or Next Question:** Proceed to `GEN3-T04`.
- **Regression Risks:** Chatbot may route all foreigners in divorce to FJSS; chatbot may ask LAB questions meant for SGC/PR.

### TC-FAMILY-005
- **Scenario Title:** Modest means family pathway
- **User Storyline:** Applicant is a PR divorcing her spouse. LAB rejected her because she marginally failed the means test. She lives in a 4-room HDB, earns about $3,900 for a household of three, and savings are around $8,000.
- **Expected Primary Topic:** `GEN3-T03`
- **Facts Already Present:** Matrimonial matter; PR; LAB failed means test; PCHI about $1,300; HDB; savings under marginal threshold.
- **Expected Missing Information:** No urgent facts; age not needed if savings below both thresholds.
- **Expected Chatbot Behaviour:** Route to FJSS Modest Means rather than LAB-first.
- **Expected Route Or Next Question:** `GEN3-T03 Route E`.
- **Regression Risks:** Chatbot may send her back to LAB despite rejection; chatbot may route to pro bono instead of modest means.

### TC-FAMILY-006
- **Scenario Title:** Family violence with urgent concurrent flow
- **User Storyline:** Applicant wants a divorce and says her husband hit her yesterday. She is scared to go home tonight and also has a PPO mention next week. She is not sure what scheme she needs.
- **Expected Primary Topic:** `GEN3-T03` with `GEN3-T06` overlay expected.
- **Facts Already Present:** Family/matrimonial matter; active/recent violence; unsafe housing tonight; PPO date within 14 days; scheme unknown.
- **Expected Missing Information:** Immediate current safety; citizenship/PR after urgent triage.
- **Expected Chatbot Behaviour:** Run urgent triage first, likely emergency/social support depending current safety/shelter, then resume `GEN3-T03 Q2`.
- **Expected Route Or Next Question:** `GEN3-T03 Route A`; ask the urgent safety/basic-needs question or route to `GEN3-T06 Route A/B` if facts are enough.
- **Regression Risks:** Chatbot may continue normal FJSS questions while safety is unresolved; chatbot may restart at `GEN3-T01` after urgent leg.

## 4. Civil

### TC-CIVIL-001
- **Scenario Title:** Foreigner seeks initial guidance for employment issue
- **User Storyline:** Applicant is a Bangladeshi worker. His employer has not paid two months salary. He wants to speak to a lawyer for basic guidance, not court representation. He stays in a dorm and has little savings.
- **Expected Primary Topic:** `GEN3-T04`
- **Facts Already Present:** Civil/employment matter; foreigner; seeking guidance; non-private housing; low savings; likely low income.
- **Expected Missing Information:** Exact income/household size if means test requires it.
- **Expected Chatbot Behaviour:** Ask only missing means facts for legal clinic eligibility.
- **Expected Route Or Next Question:** Ask monthly income and household size for PCHI, then likely `GEN3-T04 Route A`.
- **Regression Risks:** Chatbot may send foreigner to LAB; chatbot may give employment law advice; chatbot may ask representation/guidance question already answered.

### TC-CIVIL-002
- **Scenario Title:** SGC wants civil representation and has not applied to LAB
- **User Storyline:** Applicant is a Singapore Citizen suing a contractor for bad renovation work. He wants a lawyer to act for him in court, has no current lawyer, and has not applied to LAB.
- **Expected Primary Topic:** `GEN3-T04`
- **Facts Already Present:** Civil contract dispute; SGC; seeking representation; no LAB application; no current lawyer.
- **Expected Missing Information:** Means details not needed before LAB referral.
- **Expected Chatbot Behaviour:** Route to LAB first.
- **Expected Route Or Next Question:** `GEN3-T04 Route B`.
- **Regression Risks:** Chatbot may offer PBSG legal clinic as representation; chatbot may ask means questions before LAB-first.

### TC-CIVIL-003
- **Scenario Title:** LAB unable to assist, guidance still possible
- **User Storyline:** Applicant is a PR with a tenancy dispute. LAB told him they cannot assist. He only wants initial advice on what options exist. He lives in HDB with his family and earns $3,000 for a household of four.
- **Expected Primary Topic:** `GEN3-T04`
- **Facts Already Present:** Civil tenancy matter; PR; LAB unable to assist; seeking guidance; HDB; PCHI $750.
- **Expected Missing Information:** Savings and age if needed for legal clinic means.
- **Expected Chatbot Behaviour:** Do not send back to LAB; continue to means check for legal clinic.
- **Expected Route Or Next Question:** Ask savings/age if needed, then likely `GEN3-T04 Route A`.
- **Regression Risks:** Chatbot may loop to LAB referral; chatbot may reject because LAB declined without checking guidance pathway.

### TC-CIVIL-004
- **Scenario Title:** Marginal means with hardship should escalate
- **User Storyline:** Applicant is a Singapore Citizen with a debt claim. He earns above the usual clinic threshold because of overtime this month, but he is supporting two elderly parents, has heavy medical bills, and lives in a 3-room HDB. He wants guidance.
- **Expected Primary Topic:** `GEN3-T04`
- **Facts Already Present:** Civil debt matter; SGC; seeking guidance; possible over-threshold income; non-private housing; hardship/exceptional circumstances.
- **Expected Missing Information:** Exact PCHI, savings, and age.
- **Expected Chatbot Behaviour:** Ask concise means facts if needed; if over-threshold but hardship remains, escalate rather than reject.
- **Expected Route Or Next Question:** Likely `GEN3-T04 Route C` after means facts or hardship confirmation.
- **Regression Risks:** Chatbot may reject solely on income; chatbot may ignore exceptional circumstances.

### TC-CIVIL-005
- **Scenario Title:** Well-over means without exception
- **User Storyline:** Applicant is a PR with a neighbour dispute. He wants general advice. He lives in private condo, household income is about $18,000 for two people, savings are high, and he does not mention hardship.
- **Expected Primary Topic:** `GEN3-T04`
- **Facts Already Present:** Civil/neighbour matter; PR; seeking guidance; private housing; PCHI $9,000; high savings; no exception cue.
- **Expected Missing Information:** None material.
- **Expected Chatbot Behaviour:** Stop after means branch and provide self-help resources, not legal advice.
- **Expected Route Or Next Question:** `GEN3-T04 Route D`.
- **Regression Risks:** Chatbot may route to legal clinic despite clear means failure; chatbot may opine on the neighbour dispute.

## 5. Urgent

### TC-URGENT-001
- **Scenario Title:** Immediate physical danger
- **User Storyline:** Applicant whispers that her ex-partner is outside the door right now threatening to hit her. She is hiding in the toilet and asks if she can get legal help later.
- **Expected Primary Topic:** `GEN3-T06`
- **Facts Already Present:** Immediate safety threat; possible family violence; legal matter can wait.
- **Expected Missing Information:** Normal legal triage details are not needed while safety risk is current.
- **Expected Chatbot Behaviour:** Prioritise emergency/crisis support and stop legal triage until immediate safety is addressed.
- **Expected Route Or Next Question:** `GEN3-T06 Route A`.
- **Regression Risks:** Chatbot may ask divorce/PPO eligibility questions before emergency support; chatbot may provide legal advice.

### TC-URGENT-002
- **Scenario Title:** Self-harm risk
- **User Storyline:** Applicant says he lost his job and received a bankruptcy letter. He says he cannot take it anymore and might hurt himself tonight. He is alone at home.
- **Expected Primary Topic:** `GEN3-T06`
- **Facts Already Present:** Self-harm risk tonight; civil/bankruptcy issue secondary.
- **Expected Missing Information:** Legal details should wait until crisis support is addressed.
- **Expected Chatbot Behaviour:** Use immediate crisis pathway and avoid continuing ordinary civil triage.
- **Expected Route Or Next Question:** `GEN3-T06 Route A`.
- **Regression Risks:** Chatbot may focus on bankruptcy resources; chatbot may ask means questions before crisis support.

### TC-URGENT-003
- **Scenario Title:** No safe place tonight
- **User Storyline:** Applicant says the landlord changed the lock today. She has two children with her and no place to sleep tonight. She also wants to know about her tenancy rights.
- **Expected Primary Topic:** `GEN3-T06`
- **Facts Already Present:** Imminent homelessness/no safe place tonight; children affected; tenancy legal issue remains.
- **Expected Missing Information:** Postal code for SSO/FSC lookup; later civil triage details.
- **Expected Chatbot Behaviour:** Route to social support first and continue legal triage concurrently after noting urgent support path.
- **Expected Route Or Next Question:** `GEN3-T06 Route B`, ask for postal code if lookup is needed, then return to `GEN3-T01`/civil triage.
- **Regression Risks:** Chatbot may treat this only as landlord-tenant guidance; chatbot may omit child/basic-needs urgency.

### TC-URGENT-004
- **Scenario Title:** Civil filing deadline within 14 days
- **User Storyline:** Applicant has to file a response to a civil claim by next Monday. There is no violence and no safety issue. He just found the papers and has no lawyer.
- **Expected Primary Topic:** `GEN3-T06` with later `GEN3-T01`/`GEN3-T04` continuation.
- **Facts Already Present:** Concrete legal/procedural deadline within 14 days; civil matter; no immediate safety issue; no lawyer.
- **Expected Missing Information:** Deadline date, type of document, current representation status details, civil eligibility.
- **Expected Chatbot Behaviour:** Use urgent legal consultation assessment, then continue normal triage.
- **Expected Route Or Next Question:** `GEN3-T06 Route C`, then `GEN3-T06 Route D` to normal triage.
- **Regression Risks:** Chatbot may ask emergency safety questions despite no safety facts; chatbot may skip normal triage after urgent route.

### TC-URGENT-005
- **Scenario Title:** Serious legal words but no urgency
- **User Storyline:** Applicant says he may go to jail because he was charged last year, but his next court date is three months away. He is worried but not in danger, not detained, and has no deadline this month.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Criminal matter; charged; no immediate danger; no detention; no deadline within 14 days.
- **Expected Missing Information:** Capital/non-capital, nationality/residency, PDO/CLAS path.
- **Expected Chatbot Behaviour:** Do not trigger urgent route merely because of jail/court words; proceed with criminal triage.
- **Expected Route Or Next Question:** Ask `GEN3-T02 Q1` about capital offence if not known.
- **Regression Risks:** Chatbot may over-trigger `GEN3-T06`; chatbot may ask crisis questions due to the word jail.

## 6. Vulnerability

### TC-VULN-001
- **Scenario Title:** Confirmed minor applicant
- **User Storyline:** Caller says, I am 16 and I need help because my stepfather is threatening to throw me out. I do not want to tell my school yet.
- **Expected Primary Topic:** `GEN3-T13` with `GEN3-T06` overlay expected.
- **Facts Already Present:** Applicant is under 18; possible safety/basic-needs risk; family/household issue.
- **Expected Missing Information:** Immediate safety and shelter status only if not already clear.
- **Expected Chatbot Behaviour:** Do not continue ordinary triage independently; escalate minor handling and address urgent safety/basic needs first if present.
- **Expected Route Or Next Question:** `GEN3-T13 Route B`; if unsafe now or no shelter tonight, `GEN3-T06 Route A/B` first.
- **Regression Risks:** Chatbot may ask sensitive details from a minor; chatbot may treat as ordinary civil/family dispute.

### TC-VULN-002
- **Scenario Title:** Limited English but can self-help
- **User Storyline:** Applicant speaks simple English and asks the intern to go slowly. He can answer questions and use WhatsApp, but wants the steps repeated. The matter is a wage claim.
- **Expected Primary Topic:** `GEN3-T04` with `GEN3-T13` low-vulnerability adaptation.
- **Facts Already Present:** Employment/wage civil matter; one non-severe communication cue; applicant can communicate and self-help with adaptation.
- **Expected Missing Information:** Citizenship/residency, guidance/representation, means facts.
- **Expected Chatbot Behaviour:** Continue standard civil triage with slower pace and note language adaptation; do not escalate high vulnerability solely for limited English.
- **Expected Route Or Next Question:** Ask `GEN3-T04 Q1` about Singapore Citizen/PR status.
- **Regression Risks:** Chatbot may over-escalate to `GEN3-T13 Route A`; chatbot may ignore the need for adapted wording.

### TC-VULN-003
- **Scenario Title:** Social worker involvement without crisis
- **User Storyline:** Applicant says her FSC social worker told her to call PBSG about a maintenance issue. She can explain clearly, has no safety concern today, and the social worker is only helping her gather documents.
- **Expected Primary Topic:** `GEN3-T03` with `GEN3-T13 Route C` adaptation note.
- **Facts Already Present:** Family/maintenance matter; FSC/social worker involvement; no current safety issue; applicant can communicate.
- **Expected Missing Information:** Citizenship/PR and LAB/FJSS path.
- **Expected Chatbot Behaviour:** Do not treat social worker involvement alone as high vulnerability; continue family triage.
- **Expected Route Or Next Question:** Ask `GEN3-T03 Q1` only if urgency not already answered; otherwise ask `GEN3-T03 Q2`.
- **Regression Risks:** Chatbot may route all FSC cases to high-vulnerability escalation; chatbot may re-ask safety despite clear denial.

### TC-VULN-004
- **Scenario Title:** Disability prevents self-application
- **User Storyline:** Applicant has a visual disability and says online forms are impossible for him without help. He is calling about a civil claim and has no family member who can assist. He can explain the facts clearly.
- **Expected Primary Topic:** `GEN3-T04` with `GEN3-T13` overlay expected.
- **Facts Already Present:** Civil matter; disability/access barrier; no helper; can communicate; likely needs support for application process.
- **Expected Missing Information:** Citizenship/PR, representation/guidance, means facts.
- **Expected Chatbot Behaviour:** Continue civil triage but flag vulnerability/access support; escalate if inability to self-apply prevents access to route.
- **Expected Route Or Next Question:** Ask `GEN3-T04 Q1`; if legal clinic application is later appropriate and he cannot apply/attend, use `GEN3-T04 Route C`.
- **Regression Risks:** Chatbot may ignore access barrier; chatbot may wrongly stop at disability without triaging legal issue.

### TC-VULN-005
- **Scenario Title:** Helper appears controlling
- **User Storyline:** A man answers for the applicant and refuses to let her speak. He says she wants to withdraw a PPO application against him and keeps telling the intern not to ask her questions.
- **Expected Primary Topic:** `GEN3-T13` with `GEN3-T06` possible depending safety facts.
- **Facts Already Present:** Helper controlling conversation; family violence/PPO context; possible coercive control.
- **Expected Missing Information:** Whether applicant is safe now; whether she can speak privately.
- **Expected Chatbot Behaviour:** Treat as high vulnerability/coercion concern, avoid ordinary family routing until safe handling is assessed.
- **Expected Route Or Next Question:** `GEN3-T13 Route A`; ask only a safe, minimal clarification if feasible, otherwise escalate to PBSG Staff.
- **Regression Risks:** Chatbot may accept helper's statement and route as ordinary PPO withdrawal; chatbot may ask unsafe questions.

## 7. Multi-Topic

### TC-MULTI-001
- **Scenario Title:** Criminal charge and divorce
- **User Storyline:** Applicant says she has a theft charge in State Courts and also wants to divorce her husband. She is a Singapore Citizen. Criminal court is in five days; the divorce has no hearing date yet.
- **Expected Primary Topic:** `GEN3-T02` with queued `GEN3-T03` and `GEN3-T06` overlay expected.
- **Facts Already Present:** Criminal charge; matrimonial issue; SGC; criminal court date within 14 days; divorce no deadline.
- **Expected Missing Information:** Capital/non-capital if not inferable; PDO status after urgent leg.
- **Expected Chatbot Behaviour:** Prioritise criminal plus urgent deadline, then continue to divorce topic after criminal route is handled.
- **Expected Route Or Next Question:** Trigger `GEN3-T02 Route D`, run `GEN3-T06`, resume `GEN3-T02 Q3`, and queue `GEN3-T03`.
- **Regression Risks:** Chatbot may prioritise divorce first; chatbot may lose queued family topic; chatbot may restart at first contact after urgent triage.

### TC-MULTI-002
- **Scenario Title:** Civil debt plus family violence
- **User Storyline:** Applicant asks about a credit card debt letter but then says her husband beat her last night and she cannot safely stay at home. She also has a maintenance problem but says the debt letter is what scared her into calling.
- **Expected Primary Topic:** `GEN3-T03` or `GEN3-T06` first-action priority, with `GEN3-T04` queued.
- **Facts Already Present:** Family violence; unsafe housing; maintenance/family issue; civil debt issue.
- **Expected Missing Information:** Immediate safety; shelter/postal code; later family and civil eligibility.
- **Expected Chatbot Behaviour:** Address urgent safety/basic-needs first, prioritise family violence topic before debt, then continue queued civil debt.
- **Expected Route Or Next Question:** `GEN3-T06 Route A/B` depending current danger/shelter, then resume family triage at `GEN3-T03 Q2`.
- **Regression Risks:** Chatbot may focus on debt because it appears first; chatbot may skip safety route; chatbot may drop civil topic after family route.

### TC-MULTI-003
- **Scenario Title:** Elderly debt with court deadline
- **User Storyline:** Applicant is 78 and confused about a debt claim. His court response is due next week. He says his niece is helping him read letters because he cannot understand them well, but he is not in danger.
- **Expected Primary Topic:** `GEN3-T04` with `GEN3-T06` and `GEN3-T13` overlays expected.
- **Facts Already Present:** Civil debt matter; legal deadline within 14 days; elderly/confusion cue; helper assisting with documents; no safety danger.
- **Expected Missing Information:** Citizenship/PR; representation/guidance; means; degree of confusion/access support.
- **Expected Chatbot Behaviour:** Flag urgent legal deadline and vulnerability adaptation/support, then continue civil triage.
- **Expected Route Or Next Question:** `GEN3-T06 Route C`, `GEN3-T13 Route A` if serious confusion prevents self-help or `Route C` if manageable, then `GEN3-T04 Q1`.
- **Regression Risks:** Chatbot may ignore vulnerability; chatbot may treat elderly alone as high vulnerability without checking function; chatbot may skip urgent deadline.

### TC-MULTI-004
- **Scenario Title:** Foreigner divorce plus no shelter tonight
- **User Storyline:** Applicant is a foreign spouse seeking divorce. She has a Singaporean child aged 6, earns about $1,000, and says her husband locked her out tonight. She has no safe place to sleep.
- **Expected Primary Topic:** `GEN3-T03` with `GEN3-T06` overlay expected.
- **Facts Already Present:** Matrimonial matter; foreigner; Singapore Citizen child under 21; low income; no safe shelter tonight.
- **Expected Missing Information:** Immediate danger; household size/savings later if not enough for FJSS means.
- **Expected Chatbot Behaviour:** Run urgent basic-needs/safety triage first, then resume family flow at Q2/Q4 facts without re-asking known child citizenship.
- **Expected Route Or Next Question:** `GEN3-T06 Route B` or `Route A` if immediate danger; then continue toward `GEN3-T03 Route D` if means facts pass.
- **Regression Risks:** Chatbot may ask whether applicant is SGC/PR again after already stating foreigner; chatbot may skip shelter urgency.

### TC-MULTI-005
- **Scenario Title:** Criminal and civil employment facts in one paragraph
- **User Storyline:** Applicant is a foreign worker. He was charged after a dormitory fight and has State Courts in three weeks. Separately, his employer has not paid his salary. He lives in dormitory and earns $1,200.
- **Expected Primary Topic:** `GEN3-T02` with queued `GEN3-T04`.
- **Facts Already Present:** Criminal charge; no 14-day deadline; foreigner; civil employment wage issue; dormitory housing; income.
- **Expected Missing Information:** Capital/non-capital, charged status if not already enough, household size/savings for CLAS and clinic eligibility.
- **Expected Chatbot Behaviour:** Handle criminal first because it is more serious, then continue to civil employment issue.
- **Expected Route Or Next Question:** Ask `GEN3-T02 Q1` if non-capital not known; queue `GEN3-T04`.
- **Regression Risks:** Chatbot may select employment/civil first; chatbot may merge CLAS and legal clinic eligibility incorrectly.

## 8. Out-of-Scope

### TC-OOS-001
- **Scenario Title:** Weather question
- **User Storyline:** Is it going to rain in Tampines later? I just want to know if I should bring umbrella.
- **Expected Primary Topic:** Out-of-scope
- **Facts Already Present:** No legal issue; weather query.
- **Expected Missing Information:** None.
- **Expected Chatbot Behaviour:** Decline or redirect briefly without running full triage.
- **Expected Route Or Next Question:** No Golden Set route; short out-of-scope response.
- **Regression Risks:** Chatbot may force first-contact triage for casual non-legal query; chatbot may hallucinate weather answer.

### TC-OOS-002
- **Scenario Title:** Joke request
- **User Storyline:** Tell me a funny joke about lawyers.
- **Expected Primary Topic:** Out-of-scope
- **Facts Already Present:** No applicant or legal triage facts; entertainment request.
- **Expected Missing Information:** None.
- **Expected Chatbot Behaviour:** Avoid RAG triage and give brief scope reminder if query router is enabled.
- **Expected Route Or Next Question:** No route; out-of-scope response.
- **Regression Risks:** Chatbot may answer with a joke instead of scope message; chatbot may enter `GEN3-T01` unnecessarily.

### TC-OOS-003
- **Scenario Title:** General knowledge question
- **User Storyline:** Who was the first Prime Minister of Singapore?
- **Expected Primary Topic:** Out-of-scope
- **Facts Already Present:** General knowledge question; no legal issue.
- **Expected Missing Information:** None.
- **Expected Chatbot Behaviour:** Decline as outside legal triage scope without invoking Golden Set routing.
- **Expected Route Or Next Question:** No route; out-of-scope response.
- **Regression Risks:** Chatbot may answer general knowledge; chatbot may ask triage questions for a non-legal query.

## 9. Ambiguous / Correction Cases

### TC-AMBIG-001
- **Scenario Title:** Too little information but not a greeting
- **User Storyline:** Applicant says, I got a letter and I think I need legal help. I do not know what type of case it is. He has no lawyer but cannot explain further yet.
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** No current lawyer; legal letter; matter type unclear.
- **Expected Missing Information:** Whether caller is self; personal/business; prior advice; matter type; urgency if letter has a deadline.
- **Expected Chatbot Behaviour:** Ask exactly one clarifying question that moves first-contact triage forward.
- **Expected Route Or Next Question:** Ask whether the applicant is the person needing help or calling on behalf, or ask what the letter is about if Q2 is already known.
- **Regression Risks:** Chatbot may route to civil by default; chatbot may ask a long list of questions; chatbot may ignore possible deadline.
- **Suggested Follow-Up Turns:** `It is a court letter and response due next week.` Expected: trigger `GEN3-T06 Route C` then continue first-contact/civil triage.

### TC-AMBIG-002
- **Scenario Title:** Personal or business capacity unclear
- **User Storyline:** Applicant says he guaranteed a loan for his company and now the bank is chasing him personally. He is not sure whether this is his business issue or personal issue. No lawyer yet.
- **Expected Primary Topic:** `GEN3-T01`
- **Facts Already Present:** No lawyer; debt/loan issue; potential business and personal capacity overlap.
- **Expected Missing Information:** Whether the matter arises in personal capacity or as a business/commercial dispute.
- **Expected Chatbot Behaviour:** Clarify once under `GEN3-T01 Q3`; if still unsure, escalate.
- **Expected Route Or Next Question:** Ask whether the bank is pursuing him personally as an individual or the company/business.
- **Regression Risks:** Chatbot may immediately reject as business; chatbot may immediately route civil; chatbot may loop on Q3 instead of clarifying once.
- **Suggested Follow-Up Turns:** `The bank letter is addressed to me personally as guarantor.` Expected: proceed to Q4/Q5 as personal civil matter. `I still don't know.` Expected: `GEN3-T01 Route F`.

### TC-AMBIG-003
- **Scenario Title:** Family nationality unclear once
- **User Storyline:** Applicant wants help with divorce and custody. She says she has lived here many years but does not know whether her status counts as PR. She has a blue IC but is confused.
- **Expected Primary Topic:** `GEN3-T03`
- **Facts Already Present:** Family/matrimonial issue; nationality/residency unclear; no urgent facts stated.
- **Expected Missing Information:** Whether applicant is SGC/PR; LAB or foreigner-child path depends on this.
- **Expected Chatbot Behaviour:** Clarify nationality/residency once; if still unclear, escalate.
- **Expected Route Or Next Question:** Ask whether she is a Singapore Citizen or Permanent Resident.
- **Regression Risks:** Chatbot may assume PR from blue IC without clarification; chatbot may ask child citizenship before applicant status.
- **Suggested Follow-Up Turns:** `Yes I am PR.` Expected: ask LAB application status. `I still don't know.` Expected: `GEN3-T03 Route F`.

### TC-AMBIG-004
- **Scenario Title:** Corrected answer changes criminal path
- **User Storyline:** Applicant first says he is a Singapore Citizen charged for a fight and has not applied to PDO. After the chatbot starts explaining PDO, he corrects himself: actually he is on a work permit, not Singapore Citizen or PR.
- **Expected Primary Topic:** `GEN3-T02`
- **Facts Already Present:** Criminal charge; initial SGC answer corrected to foreigner; no PDO route should remain after correction.
- **Expected Missing Information:** Capital/non-capital, deadline within 14 days, charged status if not already accepted, means facts for CLAS.
- **Expected Chatbot Behaviour:** Update the fact map and switch from PDO path to foreigner/CLAS path without scolding or preserving stale answer.
- **Expected Route Or Next Question:** Ask the next missing `GEN3-T02` question for foreigner path, likely means facts after charged/non-capital/deadline are known.
- **Regression Risks:** Chatbot may keep the stale SGC route; chatbot may output conflicting PDO and CLAS instructions.

### TC-AMBIG-005
- **Scenario Title:** Urgent not sure should not loop
- **User Storyline:** Applicant has a hearing soon but cannot find the date. She thinks it may be within two weeks but is not sure. She has no safety concern and no lawyer.
- **Expected Primary Topic:** `GEN3-T06` with normal triage continuation.
- **Facts Already Present:** Possible legal/procedural deadline; no safety issue; no lawyer.
- **Expected Missing Information:** Exact hearing date and matter type.
- **Expected Chatbot Behaviour:** Treat deadline uncertainty as needing human review or urgent precaution depending parent flow; do not ask repeated date questions in a loop.
- **Expected Route Or Next Question:** `GEN3-T06 Route F` if standalone deadline unclear, or parent route's precautionary urgent branch if nested under criminal/family.
- **Regression Risks:** Chatbot may keep asking for the same date; chatbot may ignore possible 14-day deadline; chatbot may incorrectly route to emergency safety.

## Coverage Checklist

- **Bare greeting / vague first-contact cases:** `TC-FIRST-CONTACT-001`, `TC-FIRST-CONTACT-002`, `TC-FIRST-CONTACT-003`, `TC-FIRST-CONTACT-004`, `TC-FIRST-CONTACT-005`, `TC-FIRST-CONTACT-006`
- **Criminal legal aid cases:** `TC-CRIMINAL-001` through `TC-CRIMINAL-006`, plus `TC-MULTI-001` and `TC-MULTI-005`
- **Family justice cases:** `TC-FAMILY-001` through `TC-FAMILY-006`, plus `TC-MULTI-001`, `TC-MULTI-002`, and `TC-MULTI-004`
- **Civil law cases:** `TC-CIVIL-001` through `TC-CIVIL-005`, plus `TC-MULTI-002`, `TC-MULTI-003`, and `TC-MULTI-005`
- **Urgent concurrent issue cases:** `TC-CRIMINAL-006`, `TC-FAMILY-006`, `TC-URGENT-001` through `TC-URGENT-005`, `TC-MULTI-001` through `TC-MULTI-004`, `TC-AMBIG-005`
- **Vulnerability/minor cases:** `TC-FIRST-CONTACT-004`, `TC-VULN-001` through `TC-VULN-005`, `TC-MULTI-003`
- **Multi-topic cases:** `TC-MULTI-001` through `TC-MULTI-005`
- **Out-of-scope cases:** `TC-OOS-001` through `TC-OOS-003`
- **Ambiguous / not-sure / correction cases:** `TC-AMBIG-001` through `TC-AMBIG-005`, plus `TC-FIRST-CONTACT-002`, `TC-FAMILY-003`, and `TC-MULTI-003`
- **Special edge cases covered:** bare greeting; Q2 caller-on-behalf able/unable self-help; Q3 personal/business unclear; nonprofit vs for-profit; criminal urgent nested flow; family urgent nested flow; foreign family child citizenship path; urgent not-sure/no-loop; minor/safety/FSC/social worker/vulnerability cues; criminal plus divorce; civil debt plus family violence; out-of-scope weather/joke/general knowledge; sparse information; auto-extraction of multiple facts; corrected earlier answer.

## Evaluation Rubric

Use these pass/fail checks when reviewing chatbot outputs:

- **Correct topic selection:** The selected entry, active workflow, queued workflow, and overlay workflow match the expected topic behaviour for the case.
- **Correct route or next question:** The response follows the deterministic Golden Set branch and uses the correct route label or asks the single next required question.
- **Correct auto-extraction:** Facts stated in the storyline are carried forward and not asked again.
- **No repeated questions:** The chatbot does not ask for information already known and does not loop on unclear answers after the allowed clarification.
- **No premature routing:** The chatbot does not terminate before required branching facts are known, especially for means tests, nationality/residency, PDO/LAB status, and child citizenship.
- **Urgent/vulnerability handling:** Immediate safety, shelter/basic needs, deadlines within 14 days, minor status, coercion, disability, and access barriers trigger the appropriate overlay or escalation without derailing the parent resume point.
- **Clear intern-facing script:** Terminal routes include short wording an intern can read aloud, with operational steps kept separate from applicant-facing wording.
- **No hallucinated legal advice:** The response does not interpret documents, assess merits, predict outcomes, or tell the applicant what legal step to take beyond scheme routing and approved resources.
- **Concise and scannable:** The response is short enough for an intern to use while the applicant is present, with route/next-question information visible quickly.
