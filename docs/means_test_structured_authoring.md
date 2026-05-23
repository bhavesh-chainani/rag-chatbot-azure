# Structured means-test authoring

Means-test questions must not rely only on prose. If a Golden Set question asks about PCHI, savings, housing, hardship, marginal eligibility, or exceptional circumstances, add a `means_test_structured` block to the question node in `data/pbsg_golden_set_by_id/<ENTRY_ID>.json`.

The prose remains the human-readable policy source. The structured block is the machine-readable policy source used by the deterministic routing engine.

## Required shape

```json
{
  "means_test_structured": {
    "fact_requirements": [
      "applicant.monthly_income",
      "applicant.household_size",
      "applicant.savings",
      "applicant.age",
      "applicant.housing_type"
    ],
    "pchi": {
      "income_fact": "applicant.monthly_income",
      "household_size_fact": "applicant.household_size",
      "formula": "monthly_income / household_size"
    },
    "branches": {
      "if_yes": {
        "route": "Route A",
        "label": "Scheme name",
        "conditions_all": [
          { "fact": "applicant.pchi", "lte": 1050 },
          { "fact": "applicant.housing_type", "equals": "non_private_housing" },
          { "fact": "applicant.savings", "lte_by_age": { "under_60": 10000, "60_or_over": 40000 }, "missing": "defer_to_application" }
        ]
      }
    }
  }
}
```

## Supported conditions

- `conditions_all`: every condition must match.
- `conditions_any`: at least one condition must match.
- `equals` / `not_equals`: exact normalized fact comparison.
- `lte` / `gt`: numeric comparison.
- `lte_by_age`: applies the `under_60` or `60_or_over` savings threshold using `applicant.age`.
- `missing: "defer_to_application"`: missing supporting documents, such as savings, do not block standard intake where the scheme verifies documents later.
- `missing_required_facts`: true when the applicant gave some means information but key facts like income or housing are still missing.
- `contradictory_financial_facts`: reserved for future contradiction checks.

## Current means nodes

- `GEN3-T02 Q6`: CLAS means test, `if_yes` routes to CLAS standard intake and `if_no` hands off to `GEN3-T04`.
- `GEN3-T03 Q5`: FJSS means test, `if_yes` routes to FJSS Pro Bono, `if_no_marginal` routes to FJSS Modest Means, and `if_no_well_over` hands off to `GEN3-T04`.
- `GEN3-T04 Q4`: civil and guidance means screen, where hardship or exceptional circumstances must route to staff escalation instead of self-help rejection.

## Publishing note

`scripts/build_pbsg_golden_set_json.py` preserves `means_test_structured` across Word-to-JSON regeneration, similar to `routing_structured`. If a means-test policy changes, update the structured JSON block as part of the publish process and run the PBSG routing tests before deployment.
