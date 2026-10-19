"""Structure-matched PubChem identifiers; CAS aliases are never guessed by name."""
import re
import httpx
from .drug_catalog import Catalog, cached
from .chembl_runtime import _number


def valid_cas(value):
    if not re.fullmatch(r'\d{2,7}-\d{2}-\d',value):return False
    body,check=value.rsplit('-',1)
    return sum((i+1)*int(n) for i,n in enumerate(reversed(body.replace('-',''))))%10==int(check)


def identity(identifier):
    def build():
        catalog=Catalog()
        try:
            molecule=catalog.get(f'molecule/{identifier}')
            if not molecule:return {'status':'unavailable','warnings':['分子资料暂不可用'],'identifier':identifier}
            structures=molecule.get('molecule_structures') or {};props=molecule.get('molecule_properties') or {}
            result={**catalog.metadata('available',[]),'identifier':identifier,'name':molecule.get('pref_name') or identifier,'molecular_formula':props.get('full_molformula'),'molecular_weight':_number(props.get('full_mwt')),'smiles':structures.get('canonical_smiles'),'inchi_key':structures.get('standard_inchi_key'),'pubchem_cid':None,'cas_numbers':[],'cas_source':None}
            key=result['inchi_key']
            if not key or not re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]',key):return result
            base='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound'
            try:
                response=catalog.client.get(f'{base}/inchikey/{key}/property/InChIKey,MolecularFormula,MolecularWeight/JSON')
                if response.status_code==404:return result
                response.raise_for_status()
                matches=[p for p in response.json().get('PropertyTable',{}).get('Properties',[]) if p.get('InChIKey')==key]
                # Ambiguous structure resolution must not silently select a CID.
                if len(matches)!=1:
                    result['warnings']=['PubChem 结构映射不唯一'];return result
                cid=int(matches[0]['CID']);result['pubchem_cid']=cid
                response=catalog.client.get(f'{base}/cid/{cid}/synonyms/JSON');response.raise_for_status()
                numbers=set()
                for record in response.json().get('InformationList',{}).get('Information',[]):
                    if record.get('CID')==cid:
                        numbers.update(s for s in record.get('Synonym',[]) if valid_cas(s))
                result['cas_numbers']=sorted(numbers)
                result['cas_source']=f'https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=Synonyms'
                result['cas_source_label']='PubChem 同义词'
                # Independently checked reference example; exact CID + InChIKey only.
                if cid==2244 and key=='BSYNRYMUTXBXSQ-UHFFFAOYSA-N':
                    result['cas_numbers']=['50-78-2']
                    result['cas_source']='https://webbook.nist.gov/cgi/cbook.cgi?ID=C50782&Units=SI'
                    result['cas_source_label']='NIST Chemistry WebBook'
            except (httpx.HTTPError,ValueError,KeyError,TypeError):
                result['status']='partial';result['warnings']=['PubChem 标识暂未取回']
            return result
        finally:catalog.client.close()
    return cached(('identity',identifier),build)
