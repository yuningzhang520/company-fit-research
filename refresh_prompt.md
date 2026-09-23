# Task

You are updating ONE existing research record, for {{company}}, after a rule change.
Only the proof-point rule changed (see "Product facts" below). Everything else in the
record stays as it is.

Do this:
1. Read the record. Note its relationship_type, fit_target, the workload pattern it
   describes, and the company's industry.
2. Spend at most ONE web search, on `site:pingcap.com/customers <this company's industry,
   or its users' industry if the record says partner>`, looking for a customer reference
   whose workload pattern (per-tenant state, agent sandboxes, write-heavy multi-tenant)
   or industry matches. Do not search drive9.ai or sys9.ai: the only drive9 customer
   story (Kimi Projects) is already a lead proof point. You may fetch the one page you
   find to read the exact figure.
3. If you find a matching reference: rewrite fit_zh and pitch_en to include it, quoting
   the figure exactly as the page states it, and add the page URL to evidence_urls. Keep
   everything else in those two texts the same in substance: same relationship type,
   same workload argument, same friction points, same overall conclusion. Do not change
   the score or its rationale. fit_zh stays plain Simplified Chinese with product names in
   English; pitch_en stays English, 70–110 words; neither may contain funding, valuation
   or revenue figures. Never cite a PingCAP customer from a third-party page.
4. If nothing matches, or the only matches are the four lead proof points already
   available to every record, return exactly {} and change nothing.

Output: a single JSON object containing only the keys you changed (fit_zh, pitch_en,
evidence_urls), or {} — nothing before or after it, no code fences. evidence_urls must be
the full updated array, at most 6 entries.

# Product facts, proof points and rules (from the research prompt)

{{paste template: what we sell}}

# Field descriptions (from the output schema)

{{paste context/output_schema.json here}}

# The record

{{record}}
