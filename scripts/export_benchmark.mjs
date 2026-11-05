// Export the benchmark's auditable JSON into an Excel review workbook.
import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';
const reportPath=process.argv[2];
if(!reportPath) throw Error('Usage: node scripts/export_benchmark.mjs REPORT_JSON [OUTPUT_XLSX]');
const report=JSON.parse(await fs.readFile(reportPath,'utf8'));
const output=process.argv[3] || path.join(path.dirname(reportPath),'benchmark.xlsx');
const wb=Workbook.create();
function table(name, rows) {
  const sheet=wb.worksheets.add(name);sheet.showGridLines=false;
  if(!rows.length) rows=[{Status:'No observations'}];
  const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
  const names={year:'Census year',model:'Model',observations:'Values checked',correct:'Matched cleaning',discrepancies:'Differed from cleaning',observed_accuracy:'Agreement rate',cell_accuracy:'Field agreement rate',row_accuracy:'Complete row agreement',confidence_label:'Model confidence label',nonblank_accuracy:'Agreement on nonblank truth',scans:'Distinct scans',accuracy_ci_low:'Lower interval bound',accuracy_ci_high:'Upper interval bound',match:'Matched under comparison rule',ground_truth:'Manual cleaned value',model_confidence:'Model confidence label',confidence_probability:'Self-reported probability',strict_row_agreement:'Complete row agreement'};
  const safe=v=>typeof v==='string'&&v.startsWith('=')?"'"+v:typeof v==='object'&&v!==null?JSON.stringify(v):v??null;
  sheet.getRangeByIndexes(0,0,1,keys.length).values=[keys.map(k=>names[k]||k)];
  sheet.getRangeByIndexes(1,0,rows.length,keys.length).values=rows.map(r=>keys.map(k=>safe(k==='model'&&r[k]?.startsWith('@')?r[k].split('/').slice(1).join('/'):r[k])));
  const used=sheet.getRangeByIndexes(0,0,rows.length+1,keys.length);
  used.format.font.name='Aptos';used.format.font.size=11;
  used.format.columnWidth=20;used.format.rowHeight=22;
  const head=sheet.getRangeByIndexes(0,0,1,keys.length);
  head.format.fill='#16324F';head.format.font.color='#FFFFFF';head.format.font.bold=true;
  head.format.rowHeight=56;head.format.wrapText=true;head.format.horizontalAlignment='center';head.format.verticalAlignment='center';
  sheet.freezePanes.freezeRows(1);
  sheet.tables.add(`A1:${columnName(keys.length)}${rows.length+1}`,true,'T'+name.replace(/[^a-zA-Z0-9]/g,''));
  keys.forEach((k,i)=>{
    const range=sheet.getRangeByIndexes(1,i,rows.length,1);
    if(/accuracy|agreement|precision_on_accepted|accepted_error_rate|acceptance_coverage|confidence_probability|mean_self_reported_probability/.test(k)&&typeof rows.find(r=>r[k]!=null)?.[k]==='number')range.setNumberFormat('0.0%');
    if(k==='model')sheet.getRangeByIndexes(0,i,rows.length+1,1).format.columnWidth=25;
    if(k==='year')sheet.getRangeByIndexes(0,i,rows.length+1,1).format.columnWidth=12;
    if(/image|artifact/.test(k))sheet.getRangeByIndexes(0,i,rows.length+1,1).format.columnWidth=45;
    if(k==='match')range.conditionalFormats.add('cellIs',{operator:'equal',formula:'FALSE',format:{fill:'#FDE9D9',font:{color:'#9C0006'}}});
  });
  return sheet;
}
function columnName(n){let name='';while(n){n--;name=String.fromCharCode(65+n%26)+name;n=Math.floor(n/26)}return name}
if(report.scan_results){
const scans=new Set(report.cells.map(c=>c.scan_id)).size;
const years=[...new Set(report.scan_results.map(r=>r.year))].sort();
const models=new Set(report.plan.models).size;
if(report.overview?.length){
table('Overview',report.overview.map(({year,model,scans,observations,correct,discrepancies,observed_accuracy,nonblank_accuracy,strict_row_agreement,missing_rows,extra_rows,aligned_high_confidence_values,aligned_high_confidence_errors,aligned_high_confidence_agreement,elapsed_seconds,input_tokens,output_tokens})=>({year,model,scans,observations,correct,discrepancies,observed_accuracy,nonblank_accuracy,strict_row_agreement,missing_rows,extra_rows,aligned_high_confidence_values,aligned_high_confidence_errors,aligned_high_confidence_agreement,elapsed_seconds,input_tokens,output_tokens})));
}
const intro=table('Read first',[
  {Topic:'Purpose',Explanation:'Pilot agreement with manual cleaned records. This is not a production accuracy guarantee.'},
  {Topic:'Scope',Explanation:`${scans} distinct tested scans; census years ${years.join(', ')||'none'}; ${models} configured models. ${report.scan_results.length} completed scan/model results. The same scans appear once per model.`},
  {Topic:'Run date (UTC)',Explanation:report.created_utc},
  {Topic:'Sampling',Explanation:report.plan.sampling_method||'See the verified test plan.'},
  {Topic:'Read in this order',Explanation:'Overview, Scan results, Confidence calibration, Discrepancies, then All cells for a particular value.'},
  {Topic:'The count rule',Explanation:'Values checked = Matched cleaning + Differed from cleaning. These counts describe different field values in the same group.'},
  {Topic:'Confidence versus agreement',Explanation:'A high model confidence label is a model claim. Agreement is measured by comparing to the cleaned workbook.'},
  {Topic:'What matched means',Explanation:'Race/sex exact ignoring case after code mapping; age within one year; names and other text similarity at least 85. Blank agreement is included.'},
  {Topic:'What a discrepancy means',Explanation:'A difference requiring review. It may be a transcription error, normalization/research coding difference, omission or ground-truth issue.'},
  {Topic:'Repeated rows across tabs',Explanation:'Tabs are different views of the same tests. Do not add their counts together. Threshold policies are nested alternatives, not separate tests.'},
  {Topic:'Intervals left blank',Explanation:'There are too few independent scans in each decade/field group. A blank interval is not zero uncertainty.'},
  {Topic:'Automation',Explanation:'No policy is validated. More scans and held-out districts, codebook review and human adjudication are needed.'},
  {Topic:'Reporting correction',Explanation:'Exact-match flags now require an aligned row. Empty fields on extra or missing rows cannot count as exact matches. Main agreement scores are unchanged.'},
  {Topic:'Review status',Explanation:'Page alignment was checked. Discrepancies are unadjudicated unless their review disposition explicitly records a completed review.'},
  {Topic:'Unscored inputs',Explanation:`${report.pending_alignments?.length||0} selected scans still need alignment. Input inventory includes scans outside the selected sample. Run coverage distinguishes failed and unattempted model reads.`},
  {Topic:'Detailed guide',Explanation:'See docs/WORKBOOK_GUIDE.md for definitions. Its four-scan numerical examples describe the earlier pilot, not this run.'}
]);intro.getRange('A1:A18').format.columnWidth=28;intro.getRange('B1:B18').format.columnWidth=105;intro.getRange('A1:B18').format.wrapText=true;intro.getRange('A2:B18').format.rowHeight=44;
table('Scan results',report.scan_results.map(({model,year,gt_page,gt_row_start,gt_row_count,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,image,sheet})=>({model,year,gt_page,gt_row_start,gt_row_count,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,image,sheet})));
table('Confidence calibration',report.confidence_calibration.map(({model,year,field,confidence_label,observations,correct,discrepancies,observed_accuracy,scans,nonblank_accuracy,accuracy_ci_low,accuracy_ci_high,interval_method})=>({model,year,field,confidence_label,observations,correct,discrepancies,observed_accuracy,scans,nonblank_accuracy,accuracy_ci_low,accuracy_ci_high,interval_method})));
table('Threshold policies',report.threshold_policies);
table('Decade fields',report.decade_fields);
table('Row review',report.rows);
table('Discrepancies',report.discrepancies.map(({year,model,image,line,field,raw,extracted,ground_truth,model_confidence,confidence_probability,discrepancy_type,ground_truth_excel_cell,sheet,entry_id,review_disposition,reference_note})=>({year,model,line,field,raw,extracted,ground_truth,model_confidence,confidence_probability,discrepancy_type,ground_truth_excel_cell,sheet,entry_id,review_disposition,reference_note,image})));
table('All cells',report.cells.map(({year,model,image,line,field,raw,extracted,ground_truth,match,raw_exact_match,normalized_exact_match,model_confidence,confidence_probability,ground_truth_excel_cell,sheet,entry_id,row_status})=>({year,model,line,field,raw,extracted,ground_truth,match,raw_exact_match,normalized_exact_match,model_confidence,confidence_probability,row_status,ground_truth_excel_cell,sheet,entry_id,image})));
table('Numeric scores',report.numeric_confidence||[]);
table('Numeric reliability',report.reliability_bins);
table('Decade inventory',report.decade_inventory);
table('Variable coverage',report.variable_coverage);
table('Run notes',report.notes.map(Note=>({Note}))).getRange('A1:A20').format.columnWidth=110;
table('Failures',report.failures);
if(report.scan_provenance)table('Scan provenance',report.scan_provenance);
if(report.run_coverage)table('Run coverage',report.run_coverage);
if(report.intake_inventory)table('Input inventory',report.intake_inventory);
if(report.pending_alignments)table('Pending alignments',report.pending_alignments);
if(report.provider_configs)table('Run settings',report.provider_configs.map(r=>({...r,configured_model_id:r.model})));
if(report.attempt_events)table('Attempts',report.attempt_events);
}else{
table('Page results',report.summary.map(({model,year,gt_page,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,elapsed_seconds,input_tokens,output_tokens,image,sheet,gt_row_start,gt_row_count})=>({model,year,gt_page,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,elapsed_seconds,input_tokens,output_tokens,image,sheet,gt_row_start,gt_row_count})));
table('Field accuracy',report.fields);
table('Confidence audit',report.confidence);
table('Cell review',report.cells.map(({year,model,line,field,raw,extracted,ground_truth,match,model_confidence,review,row_status,image})=>({year,model,line,field,raw,extracted,ground_truth,match,model_confidence,review,row_status,image})));
table('Run notes',report.notes.map(note=>({Note:note}))).getRange('A1:A8').format.columnWidth=110;
table('Failures',report.failures);
}
wb.recalculate();
const summarySheet=report.overview?.length?'Overview':report.scan_results?'Scan results':'Page results';
console.log((await wb.inspect({kind:'region',sheetId:summarySheet,range:'A1:I9',maxChars:1800,tableMaxRows:9,tableMaxCols:9})).ndjson);
const preview=await wb.render({sheetName:summarySheet,range:'A1:I9',scale:1,format:'png'});
await fs.writeFile(path.join(path.dirname(output),'benchmark-preview.png'),new Uint8Array(await preview.arrayBuffer()));
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log(output);
