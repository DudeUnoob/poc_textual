"""
Main extraction pipeline: census image → structured JSON records via vision LLM.
"""
import anthropic
import json
import re
import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import OUTPUTS_DIR, PROMPTS_DIR
from preprocess import image_to_base64
from utils import (propagate_dittos, normalize_race, normalize_gender,
                    normalize_marital_status, BIRTHPLACE_COLUMN, GENDER_COLUMN)

load_dotenv()


def load_prompt(year: int) -> tuple[str, str]:
    """Load system + user prompt for a given census decade."""
    prompts_dir = PROMPTS_DIR
    system = (prompts_dir / "system.txt").read_text()
    decade_file = prompts_dir / f"{year}.txt"
    user = decade_file.read_text() if decade_file.exists() else \
           (prompts_dir / "generic.txt").read_text()
    return system, user


def extract_from_image(image_path: str, year: int,
                       client: anthropic.Anthropic = None) -> list[dict]:
    """
    Send census image to Claude vision API. Returns list of person records.
    """
    if client is None:
        client = anthropic.Anthropic()
    
    b64_data, media_type = image_to_base64(image_path)
    system_prompt, user_prompt = load_prompt(year)
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64_data}
                },
                {"type": "text", "text": user_prompt}
            ]
        }]
    )
    
    raw = response.content[0].text
    # Strip any markdown fences the model might add
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()
    
    records = json.loads(raw)
    
    # Post-process. Birthplace column name genuinely varies by decade
    # ("Birth Place" for 1850/1860/1950, "Birthplace" for everything else --
    # verified, see CLAUDE.md) so it must be looked up per-decade here, not
    # hardcoded, or ditto propagation silently no-ops on the wrong field.
    birthplace_field = BIRTHPLACE_COLUMN.get(year, "Birthplace")
    records = propagate_dittos(records, surname_field="Surname",
                                birthplace_field=birthplace_field)
    gender_field = GENDER_COLUMN.get(year, "Gender")
    for rec in records:
        if "Race" in rec:
            rec["Race"] = normalize_race(rec["Race"], year)
        if "Marital Status" in rec:
            rec["Marital Status"] = normalize_marital_status(rec["Marital Status"], year)
        if gender_field in rec:
            rec[gender_field] = normalize_gender(rec[gender_field])
        elif "Gender" in rec:
            rec["Gender"] = normalize_gender(rec["Gender"])
    
    return records


def process_sheet(image_path: str, year: int, output_path: str) -> list[dict]:
    """Extract one sheet and save results."""
    print(f"Extracting: {image_path} (year={year})")
    client = anthropic.Anthropic()
    records = extract_from_image(image_path, year, client)
    
    output = {
        "source_image": str(image_path),
        "census_year": year,
        "record_count": len(records),
        "records": records
    }
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"  → {len(records)} records saved to {output_path}")
    return records


def process_batch(image_dir: str, year: int, output_dir: str):
    """Process all images in a directory."""
    from tqdm import tqdm
    images = sorted(Path(image_dir).glob("*.jpg")) + \
             sorted(Path(image_dir).glob("*.jpeg"))
    
    client = anthropic.Anthropic()
    all_records = []
    
    for img_path in tqdm(images, desc=f"Processing {year} census"):
        out_path = Path(output_dir) / f"{img_path.stem}_extracted.json"
        if out_path.exists():
            print(f"  Skipping {img_path.name} (already done)")
            continue
        try:
            records = extract_from_image(str(img_path), year, client)
            process_sheet(str(img_path), year, str(out_path))
            all_records.extend(records)
        except Exception as e:
            print(f"  ERROR on {img_path.name}: {e}")
    
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--output", default=str(OUTPUTS_DIR / "extracted.json"))
    args = parser.parse_args()
    process_sheet(args.image, args.year, args.output)
