<!--
Tier-1 prompt-engineering overrides for this instance. Optional — omit this file
entirely, or leave it as-is with no active headers, to use the framework's default
prompt steps.

Format: a level-2 markdown header (two `#` characters, a space, then the section name,
alone on its line) followed by your override text. Everything until the next such
header (or end of file) becomes that section's override text.

Exactly two section names are read — anything else is parsed but ignored:

  query_rewrite_steps      overrides the "how to decide" portion of the query rewriter
                            prompt (chabo's build_query_rewrite_messages()).
  filter_extraction_steps  overrides the "how to decide" portion of the metadata-filter
                            extraction prompt (chabo's build_filter_extraction_messages()).

In both cases only the decision-making/steps portion is overridable — the output JSON
schema/contract is always core-owned and appended separately, so a bad override can
degrade quality but can never break response parsing. Full contract: chabo/README.md's
"Instance Configuration" section.

Note on this template: the parser (chabo's src/components/utils.py,
load_prompt_overrides()) is a plain regex over this file's raw text — it is NOT aware
of markdown code fences. A line starting with `##` followed by one of the two names
above activates that section for real, even inside a code block. That's why this
template doesn't show a live example of the header itself: add it yourself, on its own
line, to actually turn a section on.
-->
