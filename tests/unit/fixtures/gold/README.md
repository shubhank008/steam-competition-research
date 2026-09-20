# Stage 1 gold set

`stage1_gold_v1.json` is a small, synthetic, manually labeled contract corpus. Every sentence was authored for this repository; it is not copied from Steam and contains no profile identifiers, URLs, or personal data. It is committed so offline evaluation is reproducible.

## Annotation guidance

- Annotate only what the text supports. Do not infer reviewer geography from language.
- `eligible` follows the production eligibility rules. Punctuation-only and other noise examples are retained as negative controls but are not Stage 1 inputs.
- `actionable` means the text contains a concrete product problem, request, or decision-relevant praise; a generic reaction such as “nice” is not actionable.
- `overall_sentiment` is the text-level posture: positive, negative, mixed, or neutral. `engagement_posture` describes the reviewer’s relationship to the product, not Steam’s `voted_up` field.
- `aspects` contains every material taxonomy category supported by an explicit phrase. Multi-aspect examples must retain all supported categories; unsupported categories are not guessed.
- Low playtime and negative sentiment are evidence of early friction, not verified churn. Abandonment language is unverified text evidence.

## Evaluation limitations

This is a regression and contract corpus, not a statistically representative sample. Eight records cannot establish production quality, cross-cultural equivalence, or model release readiness. Category labels are single-annotator gold labels and should be expanded with independent annotators and adjudication before tuning a production model. The evaluator reports exact-match metrics and does not measure translation quality or calibration.
