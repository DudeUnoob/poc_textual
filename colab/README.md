# Colab + Qwen2.5-VL Setup

## Quick start
1. `python colab/make_bundle.py` → creates `poc_textual_colab.zip`
2. Open `census_ocr_colab_qwen.ipynb` in Colab
3. Runtime → GPU (A100 for 32B, T4 uses 7B fallback)
4. Run all cells; set `PROJECT_DIR` to your Drive path OR upload the zip

## Config (edit in notebook)
- `GROUND_TRUTH_SHEET = "Bastrop 11-1"`
- `RUN_BATCH = True` for all 11 sheets
- `sheet_01.jpg` → page 1, … `sheet_11.jpg` → page 11

## GPU
| Model | VRAM | Colab |
|-------|------|-------|
| Qwen2.5-VL-32B 4-bit | ~22GB | A100 |
| Qwen2.5-VL-7B 4-bit | ~8GB | T4 |

---

## IDE / Colab Extension (no web UI)

Use **`census_ocr_ide_colab.ipynb`** from Cursor/VS Code Colab extension.

**Important:** the extension syncs **only the notebook** to Colab cloud — not your local `data/` folder.

### Setup
1. Open `census_ocr_ide_colab.ipynb` → connect to Colab GPU runtime
2. In the **Bootstrap** cell, set `HF_TOKEN = "hf_..."`
3. Get project files onto the VM (pick one):
   - **`GIT_REPO_URL`** — clone your repo to `/content/poc_textual`
   - **`REMOTE_ZIP_URL`** — wget a hosted `poc_textual_colab.zip`
   - Copy **`poc_textual_colab.zip`** to `/content/` on the Colab VM (extension file upload or Drive), then re-run bootstrap

4. Run all cells. Results save to `results/colab_ide/`.

Rebuild zip after local changes: `python colab/make_bundle.py`
