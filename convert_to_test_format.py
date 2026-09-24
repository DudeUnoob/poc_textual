"""
Converts a ground-truth Excel file + an AI-output Excel file (with a
confidence column per field) into the (incorrect, high_confidence) pairs
that confidence_reliability_test.py expects.

Assumes both files have one row per person (matched by "Line"), and that
for each data field (e.g. "Age") there is a matching "<Field>_conf" column
in the AI output giving that field's confidence level.
"""

import pandas as pd

def convert(ground_truth_path, ai_output_path, fields, key_column="Line"):
    gt = pd.read_excel(ground_truth_path).set_index(key_column)
    ai = pd.read_excel(ai_output_path).set_index(key_column)

    cells = []
    details = []  # keep human-readable info alongside, for a sanity-check table

    for line in gt.index:
        for field in fields:
            gt_value = gt.loc[line, field]
            ai_value = ai.loc[line, field]
            ai_conf = ai.loc[line, f"{field}_conf"]

            # True = the AI's value does NOT match the human-cleaned ground truth
            incorrect = (str(gt_value).strip().lower() != str(ai_value).strip().lower())
            # True = the AI reported "high" confidence for this specific field
            high_confidence = (str(ai_conf).strip().lower() == "high")

            cells.append((incorrect, high_confidence))
            details.append({
                "Line": line, "Field": field,
                "Ground_Truth": gt_value, "AI_Value": ai_value, "AI_Confidence": ai_conf,
                "Incorrect": incorrect, "High_Confidence": high_confidence,
                "BAD_SCENARIO": incorrect and high_confidence,
            })

    return cells, pd.DataFrame(details)


if __name__ == "__main__":
    fields = ["Name", "Age", "Race"]
    cells, detail_table = convert(
        "ground_truth_1900_bastrop.xlsx",
        "ai_output_1900_bastrop.xlsx",
        fields,
    )

    print("=== Converted per-cell table (this is the real intermediate data) ===")
    print(detail_table.to_string(index=False))

    print(f"\n=== Final (incorrect, high_confidence) pairs fed into the test ===")
    print(cells)

    detail_table.to_excel("converted_cells_1900_bastrop.xlsx", index=False)
    print("\nSaved readable version to converted_cells_1900_bastrop.xlsx")
