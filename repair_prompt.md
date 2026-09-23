# Role

You are fixing the format of one JSON record produced by a research step. You have no
tools. Do not add information that is not already in the record.

# Task

Below are the record, the field names that failed validation, and the exact validation
errors. Return a JSON object containing ONLY the fields listed under "Fields to fix",
each with a corrected value that satisfies the schema. No other fields. Nothing before
or after the JSON object. No markdown code fences.

# Rules

- Derive every corrected value from the record itself (its other fields, notes, and
  evidence). Do not add facts.
- If the record does not determine the value, use the schema's fallback token:
  "unknown" for text fields, "none" for URL fields, null where the schema says null.
- Never invent a date, an amount, a stage, or a URL. A date whose month is not in the
  record becomes "unknown", not a guessed month.
- fit_score must be an integer 1–5. If the record gives a non-integer, choose the
  integer that the record's own fit_zh best supports.
- arr must start with a currency sign ($, €, or £) or be exactly "unknown". Keep the
  reporting currency as the source states it; never convert.
- If arr is flagged for forward-looking language (projected, target, forecast, bookings,
  expected, on track), replace it with the most recent ACTUAL figure that the record
  itself states (for example a prior-period or "up from" figure, formatted like
  '$60M (2024)'); if the record states no actual figure, use "unknown". Bookings are
  not ARR.
- If arr becomes "unknown", arr_confidence and arr_source_url are reset automatically;
  do not include them unless they are listed under "Fields to fix".
- Change nothing else. Do not rewrite prose.

# Output schema (field descriptions are authoritative)

{{paste context/output_schema.json here}}

# Record

{{record}}

# Fields to fix

{{fields}}

# Validation errors

{{errors}}
