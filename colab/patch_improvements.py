import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
NB = ROOT / "census_ocr_colab_qwen.ipynb"
nb = json.loads(NB.read_text())
FIELDS = ["Street Name","House Number","Dwelling Number","Surname","Given Name",
    "Relation to Head of House","Race","Gender","Age","Marital Status",
    "Birth Place","Occupation","Industry","Worker Class"]
PRIORITY = ["Race","Gender","Surname","Given Name","Age","Relation to Head of House","Birth Place"]

for c in nb["cells"]:
    s = "".join(c.get("source",[]))
    if "def compare(records" in s and "STRATEGY_KEYWORDS" in s:
        c["source"] = [
            f"COMPARE_FIELDS_1950 = {FIELDS!r}\n",
            f"PRIORITY_FIELDS_1950 = {PRIORITY!r}\n",
            "from fuzzywuzzy import fuzz\nimport pandas as pd\n",
            "STRATEGY_KEYWORDS = [(['race'],'exact'),(['gender','sex'],'exact'),(['marital status'],'exact'),\n",
            " (['age'],'numeric'),(['surname','given name'],'fuzzy_name'),(['relation'],'fuzzy'),\n",
            " (['birthplace','birth place'],'fuzzy'),(['occupation','industry'],'fuzzy')]\n",
            "def classify_strategy(field):\n fl=field.lower()\n for kws,st in STRATEGY_KEYWORDS:\n  if any(k in fl for k in kws): return st\n return 'fuzzy'\n",
            "def _norm(v):\n if v is None or (isinstance(v,float) and pd.isna(v)): return ''\n return str(v).strip().lower()\n",
            "def field_match(ext,gt,strategy):\n e,g=_norm(ext),_norm(gt)\n if e==g=='': return True\n if e=='' or g=='': return False\n if strategy=='exact': return e==g\n if strategy in ('fuzzy','fuzzy_name'): return fuzz.ratio(e,g)>=(90 if strategy=='fuzzy_name' else 85)\n if strategy=='numeric':\n  try: return abs(int(float(e))-int(float(g)))<=1\n  except: return e==g\n return e==g\n",
            "def split_into_physical_pages(gt_df,line_col='Line Number'):\n lines=pd.to_numeric(gt_df[line_col],errors='coerce'); starts=[0]\n for i in range(1,len(lines)):\n  if pd.notna(lines.iloc[i]) and lines.iloc[i]==1 and (pd.isna(lines.iloc[i-1]) or lines.iloc[i-1]!=1): starts.append(i)\n starts.append(len(gt_df)); return [gt_df.iloc[s:e].reset_index(drop=True) for s,e in zip(starts,starts[1:])]\n",
            "def compare(records,gt_xlsx_path,gt_sheet,year,physical_page):\n gt_df=pd.read_excel(gt_xlsx_path,sheet_name=gt_sheet); pages=split_into_physical_pages(gt_df)\n page_df=pages[physical_page-1]\n gt_by_line={int(r['Line Number']):r for _,r in page_df.iterrows() if pd.notna(r.get('Line Number'))}\n ext_by_line={int(r['Line Number']):r for r in records if r.get('Line Number') is not None}\n cols=[c for c in COMPARE_FIELDS_1950 if c in page_df.columns]\n print(f'Comparing {len(cols)} fields (not all {len(page_df.columns)-1} GT cols)')\n results,field_scores=[],{c:[] for c in cols}; priority_scores={c:[] for c in cols if c in PRIORITY_FIELDS_1950}\n for ln in sorted(gt_by_line.keys()):\n  gt_row,ext_row=gt_by_line[ln],ext_by_line.get(ln,{}); row={'line_number':ln,'all_match':True,'fields':{}}\n  for f in cols:\n   m=field_match(ext_row.get(f),gt_row.get(f),classify_strategy(f)); row['fields'][f]={'extracted':ext_row.get(f),'ground_truth':gt_row.get(f),'match':m}; field_scores[f].append(m)\n   if f in priority_scores: priority_scores[f].append(m)\n   if not m: row['all_match']=False\n  results.append(row)\n fa={f:sum(s)/len(s) for f,s in field_scores.items() if s}; pa={f:sum(s)/len(s) for f,s in priority_scores.items() if s}\n return {'rows_compared':len(results),'row_accuracy':sum(r['all_match'] for r in results)/len(results) if results else 0,'field_accuracy':fa,'overall_field_accuracy':sum(fa.values())/len(fa) if fa else 0,'priority_field_accuracy':sum(pa.values())/len(pa) if pa else 0,'physical_page':physical_page,'sheet':gt_sheet,'census_year':year,'total_physical_pages_in_sheet':len(pages)}, results\n",
            "print('compare() defined')\n",
        ]
    if "mismatch_df = pd.DataFrame" in s:
        c["source"] = [
            "PRIORITY = PRIORITY_FIELDS_1950\nmismatches=[]\n",
            "for r in results:\n for field,d in r['fields'].items():\n  if field not in PRIORITY or d['match']: continue\n  mismatches.append({'line':r['line_number'],'field':field,'extracted':d['extracted'],'ground_truth':d['ground_truth']})\n",
            "mismatch_df=pd.DataFrame(mismatches); print(f'Priority mismatches: {len(mismatch_df)}'); display(mismatch_df.head(30))\n",
        ]
    if "def decade_prompt" in s:
        c["source"] = [ln if "def decade_prompt" not in ln and "return PROMPTS" not in ln else ("def decade_prompt(y,ed):\n" if "def decade_prompt" in ln else "    return PROMPTS_1950.replace('{ED}', ed)\n") for ln in c["source"]]
NB.write_text(json.dumps(nb,indent=1)); print("ok")
