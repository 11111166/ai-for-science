"""Read-only, bounded ChEMBL dossiers. Never alter project scores or job results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
from urllib.parse import quote

import httpx
from .chembl_runtime import API_BASE, ChEMBLRuntimeAdapter, _number

from .dossier_cache import cached, _cache
from .config import get_settings


@lru_cache(maxsize=2)
def _snapshot_molecules(root: str):
    """Small, versioned ChEMBL subset already shipped with the five validated diseases."""
    result = {}
    path = Path(root)
    files = sorted(path.rglob('compound-evaluation-v1.json')) if path.is_dir() else ([path] if path.is_file() else [])
    for file in files:
        try:
            payload = json.loads(file.read_text(encoding='utf-8'))
            if payload.get('schema_version') != 'compound-evaluation-product-snapshot-v1' or not payload.get('quality_passed'):
                continue
            for molecule in payload.get('compounds') or []:
                for match in molecule.get('target_matches') or []:
                    symbol = str(match.get('target_symbol') or '').upper()
                    identifier = str(molecule.get('molecule_chembl_id') or '')
                    if symbol and re.fullmatch(r'CHEMBL\d+', identifier):
                        result.setdefault(symbol, {})[identifier] = (molecule, match, payload.get('snapshot_id'))
        except (OSError, ValueError, TypeError):
            continue
    return result


def _snapshot_items(symbol):
    root = get_settings().compound_evaluation_snapshot_path
    if not root:
        return []
    rows = _snapshot_molecules(root).get(symbol, {})
    items = []
    for identifier, (molecule, match, snapshot_id) in rows.items():
        items.append({
            'identifier': identifier, 'name': molecule.get('name') or identifier,
            'max_phase': _number(molecule.get('max_phase')), 'molecule_type': molecule.get('molecule_type'),
            'canonical_smiles': molecule.get('canonical_smiles'), 'descriptors': molecule.get('descriptors') or {},
            'target_chembl_id': match.get('target_chembl_id'), 'target_symbol': symbol,
            'mechanisms': [{'mechanism_of_action': match['mechanism']}] if match.get('mechanism') else [],
            'activities': [], 'activity_count': match.get('activity_count'),
            'assay_count': match.get('assay_count'), 'document_count': match.get('document_count'),
            'pchembl_median': match.get('pchembl_median'), 'pchembl_best': match.get('pchembl_best'),
            'evidence_source': 'ChEMBL 固定快照', 'snapshot_id': snapshot_id,
            'representative_assay': match.get('representative_assay'),
            'representative_document': match.get('representative_document'),
        })
    return sorted(items, key=lambda row: (-int(row.get('activity_count') or 0), -float(row.get('pchembl_median') or 0), row['identifier']))[:24]


def _bounded_json(client, url, max_bytes=1_500_000, **kwargs):
    """Never decode an unbounded public assay response inside the API worker."""
    try:
        with client.stream('GET', url, **kwargs) as response:
            response.raise_for_status()
            if int(response.headers.get('content-length') or 0) > max_bytes:
                return None
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > max_bytes:
                    return None
        payload = json.loads(content)
        return payload if isinstance(payload, dict) else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _pubchem_assays(symbol):
    """Small, directly target-annotated experimental subset; not a drug claim."""
    root = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug'
    with httpx.Client(timeout=httpx.Timeout(5, connect=3), headers={'User-Agent': 'AI-for-Science-target-evidence/1.0'}) as client:
        aid_payload = _bounded_json(client, f'{root}/assay/target/genesymbol/{quote(symbol)}/aids/JSON', 100_000)
        if aid_payload is None:
            return None
        aids = [int(v) for v in (aid_payload.get('IdentifierList') or {}).get('AID') or [] if str(v).isdigit()][:30]
        if not aids:
            return []
        descriptions = _bounded_json(client, f'{root}/assay/aid/{",".join(map(str,aids))}/description/JSON')
        if descriptions is None:
            return None
        eligible = []
        for block in descriptions.get('PC_AssayContainer') or []:
            desc = (block.get('assay') or {}).get('descr') or {}
            title = str(desc.get('name') or '')
            lower = title.lower()
            if re.search(r'sirna|rnai|knockdown|crispr|anti.target|off.target', lower):
                continue
            if not re.search(r'inhibit|binding|affinity|potency|activity', lower):
                continue
            aid = (desc.get('aid') or {}).get('id')
            if aid in aids:
                eligible.append((int('human' in lower or 'recombinant' in lower), aid, title))
        eligible.sort(key=lambda row: (-row[0], aids.index(row[1])))
        found = {}
        for _, aid, title in eligible[:2]:
            raw = _bounded_json(client, f'{root}/assay/aid/{aid}/JSON')
            concise = _bounded_json(client, f'{root}/assay/aid/{aid}/concise/JSON')
            if not raw or not concise:
                continue
            submit = raw.get('PC_AssaySubmit') or {}
            descr = (submit.get('assay') or {}).get('descr') or {}
            result_names = {r.get('tid'): r.get('name') for r in descr.get('results') or []}
            cid_by_sid = {}
            for row in (concise.get('Table') or {}).get('Row') or []:
                cells = row.get('Cell') or []
                if len(cells) >= 3 and str(cells[1]).isdigit() and str(cells[2]).isdigit():
                    cid_by_sid[int(cells[1])] = int(cells[2])
            pmids = [str(x['xref']['pmid']) for x in descr.get('xref') or [] if (x.get('xref') or {}).get('pmid')]
            for row in submit.get('data') or []:
                cid = cid_by_sid.get(row.get('sid'))
                if not cid:
                    continue
                values = {result_names.get(item.get('tid')): next(iter((item.get('value') or {}).values()), None) for item in row.get('data') or []}
                kind = str(values.get('Standard Type') or '')
                raw_value = values.get('Standard Value')
                if kind not in {'IC50', 'EC50', 'Ki', 'Kd', 'XI50', 'Inhibition'} or not isinstance(raw_value, (float, int)):
                    continue
                found.setdefault(cid, []).append({'aid': aid, 'assay_name': title, 'result_type': kind,
                    'reported_value': raw_value, 'relation': values.get('Standard Relation'), 'pubmed_ids': pmids[:3]})
        if not found:
            return []
        ranked = sorted(found, key=lambda cid: (-len(found[cid]), cid))[:15]
        properties = _bounded_json(client, f'{root}/compound/cid/{",".join(map(str,ranked))}/property/Title,IUPACName,CanonicalSMILES,MolecularWeight/JSON') or {}
        by_cid = {row['CID']: row for row in (properties.get('PropertyTable') or {}).get('Properties') or [] if row.get('CID')}
        return [{'identifier': f'CID:{cid}', 'name': (by_cid.get(cid) or {}).get('Title') or f'PubChem CID {cid}',
                 'canonical_smiles': (by_cid.get(cid) or {}).get('ConnectivitySMILES'),
                 'molecular_weight': _number((by_cid.get(cid) or {}).get('MolecularWeight')),
                 'assays': found[cid][:3], 'source_url': f'https://pubchem.ncbi.nlm.nih.gov/compound/{cid}'} for cid in ranked]


def _clinical_drugs(ensembl_id):
    if not re.fullmatch(r'ENSG\d{11}', ensembl_id or ''):
        return None
    query = '''query ($id: String!) { target(ensemblId: $id) { drugAndClinicalCandidates { count rows { id maxClinicalStage drug { id name drugType maximumClinicalStage } } } } }'''
    try:
        with httpx.Client(timeout=httpx.Timeout(6, connect=3)) as client:
            response = client.post('https://api.platform.opentargets.org/api/v4/graphql', json={'query': query, 'variables': {'id': ensembl_id}})
            response.raise_for_status()
            payload = response.json()
        if payload.get('errors'):
            return None
        rows = (((payload.get('data') or {}).get('target') or {}).get('drugAndClinicalCandidates') or {}).get('rows') or []
        return [{'identifier': (row.get('drug') or {}).get('id') or row.get('id'),
                 'name': (row.get('drug') or {}).get('name') or row.get('id'),
                 'drug_type': (row.get('drug') or {}).get('drugType'),
                 'clinical_stage': row.get('maxClinicalStage'),
                 'source_url': f'https://platform.opentargets.org/target/{ensembl_id}'}
                for row in rows[:24] if (row.get('drug') or {}).get('id')]
    except (httpx.HTTPError, ValueError, TypeError):
        return None


class Catalog:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=True)

    def get(self, path, **params):
        try:
            r = self.client.get(f'{API_BASE}/{path}.json', params=params)
            r.raise_for_status()
            payload = r.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    @staticmethod
    def metadata(status, warnings):
        return {"status": status, "warnings": warnings, "source": "ChEMBL REST API", "retrieved_at": datetime.now(timezone.utc).isoformat(), "scope": "bounded_live_lookup", "version": "drug-dossier-v1"}

    def dossier(self, identifier):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f = [pool.submit(self.get, f'molecule/{identifier}'), pool.submit(self.get, 'drug_indication', molecule_chembl_id=identifier, limit=50), pool.submit(self.get, 'mechanism', molecule_chembl_id=identifier, limit=50)]
            molecule, indications, mechanisms = [x.result() for x in f]
        warnings = [label for payload, label in [(molecule, '分子身份暂未取回'), (indications, '适应症资料暂未取回'), (mechanisms, '机制资料暂未取回')] if payload is None]
        if molecule is None:
            return {**self.metadata('unavailable', warnings), 'identifier': identifier, 'items': []}
        aliases = sorted({str(x.get('molecule_synonym') or x.get('synonyms')) for x in molecule.get('molecule_synonyms') or [] if x.get('molecule_synonym') or x.get('synonyms')})[:30]
        items = []
        for row in (indications or {}).get('drug_indications') or []:
            item = {k: row.get(k) for k in ['mesh_id','mesh_heading','efo_id','efo_term','indication_refs']}
            item['max_phase_for_ind'] = _number(row.get('max_phase_for_ind'))
            items.append(item)
        mechs = [{k: row.get(k) for k in ['target_chembl_id','mechanism_of_action','action_type','mechanism_refs']} for row in (mechanisms or {}).get('mechanisms') or []]
        return {**self.metadata('partial' if warnings else 'available', warnings), 'identifier': identifier, 'name': molecule.get('pref_name') or identifier, 'aliases': aliases, 'molecule_type': molecule.get('molecule_type'), 'max_phase': _number(molecule.get('max_phase')), 'first_approval': molecule.get('first_approval'), 'black_box_warning': molecule.get('black_box_warning'), 'withdrawn_flag': molecule.get('withdrawn_flag'), 'canonical_smiles': (molecule.get('molecule_structures') or {}).get('canonical_smiles'), 'indications': items, 'mechanisms': mechs, 'indication_total': (indications or {}).get('page_meta', {}).get('total_count'), 'mechanism_total': (mechanisms or {}).get('page_meta', {}).get('total_count'), 'source_url': f'https://www.ebi.ac.uk/chembl/explore/compound/{identifier}', 'limit': 50, 'sections': {'identity': 'available', 'indications': 'unavailable' if indications is None else 'available' if items else 'empty', 'mechanisms': 'unavailable' if mechanisms is None else 'available' if mechs else 'empty'}}

    def activities(self, identifier, target):
        payload = self.get('activity', molecule_chembl_id=identifier, target_chembl_id=target,
                           target_organism='Homo sapiens', standard_flag=1,
                           data_validity_comment__isnull='true', potential_duplicate=0,
                           limit=100, order_by='assay_chembl_id')
        if payload is None:
            return {**self.metadata('unavailable', ['实验记录暂未取回']), 'items': []}
        fields = ['standard_type', 'standard_value', 'standard_relation', 'standard_units',
                  'assay_chembl_id', 'document_chembl_id', 'assay_description', 'assay_type',
                  'target_organism', 'bao_label', 'pchembl_value']
        items = [{k: row.get(k) for k in fields} for row in payload.get('activities') or []]
        return {**self.metadata('available', []), 'items': items,
                'total': (payload.get('page_meta') or {}).get('total_count', len(items)), 'limit': 100}

    def target(self, symbol, ensembl_id=None):
        symbol = symbol.upper()
        # These independent sources must remain useful if ChEMBL is temporarily down.
        with ThreadPoolExecutor(max_workers=2) as source_pool:
            pubchem_future = source_pool.submit(_pubchem_assays, symbol)
            clinical_future = source_pool.submit(_clinical_drugs, ensembl_id)
            snapshot = _snapshot_items(symbol)
            search = self.get('target/search', q=symbol, limit=20)
            target = ChEMBLRuntimeAdapter._exact_target(search, symbol) if search else None
            tid = target['target_chembl_id'] if target else None
            if tid:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f = pool.submit(self.get, 'mechanism', target_chembl_id=tid, limit=100)
                    g = pool.submit(self.get, 'activity', target_chembl_id=tid, target_organism='Homo sapiens', assay_type='B', standard_flag=1, standard_relation='=', pchembl_value__isnull='false', data_validity_comment__isnull='true', potential_duplicate=0, limit=100, order_by='-pchembl_value')
                    mechanisms, activities = f.result(), g.result()
            else:
                mechanisms, activities = None, None
            pubchem_items = pubchem_future.result()
            clinical_items = clinical_future.result()
        grouped = {}
        for row in (mechanisms or {}).get('mechanisms') or []:
            mid = row.get('molecule_chembl_id')
            if mid and re.fullmatch(r'CHEMBL\d+', mid):
                entry = grouped.setdefault(mid, {'mechanisms': [], 'activities': []})
                entry['mechanisms'].append({k: row.get(k) for k in ['action_type', 'mechanism_of_action']})
        # Mechanism-linked drugs first; include up to eight activity-only molecules.
        chosen = list(grouped)[:16]
        for row in (activities or {}).get('activities') or []:
            mid = row.get('molecule_chembl_id')
            if not mid or not re.fullmatch(r'CHEMBL\d+', mid):
                continue
            if mid not in chosen and len(chosen) < 24:
                chosen.append(mid)
            if mid in chosen:
                grouped.setdefault(mid, {'mechanisms': [], 'activities': []})['activities'].append({k: row.get(k) for k in ['standard_type','standard_value','standard_units','standard_relation','pchembl_value','assay_chembl_id','document_chembl_id']})
        molecules = self.get('molecule', molecule_chembl_id__in=','.join(chosen), limit=24) if chosen else {'molecules': []}
        by_id = {row['molecule_chembl_id']: row for row in (molecules or {}).get('molecules') or []}
        items = []
        for mid in chosen:
            mol = by_id.get(mid, {})
            items.append({'identifier': mid, 'name': mol.get('pref_name') or mid, 'max_phase': _number(mol.get('max_phase')), 'molecule_type': mol.get('molecule_type'), 'canonical_smiles': (mol.get('molecule_structures') or {}).get('canonical_smiles'), 'descriptors': {name: _number((mol.get('molecule_properties') or {}).get(key)) for name, key in [('molecular_weight','full_mwt'), ('logp','alogp'), ('tpsa','psa'), ('qed','qed_weighted')]}, 'target_chembl_id': tid, 'target_symbol': symbol, **grouped[mid]})
        existing = {row['identifier'] for row in items}
        items.extend(row for row in snapshot if row['identifier'] not in existing)
        items.sort(key=lambda x: (not bool(x['mechanisms']), -(_number(x['max_phase'], -1)), -int(x.get('activity_count') or len(x['activities'])), x['identifier']))
        items = items[:24]
        source_status = {
            'chembl': 'unavailable' if search is None else 'not_mapped' if not target else 'partial' if mechanisms is None or activities is None else 'available',
            'snapshot': 'available' if snapshot else 'not_covered',
            'pubchem': 'unavailable' if pubchem_items is None else 'available' if pubchem_items else 'no_match',
            'open_targets': 'not_configured' if not ensembl_id else 'unavailable' if clinical_items is None else 'available' if clinical_items else 'no_match',
        }
        warnings = []
        if search is None:
            warnings.append('ChEMBL 实时靶点映射暂不可用；下列其他来源仍可独立核对。')
        elif not target:
            warnings.append('ChEMBL 本次未精确匹配人源单蛋白靶点；请核查标识。')
        else:
            warnings.extend(label for value,label in [(mechanisms,'ChEMBL 机制资料暂未取回'),(activities,'ChEMBL 活性资料暂未取回'),(molecules,'ChEMBL 分子名称与阶段暂未取回')] if value is None)
        usable = search is not None or any(value in {'available', 'no_match'} for value in source_status.values())
        status = 'unavailable' if not usable else 'partial' if warnings or any(value == 'unavailable' for value in source_status.values()) else 'available'
        return {**self.metadata(status, warnings), 'items': items, 'pubchem_items': pubchem_items or [],
                'clinical_items': clinical_items or [], 'source_status': source_status, 'target_chembl_id': tid,
                'mechanism_total': (mechanisms or {}).get('page_meta', {}).get('total_count'),
                'activity_total': (activities or {}).get('page_meta', {}).get('total_count'), 'limit': 24}


def lookup(kind, value, refresh=False):
    def build():
        catalog = Catalog()
        try:
            return catalog.activities(*value) if kind == 'activities' else catalog.dossier(value) if kind == 'molecule' else catalog.target(*value) if isinstance(value, tuple) else catalog.target(value)
        finally:
            catalog.client.close()
    return cached((kind, value), build, force=refresh)
