"""Versioned, offline non-drug evidence exploration for the full disease catalogue.

This is hypothesis prioritisation of recorded associations, not de novo gene
prediction, donor-level differential expression, or an efficacy probability.
"""
from __future__ import annotations
import gzip
import hashlib
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from .open_targets import OpenTargetsError, normalize_disease_text

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'data/novel-catalog-v1'
SCHEMA = 'novel-catalog-evidence-exploration-v1'
# Fixed denominator: missing channels never increase other channel weights.
CHANNELS = {
    'genetics': ('人类遗传证据', .45, ('genetic_association', 'genetic_literature')),
    'somatic': ('体细胞变异证据', .20, ('somatic_mutation',)),
    'pathway': ('功能通路证据', .15, ('affected_pathway',)),
    'expression': ('表达证据', .10, ('rna_expression',)),
    'animal': ('动物模型证据', .10, ('animal_model',)),
}


def score_association(row: dict) -> dict | None:
    scores = {x['id']: float(x['score']) for x in row['datatypeScores']}
    values = {key: max((scores.get(t, 0) for t in spec[2]), default=0) for key, spec in CHANNELS.items()}
    if any(not 0 <= v <= 1 for v in scores.values()):
        raise ValueError('Invalid public source score')
    if not any(values.values()):
        return None  # Clinical-only and literature-only hits cannot become candidates.
    support = round(sum(values[k] * spec[1] for k, spec in CHANNELS.items()), 6)
    present = [k for k, v in values.items() if v > 0]
    return {**row, 'exploration_score': support, 'channels': values,
            'support_count': len(present), 'known_drug': any(scores.get(k, 0) > 0 for k in ('clinical', 'known_drug'))}


@lru_cache(maxsize=8)
def _manifest(path: str, modified: int):
    payload = json.loads(Path(path).read_text())
    if payload.get('schema_version') != SCHEMA or payload.get('quality_passed') is not True:
        raise ValueError('Invalid catalogue manifest')
    return payload


class CatalogNovelAdapter:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        try:
            file = self.path / 'manifest.json'
            self.manifest = _manifest(str(file.resolve()), file.stat().st_mtime_ns)
        except (OSError, ValueError) as exc:
            raise OpenTargetsError('NOVEL_CATALOG_UNAVAILABLE', '公共证据探索数据暂不可用。') from exc

    def match(self, query: str):
        normalized = normalize_disease_text(query)
        matches = [d for d in self.manifest['diseases'] if normalized in {
            normalize_disease_text(x) for x in [d['id'], d['name'], d['name_zh'], *d.get('aliases', [])]
        }]
        # Multiple catalogue entries can refer to the same canonical disease.
        by_id = {d['id']: d for d in matches}
        if len(by_id) != 1:
            raise OpenTargetsError('NOVEL_CATALOG_DISEASE_NOT_FOUND', '当前疾病尚未匹配到目录中的标准疾病实体。')
        return next(iter(by_id.values()))

    def availability(self, query: str):
        d = self.match(query)
        return {'query': query, 'available': True, 'code': 'AVAILABLE', 'disease_id': d['id'],
                'analysis_tier': 'public_evidence_exploration', 'candidate_count': d['candidate_count'],
                'catalog_count': len(self.manifest['diseases']), 'diseases': [],
                'message': ('公共证据探索已就绪：本病直接证据不足，候选来自疾病子类型，需核对适用范围。' if d.get('association_scope') == 'including_descendants' else '公共证据探索已就绪：按遗传、功能及表达证据筛选候选，药物记录单独标注；未运行专属单细胞或共定位模型。')}

    def discover(self, query_text: str, top_n=50, include_indirect=False, include_evidence_records=True):
        if isinstance(top_n, bool) or not 1 <= top_n <= 200:
            raise OpenTargetsError('INVALID_TOP_N', '候选数应在 1–200 之间。')
        d = self.match(query_text)
        try:
            encoded = (self.path / d['file']).read_bytes()
            if hashlib.sha256(encoded).hexdigest() != d['sha256']:
                raise ValueError('Snapshot hash mismatch')
            payload = json.loads(gzip.decompress(encoded))
            if payload['disease_id'] != d['id']: raise ValueError('Disease mismatch')
        except (OSError, ValueError, KeyError) as exc:
            raise OpenTargetsError('NOVEL_CATALOG_INVALID', '公共证据探索数据校验失败，请联系管理员。') from exc
        retrieved = datetime.fromisoformat(self.manifest['retrieved_at'])
        version = self.manifest['source_version']
        indirect = d.get('association_scope') == 'including_descendants'
        reference = f'https://platform.opentargets.org/disease/{d["id"]}/associations'
        targets=[]; evidence=[]
        for rank, row in enumerate(payload['targets'][:top_n], 1):
            target = row['target']; channels = row['channels']; present = [k for k,v in channels.items() if v > 0]
            strongest = max(present, key=lambda k: channels[k])
            scope = '多类公共证据支持' if len(present) >= 2 else '单类证据，优先独立复核'
            summary = ('疾病子类型证据；' if indirect else '') + f'{CHANNELS[strongest][0]}较突出；{scope}。'
            gaps = ('需核对具体子类型，不能外推至所有患者。' if indirect else '') + '尚未验证作用方向、疾病细胞响应及安全窗口。'
            next_step = '先用独立遗传或功能证据复核，再做靶点扰动与疾病表型验证。'
            categories = {k:{'status':'available' if v > 0 else 'unavailable', 'score':v if v > 0 else None, 'datatypes':list(CHANNELS[k][2])} for k,v in channels.items()}
            datatype_scores={x['id']:x['score'] for x in row['datatypeScores']}
            datasource_scores={x['id']:x['score'] for x in row['datasourceScores']}
            breakdown={'registry_version':SCHEMA,'score_contract_version':SCHEMA,'discovery_strategy':'novel',
                       'analysis_tier':'public_evidence_exploration','categories':categories,
                       'datatype_scores':datatype_scores,'datasource_scores':datasource_scores,
                       'held_out':{'known_drug_label':row['known_drug']}, 'candidate_origin':'已收录非药物证据探索',
                       'exploration':{'summary':summary,'gaps':gaps,'next_step':next_step,'support_count':len(present),
                                      'channels':[CHANNELS[k][0] for k in present], 'clinical_label':'已有本病临床药物记录' if row['known_drug'] else '本批数据未见本病临床药物记录'}}
            targets.append({'external_id':target['id'],'symbol':target['approvedSymbol'],'name':target['approvedName'],
                            'biotype':target['biotype'],'rank':rank,'score':row['exploration_score'],
                            'score_origin':SCHEMA,'derived_score':None,'causal_confidence':None,'disease_context':None,
                            'development_feasibility':None,'evidence_coverage':len(present)/len(CHANNELS),
                            'evidence_count':len(present),'source_count':sum(v>0 for k,v in datasource_scores.items() if k not in ('europepmc','clinical_precedence','chembl')),
                            'evidence_breakdown':breakdown,'direction':'not_available','raw_reference':reference})
            for key in present:
                evidence.append({'target_external_id':target['id'],'external_evidence_id':f'{d["id"]}:{target["id"]}:exploration:{key}',
                                 'disease_id':d['id'],'evidence_type':'non_drug_datatype_aggregate','datatype':CHANNELS[key][2][0],
                                 'datasource':None,'category':key,'source':'Open Targets Platform','summary':CHANNELS[key][0],
                                 'score':channels[key],'direction_on_target':None,'direction_on_trait':None,'evidence_count':1,
                                 'confidence':None,'raw_reference':reference,
                                 'details':{'aggregation_level':'datatype','datasource_scores':datasource_scores,'channel_datatypes':list(CHANNELS[key][2]),'source_scores':datatype_scores,'not_independent_replications':True,'association_scope':d.get('association_scope','direct')}})
        quality={'discovery_strategy':'novel','analysis_tier':'public_evidence_exploration','scientific_status':'unvalidated_hypothesis_prioritisation',
                 'source_version':version,'source_registry_version':SCHEMA,'score_contract_version':SCHEMA,'snapshot_sha256':d['sha256'],
                 'targets_retrieved':len(targets),'published_target_count':len(payload['targets']),
                 'normalized_evidence_records':len(evidence),'targets_with_genetics':sum(t['evidence_breakdown']['categories']['genetics']['status']=='available' for t in targets),
                 'targets_with_known_drug':sum(t['evidence_breakdown']['held_out']['known_drug_label'] for t in targets),
                 'build_counts':{'candidate_genes':d['candidate_count'],'scanned_associations':d['association_count']},
                 'coverage':{'mean':sum(t['evidence_coverage'] for t in targets)/len(targets) if targets else None},'cache_hit':True,
                 'evidence_materialization':'non_drug_datatype_aggregates','validation_status':'not_externally_validated',
                 'conclusion':f'从 {d["association_count"]} 个疾病关联基因中筛出 {d["candidate_count"]} 个有非药物证据的候选。' if targets else '已完成证据扫描；本批数据没有满足非药物证据条件的候选，不以文献提及或药物记录补足。',
                 'limitations':['候选基于数据库已有非药物关联，不代表首次发现的新基因。','未运行疾病专属单细胞、共定位或网络模型；分数用于本病候选排序，不代表有效概率。'],
                 'association_scope':d.get('association_scope','direct'),'score_weights':{k:v[1] for k,v in CHANNELS.items()}}
        quality['limitations'].append('候选范围限于 HGNC 已批准的蛋白编码基因，不包含非编码 RNA。')
        if d['id'].startswith('HP_'):
            quality['limitations'].insert(0,'该目录条目在源数据库中按临床表型聚合；需结合疾病定义与患者分层复核。')
        if indirect:
            quality['limitations'].insert(0,'本病直接证据未形成候选，本次纳入疾病子类型的非药物证据；不代表所有子类型或患者均适用。')
        benchmark={'status':'not_validated','reason':'公共证据探索尚未经过外部队列和前瞻实验验证。'}
        return {'metadata':{'source':'Open Targets 公共证据探索','source_version':version,'api_version':SCHEMA,'endpoint':reference},
                'resolution':{'query':query_text,'canonical_name':d['name'],'disease_id':d['id'],'ontology':d['id'].split('_')[0],
                              'synonyms':d.get('aliases',[]),'resolution_status':'resolved','resolution_confidence':1.0,
                              'resolution_method':'versioned_catalog_exact_identity','raw_reference':reference,'cache_hit':True},
                'parameters':{'top_n':top_n,'include_indirect':indirect,'discovery_strategy':'novel','analysis_tier':'public_evidence_exploration'},
                'targets':targets,'evidence':evidence,'data_quality':quality,'benchmark':benchmark,
                'warnings':['PUBLIC_EVIDENCE_EXPLORATION_NOT_MULTIMODAL_VALIDATION'],'raw_reference':reference,'retrieved_at':retrieved}


def catalog_path(settings):
    return getattr(settings, 'novel_target_catalog_path', str(DEFAULT_PATH))


def novel_availability(settings, query):
    from .novel_target_snapshot import novel_target_availability
    available = novel_target_availability(settings.novel_target_discovery_snapshot_path, query)
    if available['available']:
        return {**available, 'analysis_tier':'disease_specific_multimodal', 'message':'疾病专属多组学探索：使用 GWAS、共定位、供体级单细胞和网络证据；结果仍需实验验证。'}
    if available['code'] not in ('NOVEL_TARGET_DISEASE_NOT_FOUND', 'NOVEL_TARGET_NOT_CONFIGURED'):
        return available  # A broken configured pack must not be silently replaced.
    try:
        return CatalogNovelAdapter(catalog_path(settings)).availability(query)
    except OpenTargetsError as exc:
        if exc.code == 'NOVEL_CATALOG_DISEASE_NOT_FOUND':
            return {'query':query,'available':False,'code':exc.code,'diseases':[],'message':'未匹配到目录中的标准疾病，请使用疾病目录名称或标准疾病 ID。'}
        return available


def novel_adapter(settings, query):
    from .novel_target_snapshot import NovelTargetSnapshotAdapter
    if settings.novel_target_discovery_snapshot_path:
        try:
            adapter=NovelTargetSnapshotAdapter(settings.novel_target_discovery_snapshot_path)
            adapter.resolve_disease(query)
            return adapter
        except OpenTargetsError as exc:
            if exc.code != 'NOVEL_TARGET_DISEASE_NOT_FOUND': raise
    adapter=CatalogNovelAdapter(catalog_path(settings))
    adapter.match(query)
    return adapter
