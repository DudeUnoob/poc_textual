# Census Ground Truth Workbook Reading Guide

Methodology review and worked examples for the research team

Reviewed November 4 2026

### What you can conclude today

This workbook is a useful pilot test of agreement with the manually cleaned census files. It is not yet a validated estimate of handwriting accuracy or reliability in uncleaned districts. Read the percentages as agreement under stated comparison rules, not as proof that every accepted transcription is correct.

The comparison structure is sound for finding differences: the same images and cleaned rows are used for both models, raw text is retained, omissions are counted, and each tested value can be traced to its source. The study is still too small, and some cleaned values represent research coding rather than literal handwriting.

| What was tested | Amount |
| --- | --- |
| Distinct population scans | 4 scans across 1850, 1880, 1910 and 1950 |
| Models | Claude Opus 5.5 and GPT 6 Astra |
| Manual records in the tested scans | 168 records, compared separately for each model |
| Field comparisons | 3,272 model and field comparisons |
| Differences under the comparison rules | 630 model and field comparisons requiring review |

### The easiest way to read the workbook

1. Open Read first, then Scan results. Select one model and one census year.

2. Open Confidence calibration. Select one field and one confidence label.

3. Open Discrepancies to inspect the values that differed.

4. Use All cells and the original scan and workbook to decide what each difference means.

This guide describes granular-audit-reviewed.xlsx. It has clearer column labels than the earlier granular-audit.xlsx, plus a Read first tab. Main agreement percentages are unchanged.

## How the counts work

Each row in Confidence calibration describes a group of field values: one model, one year, one field, and one model confidence label. It is not one person’s census row.

### A real example from this workbook

Filter to Claude Opus 5.5, year 1850, field Given Name, and confidence high.

| Values checked | Matched cleaning | Differed from cleaning | Agreement rate |
| --- | --- | --- | --- |
| 15 | 15 | 0 | 100% |

This means the model labeled 15 given names high confidence, and all 15 agreed with the manual cleaning under the name comparison rule. The first 15 is the size of the group; the second 15 is the matched part of that group. These are not two separate groups of 15.

### A different real group

In Decade fields, GPT 6 Astra’s 1850 Given Name row contains 42 values checked, 27 matched, and 15 differing. Its agreement rate is 27 divided by 42, or 64.3%. This group includes all confidence labels.

### If both counts are 15

If a single group actually had 15 matched and 15 differing, it would contain 30 checked values and have 50% agreement. The reviewed workbook does not contain that exact pair on a single row. The 15 checked and 15 matched example above has zero differences.

The arithmetic is always: values checked equals matched cleaning plus differed from cleaning. Agreement rate equals matched cleaning divided by values checked.

### Do not add the tabs together

Confidence calibration, Decade fields, Threshold policies, Discrepancies and All cells are different views of the same underlying tests. Adding their totals would count the same evidence repeatedly. The high only and high plus medium policies also overlap; they are alternative rules, not independent experiments.

The 340 rows in Row review are model and line combinations, including unmatched lines. They are not 340 different people. Likewise, the 630 discrepancies count a field difference separately for each model; they are not necessarily 630 distinct mistakes in the handwritten records.

## What each workbook tab is for

| Tab | One row represents | Use it to |
| --- | --- | --- |
| Read first | One reading instruction | Understand scope, labels and limits before comparing scores. |
| Scan results | One scan tested by one model | Check overall field agreement, complete rows, and missing or extra lines. |
| Confidence calibration | One model, year, field and label | See how often high, medium or low confidence agreed with cleaning. |
| Threshold policies | One possible acceptance policy | Compare precision and coverage if only selected confidence labels were accepted. |
| Decade fields | One model, year and field | Find weak fields without mixing different decades. |
| Row review | One printed line for one model | See which fields differ on a particular person or vacant line. |
| Discrepancies | One differing field for one model | Review the disagreement and the model’s confidence. |
| All cells | One tested field for one model | Trace both matches and differences to raw text and the exact manual cell. |
| Numeric reliability | One probability band and field | Inspect numeric self-reports from the new 1950 test only. |
| Decade inventory | One census year | Find which decades have candidate scans and which still need inputs. |
| Variable coverage | One year and workbook variable | See what was tested and what remains untested. |
| Run notes and Failures | A limitation or unresolved failure | Understand exclusions, missing evidence and failed runs. |

### Useful Excel actions

Select a header row and use Data then Filter if filter arrows are not already shown. Filter the model and year before reading a percentage. Filter the field next, then the confidence label or policy. Keep the checked count visible beside the rate.

A long model ID beginning with @ identifies the Portkey route. The final part, claude-opus-5-5 or gpt-6-astra, is the model name. Do not compare two rows unless their field, year and tested scan scope are comparable.

## How to inspect a single difference

### Start with one specific cell

A real example is Opus on the first 1850 scan, printed line 1, Birth Place. The model transcribed N.Y. with high confidence. The manual file contains New York at cell M2 on the Not Stated Census tab. The current generic text comparison marks this as a discrepancy.

That example is not evidence that the model misread the handwriting. It is evidence that the comparison does not yet reconcile every abbreviation with the cleaned vocabulary. A person should check the image before deciding how to classify it.

### Follow this review sequence

1. Identify the model, year, image, printed line and field in Discrepancies.

2. Find the same entry in All cells using entry_id. This ID identifies the source cell across models; each model’s prediction has its own model_entry_id in the data export.

3. Compare raw, extracted and Manual cleaned value. Raw is what the model read; extracted includes the pipeline’s explicit normalization.

4. Open the source image and then the original cleaned workbook. Use sheet and ground_truth_excel_cell to find the reference value. An empty reference address on an extra row means there is no aligned manual row.

5. Record an adjudication: transcription error, equivalent wording, research coding difference, alignment problem, manual-data issue, or unresolved. Preserve the original values and the reason for the decision.

### What counts as a match today

| Field or situation | Current comparison rule |
| --- | --- |
| Race and sex | Exact value ignoring case after the existing explicit code mappings. |
| Age | Within one year when numeric; fractional infant ages are not fully standardized. |
| Names and other text | String similarity of at least 85. This is a threshold, not an 85% probability of correctness. |
| Blank cells | Two blanks can match on an aligned row. Nonblank agreement is also reported. |
| Missing or extra rows | Every tested field on that unmatched line is treated as a difference. |

Exact flags compare strings after trimming spaces and ignoring case. They do not expand abbreviations or treat every numeric format as identical. “Raw exact match” is still agreement with the cleaned cell, not independently verified literal ground truth.

## How to read confidence and automation estimates

### Model confidence is a claim

High, medium and low describe the model’s own judgment. They do not correspond to fixed probabilities. The earlier 1850, 1880 and 1910 extractions collected labels only. The new 1950 extraction additionally requested numbers between 0 and 1. A 0.99 self-report means the model claimed 99% confidence; it is not evidence that 99% of such values are correct.

Across the four scans, Opus had 1,055 matches among 1,190 aligned fields labeled high confidence, or 88.7% agreement. GPT 6 Astra had 1,044 among 1,223, or 85.4%. These pooled figures are descriptive only: decades, fields and prompt versions differ. Use the separate year and field groups when making decisions.

### Read an acceptance policy as a what if calculation

In Threshold policies, high only means accepting nonblank predictions labeled high. High plus medium accepts both labels. Precision on accepted is the fraction of those accepted predictions that matched cleaning. Acceptance coverage is the fraction of eligible nonblank predictions the policy would accept; it is not the fraction of the whole census safely automated.

For example, 90% precision would mean 10% of accepted values differed from cleaning in that tested group. The reference errors per 1000 would then be 100. That is arithmetic under an assumption that the next data behaves the same way, not a validated forecast for another district.

### Blank intervals mean unavailable evidence

There is one scan per decade. Many fields on one scan share the same writer, image quality and context. They cannot be treated as hundreds of independent page samples. The report therefore leaves uncertainty intervals blank in these strata. Five scans is only a reporting floor in the code; it is not enough by itself to establish reliability.

### The numeric diagnostics have a limited target

Numeric reliability groups reported probabilities into bands and compares them with observed cleaning agreement. Its Brier score is a squared difference between the numeric self-report and the match result; lower values indicate closer agreement with that result. Because the model was asked about literal transcription while the reference may be normalized or recoded, this is only a diagnostic until those targets are reconciled. No fitted calibration model has been validated.

Missing rows have no confidence to threshold. They remain a separate risk even if the confidence of returned values appears reliable. Every automation_validated flag is false in this pilot.

## Methodology review and the next defensible study

### What the current process does correctly

The models see the same selected images and field schema without receiving the manual answers. The same verified reference rows are used for both models. Raw outputs, confidence, input hashes and manual cell addresses are retained. Missing and extra rows are penalized, and matches can be separated from nonblank matches. The count identities and traceability checks pass.

### What is not established

Four first-page scans are a convenience pilot, not a representative sample of handwriting or districts. The test covers selected variables only. Two models and many cells do not increase the number of distinct source scans. Opus leading on field agreement does not mean it wins every metric: on the 1880 scan, GPT has higher complete row agreement even though Opus has higher field agreement.

The manual cleaning is an imperfect and sometimes different target from the image. Blank race cells may become White; abbreviated names and places may be expanded; researcher ethnicity categories may not appear on the form. Differences have not all been human-adjudicated. Fuzzy acceptance can approve distinct names, and age tolerance can accept a transcription that differs by a year. These scores cannot yet be called pure OCR correctness.

### What should happen before trusting new districts

1. Agree with the research team on the evaluation target and codebook. Separate literal transcription from deterministic normalization, derived fields and research coding.

2. Randomly sample multiple scans within each decade, including different enumerators, districts, form variants and image qualities. Verify every scan-to-reference mapping; do not assume viewer numbers are Excel page numbers.

3. Independently review and adjudicate discrepancies against the source scan. Also review a random sample of accepted matches, since fuzzy matching can hide errors. Keep both original and adjudicated scores.

4. Use separate data for calibration, policy selection and final evaluation. Hold out whole districts or enumerators where possible. Freeze the prompt, model, normalization and scoring rules during the final test.

5. Report agreement, omissions, extra rows, accepted-value precision, coverage and uncertainty separately by decade and priority field. Select automation targets with the research team, and retain random human quality checks after deployment.

### Review correction and references

The reviewed workbook fixes exact-match flags that could mark two empty placeholders on an unmatched row as equal. Main agreement rates and the 630 discrepancies are unchanged. It also replaces “correct” with “Matched cleaning” and adds Read first. At the time of this guide review, 65 code tests passed; passing code tests does not validate the research study design.

Project evidence: combined_benchmark.json, audit.json, the four source scans and cleaned workbooks, and the benchmark and audit scripts in the supplied data package. Statistical background: Guo and colleagues, On Calibration of Modern Neural Networks, ICML 2017, https://proceedings.mlr.press/v70/guo17a.html. That paper explains calibration; it does not validate these census results.
