import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
NB = ROOT / "census_ocr_colab_qwen.ipynb"
nb = json.loads(NB.read_text())
nb["cells"][0]["source"] = ["# Census OCR — Colab + Qwen2.5-VL (4-bit)\n", "Runtime GPU required. See colab/README.md\n"]
nb["cells"][2]["source"] = ['!pip install -q "transformers>=4.49" accelerate bitsandbytes qwen-vl-utils openpyxl pandas fuzzywuzzy python-levenshtein matplotlib einops\n']
nb["cells"][3]["source"] = [
"import torch\n",
"VRAM_GB = torch.cuda.get_device_properties(0).total_memory/1e9 if torch.cuda.is_available() else 0\n",
"print(f'VRAM: {VRAM_GB:.1f} GB')\n",
'MODEL_ID="Qwen/Qwen2.5-VL-32B-Instruct"\n',
'FALLBACK="Qwen/Qwen2.5-VL-7B-Instruct"\n',
"LOAD_IN_4BIT=True\nMAX_NEW_TOKENS=4096\nMAX_PIXELS=1280*28*28\n",
"SELECTED_MODEL=MODEL_ID if VRAM_GB>=20 else FALLBACK\n",
"print('Model:', SELECTED_MODEL)\n"]
load={"cell_type":"code","metadata":{},"outputs":[],"execution_count":None,"source":[
"from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig\nimport torch\n",
"bnb=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_compute_dtype=torch.bfloat16,bnb_4bit_use_double_quant=True) if LOAD_IN_4BIT else None\n",
"processor=AutoProcessor.from_pretrained(SELECTED_MODEL,trust_remote_code=True)\n",
"model=Qwen2_5_VLForConditionalGeneration.from_pretrained(SELECTED_MODEL,quantization_config=bnb,device_map='auto',torch_dtype=torch.bfloat16,trust_remote_code=True)\n",
"model.eval(); print('Model ready')\n"]}
if not any('Qwen2_5_VL' in ''.join(c.get('source',[])) for c in nb['cells']):
    nb['cells'].insert(4, load)
for c in nb['cells']:
    s=''.join(c.get('source',[]))
    if 'Upload the census scan' in s:
        c['source']=["from pathlib import Path\nDATA_MODE='drive'\n",
"from google.colab import drive; drive.mount('/content/drive')\n",
"PROJECT_DIR=Path('/content/drive/MyDrive/poc_textual')\n",
"GT_PATH=PROJECT_DIR/'data/ground_truth/Bastrop County 1950 Clean.xlsx'\n",
"IMAGE_DIR=PROJECT_DIR/'data/raw_images/1950_11-1'\nprint(PROJECT_DIR, GT_PATH.exists(), IMAGE_DIR.exists())\n"]
    if 'CENSUS_YEAR = 1950' in s and 'GROUND_TRUTH_SHEET' in s:
        c['source']=["CENSUS_YEAR=1950\nGROUND_TRUTH_SHEET='Bastrop 11-1'\nENUMERATION_DISTRICT='11-1'\nPHYSICAL_PAGE=1\nIMAGE_FILENAME='sheet_01.jpg'\nRUN_BATCH=True\nBATCH_PAGES=list(range(1,12))\n"]
    if 'import anthropic' in s:
        c['source']=["from qwen_vl_utils import process_vision_info\nimport torch,re\n",
"def decade_prompt(y,ed): return PROMPTS_1950.replace('11-2A',ed)\n",
"def parse_json_records(raw):\n raw=re.sub(r'```json\\s*','',raw.strip()); raw=re.sub(r'```\\s*$','',raw,flags=re.M).strip(); s,e=raw.find('['),raw.rfind(']');\n",
" return json.loads(raw[s:e+1] if s>=0 else raw)\n",
"def extract_from_image(image_path,year,ed=ENUMERATION_DISTRICT):\n",
" full=SYSTEM_PROMPT+'\\n\\n'+decade_prompt(year,ed); msgs=[{'role':'user','content':[{'type':'image','image':str(image_path),'max_pixels':MAX_PIXELS},{'type':'text','text':full}]}]\n",
" text=processor.apply_chat_template(msgs,tokenize=False,add_generation_prompt=True); imgs,vids=process_vision_info(msgs)\n",
" inp=processor(text=[text],images=imgs,videos=vids,padding=True,return_tensors='pt').to(model.device)\n",
" with torch.no_grad(): gen=model.generate(**inp,max_new_tokens=MAX_NEW_TOKENS,do_sample=False)\n",
" trim=[o[len(i):] for i,o in zip(inp.input_ids,gen)]; raw=processor.batch_decode(trim,skip_special_tokens=True,clean_up_tokenization_spaces=False)[0]\n",
" recs=parse_json_records(raw); bp=BIRTHPLACE_COLUMN.get(year,'Birthplace'); recs=propagate_dittos(recs,birthplace_field=bp)\n",
" gf=GENDER_COLUMN.get(year,'Gender')\n",
" for r in recs:\n  if 'Race' in r: r['Race']=normalize_race(r['Race'],year)\n  if 'Marital Status' in r: r['Marital Status']=normalize_marital_status(r['Marital Status'],year)\n  if gf in r: r[gf]=normalize_gender(r[gf])\n  elif 'Gender' in r: r['Gender']=normalize_gender(r['Gender'])\n return recs\n",
"image_path=IMAGE_DIR/IMAGE_FILENAME; print('Extracting',image_path); extracted_records=extract_from_image(image_path,CENSUS_YEAR); print(len(extracted_records),'records'); extracted_records[:3]\n"]
    if 'compare(extracted_records' in s:
        c['source']=["gt_path=GT_PATH\nmetrics,results=compare(extracted_records,gt_path,GROUND_TRUTH_SHEET,CENSUS_YEAR,PHYSICAL_PAGE)\nprint(metrics['row_accuracy'], metrics['overall_field_accuracy'])\n"]
nb['cells'].extend([{"cell_type":"markdown","metadata":{},"source":["## Batch ED 11-1\n"]},
{"cell_type":"code","metadata":{},"outputs":[],"execution_count":None,"source":[
"if RUN_BATCH:\n batch=[]; out=PROJECT_DIR/'results/colab_qwen'; out.mkdir(parents=True,exist_ok=True)\n",
" for page in BATCH_PAGES:\n  img=IMAGE_DIR/f'sheet_{page:02d}.jpg'\n  if not img.exists(): continue\n  recs=extract_from_image(img,CENSUS_YEAR); m,res=compare(recs,GT_PATH,GROUND_TRUTH_SHEET,CENSUS_YEAR,page)\n  batch.append({'page':page,'row':m['row_accuracy'],'field':m['overall_field_accuracy']}); print(page,m['row_accuracy'],m['overall_field_accuracy'])\n",
" if batch: json.dump({'model':SELECTED_MODEL,'pages':batch},open(out/'batch_summary.json','w'),indent=2)\n"]}])
nb['metadata']['accelerator']='GPU'
NB.write_text(json.dumps(nb,indent=1))
print('patched', len(nb['cells']), 'cells')
