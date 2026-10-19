import type {Compound} from '@drug-discovery/contracts';
import {matchesFor, finite} from './compoundInsights';

/** Only exact, positive measurements in a shared assay may support a direct comparison. */
export function comparableExperiments(compounds:Compound[], target:string, endpoint:string) {
 const rows=compounds.map(c=>{
  const records=matchesFor(c,target).flatMap(m=>m.experimental_records ?? []);
  return records.flatMap(r=>{
   const value=Number(r.standard_value);
   const factor=r.standard_units==='nM'?1:['uM','µM','μM'].includes(r.standard_units || '')?1000:r.standard_units==='M'?1e9:null;
   return r.standard_type===endpoint && r.standard_relation==='=' && value>0 && finite(value) && factor && r.assay_chembl_id
    ? [{assay:r.assay_chembl_id,value:value*factor}] : [];
  });
 });
 const assays=[...new Set(rows[0]?.map(r=>r.assay) ?? [])].filter(a=>rows.every(rs=>rs.some(r=>r.assay===a)));
 return assays.map(assay=>({assay, values:rows.map(rs=>{
  const values=rs.filter(r=>r.assay===assay).map(r=>r.value).sort((a,b)=>a-b), n=values.length;
  return {median:n%2?values[(n-1)/2]:(values[n/2-1]+values[n/2])/2, count:n};
 })}));
}
export function comparisonEndpoints(compounds:Compound[],target:string) {
 return [...new Set(compounds.flatMap(c=>matchesFor(c,target).flatMap(m=>(m.experimental_records ?? []).map(r=>r.standard_type).filter((x):x is string=>!!x))))].sort();
}
