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
  const keys=Object.keys(rows[0]);
  const names={observations:'Values checked',correct:'Matched cleaning',discrepancies:'Differed from cleaning',observed_accuracy:'Agreement rate',cell_accuracy:'Field agreement rate',row_accuracy:'Complete row agreement',confidence_label:'Model confidence label',nonblank_accuracy:'Agreement on nonblank truth',scans:'Distinct scans',accuracy_ci_low:'Lower interval bound',accuracy_ci_high:'Upper interval bound',match:'Matched under comparison rule',ground_truth:'Manual cleaned value',model_confidence:'Model confidence label'};
  const safe=v=>typeof v==='string'&&v.startsWith('=')?"'"+v:v??null;
  sheet.getRangeByIndexes(0,0,1,keys.length).values=[keys.map(k=>names[k]||k)];
  sheet.getRangeByIndexes(1,0,rows.length,keys.length).values=rows.map(r=>keys.map(k=>safe(r[k])));
  const used=sheet.getRangeByIndexes(0,0,rows.length+1,keys.length);
  used.format.font.name='Aptos';used.format.font.size=11;
  used.format.columnWidth=20;used.format.rowHeight=22;
  const head=sheet.getRangeByIndexes(0,0,1,keys.length);
  head.format.fill='#16324F';head.format.font.color='#FFFFFF';head.format.font.bold=true;
  head.format.rowHeight=56;head.format.wrapText=true;
  sheet.freezePanes.freezeRows(1);
  keys.forEach((k,i)=>{
    const range=sheet.getRangeByIndexes(1,i,rows.length,1);
    if(/accuracy|precision_on_accepted|accepted_error_rate|acceptance_coverage|confidence_probability|mean_self_reported_probability/.test(k))range.setNumberFormat('0.0%');
    if(k==='model')sheet.getRangeByIndexes(0,i,rows.length+1,1).format.columnWidth=52;
    if(/image|artifact/.test(k))sheet.getRangeByIndexes(0,i,rows.length+1,1).format.columnWidth=45;
  });
  return sheet;
}
if(report.scan_results){
const intro=table('Read first',[
  {Topic:'Purpose',Explanation:'Pilot agreement with manual cleaned records. This is not a production accuracy guarantee.'},
  {Topic:'Scope',Explanation:'Four scans, one each from 1850, 1880, 1910 and 1950; two models. The same scans appear twice in model results.'},
  {Topic:'Read in this order',Explanation:'Scan results, Confidence calibration, Discrepancies, then All cells to inspect a particular value.'},
  {Topic:'The count rule',Explanation:'Values checked = Matched cleaning + Differed from cleaning. These counts describe different field values in the same group.'},
  {Topic:'Confidence versus agreement',Explanation:'A high model confidence label is a model claim. Agreement is measured by comparing to the cleaned workbook.'},
  {Topic:'What matched means',Explanation:'Race/sex exact ignoring case after code mapping; age within one year; names and other text similarity at least 85. Blank agreement is included.'},
  {Topic:'What a discrepancy means',Explanation:'A difference requiring review. It may be a transcription error, normalization/research coding difference, omission or ground-truth issue.'},
  {Topic:'Repeated rows across tabs',Explanation:'Tabs are different views of the same tests. Do not add their counts together. Threshold policies are nested alternatives, not separate tests.'},
  {Topic:'Intervals left blank',Explanation:'There are too few independent scans in each decade/field group. A blank interval is not zero uncertainty.'},
  {Topic:'Automation',Explanation:'No policy is validated. More scans and held-out districts, codebook review and human adjudication are needed.'},
  {Topic:'Reporting correction',Explanation:'Exact-match flags now require an aligned row. Empty fields on extra or missing rows cannot count as exact matches. Main agreement scores are unchanged.'},
  {Topic:'Detailed guide',Explanation:'See Census Ground Truth Workbook Reading Guide and Methodology Review supplied with this workbook.'}
]);intro.getRange('A1:A13').format.columnWidth=28;intro.getRange('B1:B13').format.columnWidth=105;intro.getRange('A1:B13').format.wrapText=true;intro.getRange('A2:B13').format.rowHeight=44;
table('Scan results',report.scan_results.map(({model,year,gt_page,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,image,sheet})=>({model,year,gt_page,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,image,sheet})));
table('Confidence calibration',report.confidence_calibration.map(({model,year,field,confidence_label,observations,correct,discrepancies,observed_accuracy,scans,nonblank_accuracy,accuracy_ci_low,accuracy_ci_high,interval_method})=>({model,year,field,confidence_label,observations,correct,discrepancies,observed_accuracy,scans,nonblank_accuracy,accuracy_ci_low,accuracy_ci_high,interval_method})));
table('Threshold policies',report.threshold_policies);
table('Decade fields',report.decade_fields);
table('Row review',report.rows);
table('Discrepancies',report.discrepancies.map(({year,model,image,line,field,raw,extracted,ground_truth,model_confidence,confidence_probability,discrepancy_type,ground_truth_excel_cell,sheet,entry_id,review_disposition})=>({year,model,image,line,field,raw,extracted,ground_truth,model_confidence,confidence_probability,discrepancy_type,ground_truth_excel_cell,sheet,entry_id,review_disposition})));
table('All cells',report.cells.map(({year,model,image,line,field,raw,extracted,ground_truth,match,raw_exact_match,normalized_exact_match,model_confidence,confidence_probability,ground_truth_excel_cell,sheet,entry_id})=>({year,model,image,line,field,raw,extracted,ground_truth,match,raw_exact_match,normalized_exact_match,model_confidence,confidence_probability,ground_truth_excel_cell,sheet,entry_id})));
table('Numeric reliability',report.reliability_bins);
table('Decade inventory',report.decade_inventory);
table('Variable coverage',report.variable_coverage);
table('Run notes',report.notes.map(Note=>({Note}))).getRange('A1:A20').format.columnWidth=110;
table('Failures',report.failures);
}else{
table('Page results',report.summary.map(({model,year,gt_page,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,elapsed_seconds,input_tokens,output_tokens,image,sheet,gt_row_start,gt_row_count})=>({model,year,gt_page,cell_accuracy,row_accuracy,missing_rows,extra_rows,truth_rows,extracted_rows,elapsed_seconds,input_tokens,output_tokens,image,sheet,gt_row_start,gt_row_count})));
table('Field accuracy',report.fields);
table('Confidence audit',report.confidence);
table('Cell review',report.cells.map(({year,model,line,field,raw,extracted,ground_truth,match,model_confidence,review,row_status,image})=>({year,model,line,field,raw,extracted,ground_truth,match,model_confidence,review,row_status,image})));
table('Run notes',report.notes.map(note=>({Note:note}))).getRange('A1:A8').format.columnWidth=110;
table('Failures',report.failures);
}
const summarySheet=report.scan_results?'Scan results':'Page results';
console.log((await wb.inspect({kind:'region',sheetId:summarySheet,range:'A1:I9',maxChars:1800,tableMaxRows:9,tableMaxCols:9})).ndjson);
const preview=await wb.render({sheetName:summarySheet,range:'A1:I9',scale:1,format:'png'});
await fs.writeFile(path.join(path.dirname(output),'benchmark-preview.png'),new Uint8Array(await preview.arrayBuffer()));
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log(output);
