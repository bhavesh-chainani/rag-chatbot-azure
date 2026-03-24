import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = ROOT / "data" / "2025.11.26 Legal Help Triaging Flow Chart.drawio (1).pdf"
OUTPUT_JSON = ROOT / "data" / "legal_help_triage_flowchart_2025_11_26.json"


def extract_text(pdf_path: Path) -> str:
    return subprocess.check_output(["textutil", "-convert", "txt", "-stdout", str(pdf_path)], text=True)


def build_structured_payload(raw_text: str) -> dict:
    # This flowchart PDF is visually dense and PDF text extraction loses edge/arrow structure.
    # We store a curated node graph with normalized criteria and preserve raw text for traceability.
    return {
        "document_id": "PBSG-LHTF-2025-11-26",
        "title": "Legal Help Triaging Flow Chart",
        "version_date": "2025-11-26",
        "source_file": SOURCE_PDF.name,
        "disclaimer": "For internal reference only",
        "schema_version": "1.0",
        "intake_dimensions": [
            "matter_type",
            "legal_representation_status",
            "citizenship_or_pr",
            "income_assets_property_eligibility",
            "court_stage",
            "vulnerability",
            "representation_preference",
        ],
        "criteria_sets": {
            "financial_standard_1050": {
                "pchi_max": 1050,
                "single_property_annual_value_max": 21000,
                "savings_investments_max_under_60": 10000,
                "savings_investments_max_60_plus": 40000,
                "no_private_property": True,
            },
            "financial_modest_means_1450": {
                "pchi_max": 1450,
                "single_property_annual_value_max": 21000,
                "savings_investments_max_under_60": 12000,
                "savings_investments_max_60_plus": 48000,
                "no_private_property": True,
            },
            "financial_clinic_1650_or_hhi_1900": {
                "pchi_max": 1650,
                "hhi_max": 1900,
                "single_property_annual_value_max": 21000,
                "savings_investments_max_under_60": 10000,
                "savings_investments_max_60_plus": 40000,
            },
            "criminal_5000": {
                "pchi_max": 5000,
                "savings_investments_max_under_60": 10000,
                "savings_investments_max_60_plus": 40000,
                "no_private_property": True,
            },
        },
        "matter_buckets": {
            "civil": {},
            "criminal": {},
            "family": {},
        },
        "routes": [
            {"route_id": "R-LASCO", "label": "LASCO", "type": "capital_offence_legal_aid"},
            {"route_id": "R-PDO", "label": "Public Defender's Office", "type": "criminal_defence"},
            {"route_id": "R-CLAS", "label": "Criminal Legal Aid Scheme", "type": "criminal_defence"},
            {"route_id": "R-CLC", "label": "Criminal Legal Clinic", "type": "advice_clinic"},
            {"route_id": "R-LAB", "label": "Legal Aid Bureau", "type": "civil_family_legal_aid"},
            {
                "route_id": "R-FJSS-PB",
                "label": "Family Justice Support Scheme (Pro Bono)",
                "type": "family_representation",
            },
            {
                "route_id": "R-FJSS-MM",
                "label": "Family Justice Support Scheme (Modest Means)",
                "type": "family_representation",
            },
            {"route_id": "R-CLINICS", "label": "Legal Clinics / External Clinics", "type": "advice_clinic"},
            {"route_id": "R-DIRLAW", "label": "Directory of Lawyers", "type": "private_referral"},
            {"route_id": "R-COMMUNITY-LAW", "label": "Community Law Centre", "type": "community_support"},
        ],
        "decision_nodes": [
            {
                "node_id": "N1",
                "question": "What type of matter are you currently facing?",
                "options": ["civil", "criminal", "family"],
            },
            {
                "node_id": "N2",
                "question": "Are you currently legally represented?",
                "options": ["yes", "no"],
            },
            {
                "node_id": "N3-CRIMINAL",
                "question": "Criminal intake checks",
                "sub_questions": [
                    "Have you been formally charged in court?",
                    "Are you facing a capital offence?",
                    "Is offence regulatory / gambling-betting / organised-syndicate / terrorism / privately prosecuted?",
                    "Are you a Singaporean or PR?",
                ],
            },
            {
                "node_id": "N4-CRIMINAL-FINANCIAL",
                "question": "Do you meet criminal financial criteria?",
                "criteria_ref": "criminal_5000",
            },
            {
                "node_id": "N5-FAMILY-FINANCIAL-STRICT",
                "question": "Do you meet strict family aid criteria?",
                "criteria_ref": "financial_standard_1050",
                "additional": [
                    "If foreigner, have a Singaporean child",
                ],
            },
            {
                "node_id": "N6-FAMILY-FINANCIAL-MODEST",
                "question": "Do you meet modest means family criteria?",
                "criteria_ref": "financial_modest_means_1450",
                "additional": [
                    "If Singaporean/PR, have LAB rejection letter",
                    "If foreigner, have a Singaporean child",
                ],
            },
            {
                "node_id": "N7-CIVIL-CLINIC-ELIGIBILITY",
                "question": "Do you meet clinic-level civil/family thresholds?",
                "criteria_ref": "financial_clinic_1650_or_hhi_1900",
                "additional": [
                    "Not already received legal advice for same issue",
                    "Enquiring for own matter, not on behalf of another",
                    "Personal legal issue, not business/corporate",
                ],
            },
            {
                "node_id": "N8-FAMILY-MATTER-SCOPE",
                "question": "Is the matter in supported family scope?",
                "scope_items": [
                    "Civil divorce and Syariah divorce at PTC stage",
                    "Variation/enforcement of ancillary orders",
                    "Adoption",
                    "Children issues (custody/care-control/access)",
                    "Maintenance enforcement",
                    "PPO where adverse party is unrepresented",
                ],
            },
            {
                "node_id": "N9-CIVIL-EXCLUSIONS",
                "question": "Does the matter exclude listed unsupported areas?",
                "exclusions": [
                    "Defamation",
                    "Tribunal matters (SCT, CDRT, ECT, TADM, Tribunal for Maintenance of Parents)",
                    "Child / spousal maintenance enforcement",
                    "PPO where adverse party is unrepresented",
                ],
            },
            {
                "node_id": "N10-VULNERABILITY",
                "question": "Does applicant meet vulnerability criteria?",
                "criteria": [
                    "Sensitive case (e.g. sexual assault, trauma)",
                    "Physical disability",
                    "Requires high-touch assistance (e.g. low digital literacy, mental condition, special needs)",
                ],
            },
            {
                "node_id": "N11-REPRESENTATION-PREFERENCE",
                "question": "Do you want a lawyer to represent you?",
                "options": ["yes", "no_or_unsure"],
            },
        ],
        "pathway_summaries": [
            {
                "pathway_id": "P-CRIMINAL-CAPITAL",
                "if": ["matter_type=criminal", "capital_offence=yes"],
                "route": "R-LASCO",
            },
            {
                "pathway_id": "P-CRIMINAL-NONCAPITAL",
                "if": ["matter_type=criminal", "capital_offence=no", "sc_or_pr=yes"],
                "routes_priority": ["R-PDO", "R-CLAS", "R-CLC", "R-DIRLAW"],
            },
            {
                "pathway_id": "P-FAMILY-STRICT",
                "if": ["matter_type=family", "meets_financial_standard_1050=yes"],
                "routes_priority": ["R-FJSS-PB", "R-LAB"],
            },
            {
                "pathway_id": "P-FAMILY-MODEST",
                "if": ["matter_type=family", "meets_financial_standard_1050=no", "meets_financial_modest_1450=yes"],
                "route": "R-FJSS-MM",
            },
            {
                "pathway_id": "P-CIVIL-LEGAL-AID",
                "if": ["matter_type=civil", "sc_or_pr=yes", "meets_financial_standard_1050=yes"],
                "route": "R-LAB",
            },
            {
                "pathway_id": "P-CIVIL-CLINIC",
                "if": ["matter_type=civil", "meets_clinic_criteria=yes", "civil_exclusions=pass"],
                "route": "R-COMMUNITY-LAW",
            },
            {
                "pathway_id": "P-FALLBACK",
                "if": ["rejected_or_ineligible=yes"],
                "routes_priority": ["R-CLINICS", "R-DIRLAW"],
            },
        ],
        "quality_notes": [
            "This representation is normalized from a visual flow chart and may not preserve all arrow-level sequencing.",
            "Use for retrieval and triage support; validate edge cases against original operational guidance.",
            "Threshold values are preserved verbatim from extracted text blocks.",
        ],
        "raw_pdf_text": raw_text,
    }


def main() -> None:
    raw_text = extract_text(SOURCE_PDF)
    payload = build_structured_payload(raw_text)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote structured flowchart JSON to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
