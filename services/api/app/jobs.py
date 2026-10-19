import hashlib
import json
import threading
from datetime import datetime, timezone

from sqlalchemy import delete, select

from .adapters import COMPOUNDS, IPF_DISEASE_SIGNATURE, PERTURBATION_SIGNATURES, PROVENANCE, TARGETS
from .algorithms import normalize_weights, signature_reversal, weighted_score
from .database import SessionLocal
from .models import (
    Compound,
    CompoundEvaluation,
    Job,
    JobEvent,
    JobStatus,
    ModelRun,
    PerturbationResult,
    Project,
    Ranking,
    TargetCandidate,
    TargetDiscoveryRun,
    TargetEvidence,
)
from .open_targets import OpenTargetsAdapter, OpenTargetsError, REGISTRY_VERSION
from .config import get_settings
from .target_snapshot import TargetSnapshotAdapter
from .novel_catalog import CatalogNovelAdapter, catalog_path, novel_adapter
from .perturbation_snapshot import PerturbationKnockoutSnapshotAdapter
from .virtual_cell_snapshot import VirtualCellSnapshotAdapter
from .compound_snapshot import CompoundEvaluationSnapshotAdapter
from .intervention_baseline import MODEL_VERSION as INTERVENTION_BASELINE_VERSION, build_intervention_baseline
from .virtual_cell_baseline import MODEL_VERSION as VIRTUAL_CELL_BASELINE_VERSION, build_virtual_cell_baselines
from .chembl_runtime import ChEMBLRuntimeAdapter
from .omics_analysis import OmicsAnalysisError, process_omics_analysis
from .fungal_compound import FungalCompoundError, process_fungal_compound
from .bacterial_compound import BacterialCompoundError, process_bacterial_compound
from .fungal_target import FungalTargetError, process_fungal_target
from .persistent_cache import invalidate_response_cache


def now():
    return datetime.now(timezone.utc)


def record(db, job: Job, status: str, stage: str, progress: int, message: str):
    job.status = status
    job.stage = stage
    job.progress = progress
    db.add(JobEvent(job_id=job.id, status=status, stage=stage, progress=progress, message=message))
    db.commit()
    if status in {JobStatus.completed.value, JobStatus.failed.value, JobStatus.cancelled.value}:
        invalidate_response_cache()


def model_run(
    db,
    job: Job,
    adapter: str,
    model_name: str,
    model_version: str,
    provenance: dict,
    demo: bool,
):
    payload = json.dumps({"project": job.project_id, "parameters": job.parameters, "input": job.input_version}, sort_keys=True)
    db.add(ModelRun(
        job_id=job.id,
        adapter=adapter,
        adapter_mode=job.adapter_mode,
        model_name=model_name,
        model_version=model_version,
        input_hash=hashlib.sha256(payload.encode()).hexdigest(),
        parameters=job.parameters,
        provenance=provenance,
        demo=demo,
    ))


def process_target_discovery(db, job: Job):
    """Preserve the original IPF preview as an append-only precomputed run."""
    project = db.get(Project, job.project_id)
    retrieved_at = now()
    run = TargetDiscoveryRun(
        job_id=job.id,
        project_id=job.project_id,
        query=project.disease if project else "IPF",
        resolution_status="precomputed",
        canonical_name="Idiopathic pulmonary fibrosis",
        disease_id="EFO_0000768",
        ontology="EFO",
        synonyms=["IPF"],
        resolution_confidence=1.0,
        resolution_method="precomputed_demo_fixture",
        source=PROVENANCE["label"],
        source_version=PROVENANCE["version"],
        source_api_version="not_applicable",
        evidence_registry_version=REGISTRY_VERSION,
        retrieved_at=retrieved_at,
        parameters=job.parameters,
        data_quality={
            "disease_resolution_status": "precomputed",
            "targets_retrieved": len(TARGETS),
            "source": PROVENANCE["label"],
            "source_version": PROVENANCE["version"],
            "warnings": ["PRECOMPUTED_DEMONSTRATION_DATA"],
            "errors": [],
        },
        benchmark={"status": "not_yet_validated", "reason": "Precomputed UI fixture is not benchmark input."},
        benchmark_status="not_yet_validated",
        warnings=["PRECOMPUTED_DEMONSTRATION_DATA"],
    )
    db.add(run)
    db.flush()
    for index, item in enumerate(TARGETS):
        target = TargetCandidate(
            project_id=job.project_id,
            job_id=job.id,
            target_discovery_run_id=run.id,
            rank=index + 1,
            score_origin="precomputed_demo",
            evidence_coverage=None,
            evidence_count=1,
            source_count=1,
            evidence_breakdown={"registry_version": REGISTRY_VERSION, "status": "precomputed_demo"},
            data_mode="precomputed",
            source=PROVENANCE["label"],
            source_version=PROVENANCE["version"],
            retrieved_at=retrieved_at,
            **item,
        )
        db.add(target)
        db.flush()
        db.add(TargetEvidence(
            target_id=target.id,
            evidence_type="demonstration-evidence",
            source="Public-domain workflow fixture",
            summary="Illustrative evidence record for interface and traceability validation; not a scientific conclusion.",
            score=max(0.0, item["score"] - 0.08),
            evidence_count=1,
            source_version=PROVENANCE["version"],
            retrieved_at=retrieved_at,
            data_mode="precomputed",
            details={"scientific_conclusion": False},
            provenance=PROVENANCE,
        ))
        record(db, job, JobStatus.running.value, "ranking_targets", 25 + (index + 1) * 10, f"Prepared demonstration target {item['symbol']}")
    job.output_ref = f"projects/{job.project_id}/targets"
    return {
        "adapter": "IPFPrecomputedTargetDiscoveryAdapter",
        "model_name": "IPF workflow demonstration adapter",
        "model_version": PROVENANCE["version"],
        "provenance": PROVENANCE,
        "demo": True,
    }


def process_target_discovery_real(db, job: Job):
    project = db.get(Project, job.project_id)
    query_text = str(job.parameters.get("disease_query") or (project.disease if project else "")).strip()
    top_n = int(job.parameters.get("top_n", 50))
    include_indirect = bool(job.parameters.get("include_indirect", True))
    include_evidence_records = bool(job.parameters.get("include_evidence_records", False))
    discovery_strategy = str(job.parameters.get("discovery_strategy") or "established")
    if discovery_strategy not in {"established", "novel"}:
        raise OpenTargetsError("INVALID_DISCOVERY_STRATEGY", "靶点发现策略必须是已知靶点排序或新靶点探索。")
    if discovery_strategy == "novel":
        novel_snapshot_path = get_settings().novel_target_discovery_snapshot_path
        adapter = novel_adapter(get_settings(), query_text)
        snapshot_path = novel_snapshot_path
    else:
        snapshot_path = get_settings().target_discovery_snapshot_path
        adapter = TargetSnapshotAdapter(snapshot_path) if snapshot_path else OpenTargetsAdapter(db)
    try:
        record(db, job, JobStatus.running.value, "resolving_disease", 20, "正在使用新靶点发现快照解析疾病" if discovery_strategy == "novel" else "正在使用版本化公共数据解析疾病" if snapshot_path else "正在使用 Open Targets 解析疾病")
        try:
            result = adapter.discover(
                query_text,
                top_n=top_n,
                include_indirect=include_indirect,
                include_evidence_records=include_evidence_records,
            )
        except OpenTargetsError as exc:
            if discovery_strategy == "novel" or not snapshot_path or exc.code != "TARGET_SNAPSHOT_DISEASE_NOT_FOUND":
                raise
            adapter = OpenTargetsAdapter(db)
            result = adapter.discover(
                query_text,
                top_n=top_n,
                include_indirect=include_indirect,
                include_evidence_records=include_evidence_records,
            )
    except OpenTargetsError as exc:
        if discovery_strategy != "established" or exc.code != "DISEASE_NOT_RESOLVED":
            raise
        # A few catalogue identities can disappear from the current Open Targets
        # API while remaining valid in the pinned 26.06 evidence release. Use the
        # versioned local evidence pack instead of mis-resolving a similarly named
        # disease or leaving the catalogue entry without results.
        close = getattr(adapter, "close", None)
        if close:
            close()
        adapter = CatalogNovelAdapter(catalog_path(get_settings()))
        try:
            result = adapter.discover(
                query_text,
                top_n=top_n,
                include_indirect=include_indirect,
                include_evidence_records=False,
            )
        except OpenTargetsError:
            raise exc
        result["metadata"]["source"] = "Open Targets versioned catalogue evidence"
        result["parameters"]["discovery_strategy"] = "established"
        quality = result["data_quality"]
        quality["discovery_strategy"] = "established"
        quality["analysis_tier"] = "versioned_public_evidence_baseline"
        quality.setdefault("limitations", []).append("当前实时接口已撤下该目录实体，本结果固定使用 26.06 版本化公共证据。")
        result.setdefault("warnings", []).append("LIVE_DISEASE_ENTITY_UNAVAILABLE_USING_VERSIONED_EVIDENCE")
        for target in result["targets"]:
            target["evidence_breakdown"]["discovery_strategy"] = "established"
            target["evidence_breakdown"]["analysis_tier"] = "versioned_public_evidence_baseline"
    finally:
        close = getattr(adapter, "close", None)
        if close:
            close()
    record(db, job, JobStatus.running.value, "normalizing_evidence", 65, "正在标准化靶点关联与证据")
    resolution = result["resolution"]
    if project:
        configured_disease_id = str(project.disease_id or "").strip()
        resolved_disease_id = str(resolution["disease_id"]).strip()
        if configured_disease_id and configured_disease_id != resolved_disease_id:
            raise OpenTargetsError(
                "DISEASE_ENTITY_MISMATCH",
                f"项目疾病实体为 {configured_disease_id}，本次解析为 {resolved_disease_id}。请确认疾病后再运行，避免把结果写入错误项目。",
            )
        if not configured_disease_id:
            project.disease_id = resolved_disease_id
    metadata = result["metadata"]
    retrieved_at = result["retrieved_at"]
    run = TargetDiscoveryRun(
        job_id=job.id,
        project_id=job.project_id,
        query=query_text,
        resolution_status=resolution["resolution_status"],
        canonical_name=resolution["canonical_name"],
        disease_id=resolution["disease_id"],
        ontology=resolution["ontology"],
        synonyms=resolution["synonyms"],
        resolution_confidence=resolution["resolution_confidence"],
        resolution_method=resolution["resolution_method"],
        source=metadata["source"],
        source_version=metadata["source_version"],
        source_api_version=metadata["api_version"],
        evidence_registry_version=result["data_quality"].get("source_registry_version", REGISTRY_VERSION),
        retrieved_at=retrieved_at,
        raw_reference=result["raw_reference"],
        parameters=result["parameters"],
        data_quality=result["data_quality"],
        benchmark=result["benchmark"],
        benchmark_status=result["benchmark"]["status"],
        warnings=result["warnings"],
        errors=[],
    )
    db.add(run)
    db.flush()
    target_ids: dict[str, str] = {}
    provenance = {
        "source": metadata["source"],
        "source_version": metadata["source_version"],
        "api_version": metadata["api_version"],
        "endpoint": metadata["endpoint"],
        "retrieved_at": retrieved_at.isoformat(),
        "raw_reference": result["raw_reference"],
        "data_mode": "real",
        "scientific_conclusion": False,
    }
    for item in result["targets"]:
        target = TargetCandidate(
            project_id=job.project_id,
            job_id=job.id,
            target_discovery_run_id=run.id,
            external_id=item["external_id"],
            symbol=item["symbol"],
            name=item["name"],
            biotype=item["biotype"],
            rank=item["rank"],
            score=item["score"],
            score_origin=item["score_origin"],
            derived_score=item["derived_score"],
            causal_confidence=item.get("causal_confidence"),
            disease_context=item.get("disease_context"),
            development_feasibility=item.get("development_feasibility"),
            evidence_coverage=item["evidence_coverage"],
            evidence_count=item["evidence_count"],
            source_count=item["source_count"],
            evidence_breakdown=item["evidence_breakdown"],
            data_mode="real",
            source=metadata["source"],
            source_version=metadata["source_version"],
            retrieved_at=retrieved_at,
            raw_reference=item["raw_reference"],
            direction=item["direction"],
            demo=False,
        )
        db.add(target)
        db.flush()
        target_ids[item["external_id"]] = target.id
    for item in result["evidence"]:
        target_id = target_ids.get(item["target_external_id"])
        if not target_id:
            continue
        db.add(TargetEvidence(
            target_id=target_id,
            external_evidence_id=item["external_evidence_id"],
            disease_id=item["disease_id"],
            evidence_type=item["evidence_type"],
            datatype=item["datatype"],
            datasource=item["datasource"],
            category=item["category"],
            source=item["source"],
            summary=item["summary"],
            score=item["score"],
            direction_on_target=item["direction_on_target"],
            direction_on_trait=item["direction_on_trait"],
            evidence_count=item["evidence_count"],
            source_version=metadata["source_version"],
            retrieved_at=retrieved_at,
            raw_reference=item["raw_reference"],
            data_mode="real",
            confidence=item["confidence"],
            details=item["details"],
            provenance=provenance,
        ))
    job.output_ref = f"jobs/{job.id}/target-discovery-result"
    return {
        "adapter": type(adapter).__name__,
        "model_name": ("Public non-drug evidence exploration v1" if type(adapter).__name__ == "CatalogNovelAdapter" else "Transparent Novel Target Discovery v1") if discovery_strategy == "novel" else "Open Targets association baseline",
        "model_version": metadata["source_version"],
        "provenance": provenance,
        "demo": False,
    }


def process_target_discovery_not_configured(db, job: Job):
    project = db.get(Project, job.project_id)
    run = TargetDiscoveryRun(
        job_id=job.id,
        project_id=job.project_id,
        query=str(job.parameters.get("disease_query") or (project.disease if project else "")),
        resolution_status="not_configured",
        source="not_configured",
        source_version="not_configured",
        source_api_version="not_configured",
        parameters=job.parameters,
        data_quality={"disease_resolution_status": "not_configured", "warnings": ["ADAPTER_NOT_CONFIGURED"], "errors": []},
        benchmark={"status": "not_yet_validated", "reason": "Adapter not configured."},
        warnings=["ADAPTER_NOT_CONFIGURED"],
    )
    db.add(run)
    db.flush()


def process_perturbation(db, job: Job):
    perturbation_job_ids = select(Job.id).where(Job.project_id == job.project_id, Job.type == "perturbation")
    db.execute(delete(PerturbationResult).where(
        PerturbationResult.project_id == job.project_id,
        PerturbationResult.job_id.in_(perturbation_job_ids),
    ))
    latest_target_job = db.scalar(
        select(Job.id)
        .where(Job.project_id == job.project_id, Job.type == "target_discovery", Job.status == JobStatus.completed.value)
        .order_by(Job.completed_at.desc(), Job.created_at.desc())
        .limit(1)
    )
    targets = db.scalars(
        select(TargetCandidate)
        .where(TargetCandidate.project_id == job.project_id, TargetCandidate.job_id == latest_target_job)
        .order_by(TargetCandidate.score.desc())
    ).all()
    if not targets:
        raise ValueError("TARGETS_REQUIRED")
    selected = [item for item in targets if item.selected] or targets[:3]
    for index, target in enumerate(selected):
        ranked_signature = PERTURBATION_SIGNATURES.get(target.symbol)
        if not ranked_signature:
            raise ValueError(f"PERTURBATION_SIGNATURE_NOT_CONFIGURED:{target.symbol}")
        result = signature_reversal(
            IPF_DISEASE_SIGNATURE["up"],
            IPF_DISEASE_SIGNATURE["down"],
            ranked_signature,
        )
        db.add(PerturbationResult(
            project_id=job.project_id,
            job_id=job.id,
            target_id=target.id,
            intervention=job.parameters.get("intervention", "inhibition"),
            cell_context=job.parameters.get("cell_context", "IPF fibroblast demonstration context"),
            reversal_score=result["reversal_score"],
            confidence=result["confidence"],
            result={
                **result,
                "label": "illustrative",
                "interpretation": "Real rank-enrichment calculation over synthetic demonstration signatures; not a scientific conclusion",
                "target": target.symbol,
                "signature_source": PROVENANCE["label"],
            },
        ))
        record(db, job, JobStatus.running.value, "simulating_interventions", 30 + (index + 1) * 15, f"Prepared perturbation for {target.symbol}")
    job.output_ref = f"projects/{job.project_id}/perturbations"


def _latest_targets(db, job: Job):
    requested_target_job = str(job.parameters.get("target_job_id") or "").strip()
    latest_target_job = requested_target_job or db.scalar(
        select(Job.id)
        .where(Job.project_id == job.project_id, Job.type == "target_discovery", Job.status == JobStatus.completed.value)
        .order_by(Job.completed_at.desc(), Job.created_at.desc())
        .limit(1)
    )
    if not latest_target_job:
        raise ValueError("TARGETS_REQUIRED")
    target_job = db.get(Job, latest_target_job)
    if not target_job or target_job.project_id != job.project_id or target_job.type != "target_discovery" or target_job.status != JobStatus.completed.value:
        raise OpenTargetsError("TARGET_JOB_INVALID", "指定的上游靶点分析不存在、尚未完成或不属于当前项目。")
    targets = db.scalars(
        select(TargetCandidate)
        .where(TargetCandidate.project_id == job.project_id, TargetCandidate.job_id == latest_target_job)
        .order_by(TargetCandidate.rank.asc().nullslast(), TargetCandidate.score.desc())
    ).all()
    if not targets:
        raise ValueError("TARGETS_REQUIRED")
    selected = [item for item in targets if item.selected]
    top_n = int(job.parameters.get("top_n", 50))
    # Do not silently truncate an explicit batch selection to the downstream
    # module's default. This keeps "select top 20/all" semantically exact.
    return selected if selected else targets[:top_n]


def _real_snapshot_context(db, job: Job, module: str):
    snapshot_path = get_settings().perturbation_knockout_snapshot_path
    if not snapshot_path:
        raise OpenTargetsError("PERTURBATION_KNOCKOUT_NOT_CONFIGURED", "虚拟干预与基因敲除真实数据快照尚未配置。")
    adapter = PerturbationKnockoutSnapshotAdapter(snapshot_path)
    targets = _latest_targets(db, job)
    if module == "gene_knockout":
        pooled_module = adapter.pooled_gene_knockout_module()
        provenance = adapter.pooled_gene_knockout_provenance()
        eligible_targets = [target for target in targets if target.data_mode == "real" and not target.demo]
        by_symbol = {
            str(item.get("symbol") or "").upper(): item
            for item in pooled_module["results"]
            if bool(item.get("screened"))
        }
        matched = [(target, by_symbol[target.symbol.upper()]) for target in eligible_targets if target.symbol.upper() in by_symbol]
        if not matched:
            raise OpenTargetsError(
                "PERTURBATION_KNOCKOUT_TARGETS_NOT_FOUND",
                "所选真实靶点尚未被 DepMap 24Q4 CRISPR 筛选覆盖。可返回靶点页改选候选后重试。",
            )
        return adapter, matched, pooled_module, provenance

    target_job_id = str(job.parameters.get("target_job_id") or targets[0].job_id)
    target_run = db.scalar(select(TargetDiscoveryRun).where(TargetDiscoveryRun.job_id == target_job_id))
    if not target_run:
        raise OpenTargetsError("TARGET_RUN_PROVENANCE_MISSING", "指定靶点结果缺少疾病来源，无法安全进入下游分析。")
    try:
        adapter.validate_disease(target_run.disease_id or target_run.canonical_name or target_run.query)
    except OpenTargetsError as exc:
        raise OpenTargetsError(
            "UPSTREAM_DISEASE_MISMATCH",
            f"当前靶点来自“{target_run.canonical_name or target_run.query}”，与虚拟干预/基因敲除 v1 的已验证疾病不一致。",
            {"target_job_id": target_job_id, "upstream_disease_id": target_run.disease_id},
        ) from exc
    by_symbol = {
        str(item.get("symbol") or "").upper(): item
        for item in adapter.module(module)["results"]
        if module != "gene_knockout" or bool(item.get("screened"))
    }
    matched = [(target, by_symbol[target.symbol.upper()]) for target in targets if target.symbol.upper() in by_symbol]
    if not matched:
        raise OpenTargetsError("PERTURBATION_KNOCKOUT_TARGETS_NOT_FOUND", "当前靶点与已验证快照没有可计算的重叠基因。")
    return adapter, matched, adapter.module(module), adapter.provenance(module)


def process_perturbation_real(db, job: Job):
    targets = _latest_targets(db, job)
    target_job_id = str(job.parameters.get("target_job_id") or targets[0].job_id)
    target_run = db.scalar(select(TargetDiscoveryRun).where(TargetDiscoveryRun.job_id == target_job_id))
    if not target_run:
        raise OpenTargetsError("TARGET_RUN_PROVENANCE_MISSING", "指定靶点结果缺少疾病来源，无法安全进入下游分析。")
    disease_name = target_run.canonical_name or target_run.query or "当前疾病"

    adapter = None
    module = None
    provenance = None
    by_symbol: dict[str, dict] = {}
    knockout_by_symbol: dict[str, dict] = {}
    snapshot_path = get_settings().perturbation_knockout_snapshot_path
    if snapshot_path:
        candidate_adapter = PerturbationKnockoutSnapshotAdapter(snapshot_path)
        knockout_by_symbol = {
            str(item.get("symbol") or "").upper(): item
            for item in candidate_adapter.pooled_gene_knockout_module()["results"]
            if bool(item.get("screened"))
        }
        try:
            candidate_adapter.validate_disease(target_run.disease_id or disease_name)
            module = candidate_adapter.module("virtual_intervention")
            provenance = candidate_adapter.provenance("virtual_intervention")
            by_symbol = {str(item.get("symbol") or "").upper(): item for item in module["results"]}
            adapter = candidate_adapter
            disease = candidate_adapter.payload.get("disease") or {}
            disease_name = disease.get("name_zh") or disease.get("name") or disease.get("id") or disease_name
        except OpenTargetsError:
            # Enhanced diseases can still use the transparent evidence baseline.
            # The missing disease-specific snapshot remains explicit per result.
            pass

    evidence_by_target: dict[str, list[TargetEvidence]] = {target.id: [] for target in targets}
    if evidence_by_target:
        for evidence in db.scalars(select(TargetEvidence).where(TargetEvidence.target_id.in_(list(evidence_by_target)))).all():
            evidence_by_target[evidence.target_id].append(evidence)

    live_direction_by_external: dict[str, list[dict]] = {}
    direction_lookup = {"status": "not_requested", "directional_target_count": 0}
    baseline_external_ids = [
        str(target.external_id)
        for target in targets
        if target.symbol.upper() not in by_symbol and target.data_mode == "real" and not target.demo and target.external_id
    ]
    if baseline_external_ids:
        direction_adapter = None
        try:
            direction_adapter = OpenTargetsAdapter(db)
            live_direction_by_external, direction_meta = direction_adapter.get_directional_evidence(
                target_run.disease_id or target_run.canonical_name or target_run.query,
                baseline_external_ids,
            )
            direction_lookup = {"status": "completed", **direction_meta}
        except (OpenTargetsError, AttributeError) as exc:
            # Directional enrichment improves the hypothesis but is never a
            # hard dependency.  The transparent product prior remains usable
            # during upstream API outages and in deterministic test adapters.
            direction_lookup = {"status": "fallback", "reason": getattr(exc, "code", type(exc).__name__), "directional_target_count": 0}
        finally:
            if direction_adapter is not None:
                direction_adapter.close()

    full_count = 0
    baseline_count = 0
    for index, target in enumerate(targets):
        item = by_symbol.get(target.symbol.upper())
        if item:
            full_count += 1
            direction = item.get("direction") or "unknown"
            direction_zh = {"inhibit": "抑制", "activate": "激活"}.get(direction, "方向待定")
            intervention = f"{direction_zh} {target.symbol}"
            cell_context = f"{disease_name} · {item.get('cell_type') or '供体级单细胞'}"
            reversal_score = item.get("virtual_intervention_score")
            confidence = item.get("confidence")
            result = {**item, "analysis_tier": "disease_specific", "metric_type": "disease_module_attenuation", "module": "virtual_intervention", "benchmark": module.get("benchmark") if module else None, "provenance": provenance}
        else:
            if target.data_mode != "real" or target.demo:
                continue
            baseline_count += 1
            result = build_intervention_baseline(
                target,
                disease_name,
                evidence_records=[
                    *(evidence_by_target.get(target.id) or []),
                    *(live_direction_by_external.get(str(target.external_id)) or []),
                ],
                knockout_profile=knockout_by_symbol.get(target.symbol.upper()),
            )
            result["direction_lookup"] = direction_lookup
            direction_zh = {"inhibit": "优先抑制", "activate": "优先激活"}.get(result["direction"], "方向待确认")
            intervention = f"{direction_zh} {target.symbol}"
            cell_context = f"{disease_name} · 公开证据基线"
            reversal_score = result["virtual_intervention_score"]
            confidence = result["confidence"]
        db.add(PerturbationResult(
            project_id=job.project_id,
            job_id=job.id,
            target_id=target.id,
            intervention=intervention,
            cell_context=cell_context,
            reversal_score=reversal_score,
            confidence=confidence,
            result=result,
            demo=False,
        ))
        record(db, job, JobStatus.running.value, "scoring_intervention_priority", min(90, 30 + (index + 1) * 60 // len(targets)), f"已计算 {target.symbol} 的干预验证优先级")
    if full_count + baseline_count == 0:
        raise OpenTargetsError("INTERVENTION_INPUT_NOT_ELIGIBLE", "当前靶点不是可追溯的真实数据结果，不能运行证据基线评估。")
    job.output_ref = f"projects/{job.project_id}/perturbations?module=virtual_intervention"
    return {
        "adapter": "PerturbationKnockoutSnapshotAdapter" if adapter and baseline_count == 0 else "TieredInterventionAdapter",
        "model_name": "疾病专属模块衰减 + 通用证据验证优先级",
        "model_version": module["model_version"] if module and baseline_count == 0 else INTERVENTION_BASELINE_VERSION,
        "provenance": {**(provenance or {"source": "upstream_target_snapshot", "model_version": INTERVENTION_BASELINE_VERSION}),
                       "analysis_tiers": {"disease_specific": full_count, "evidence_baseline": baseline_count},
                       "direction_lookup": direction_lookup},
        "demo": False,
    }


def process_gene_knockout_real(db, job: Job):
    _adapter, matched, module, provenance = _real_snapshot_context(db, job, "gene_knockout")
    for index, (target, item) in enumerate(matched):
        db.add(PerturbationResult(
            project_id=job.project_id,
            job_id=job.id,
            target_id=target.id,
            intervention=f"CRISPR 敲除 {target.symbol}",
            cell_context="DepMap 24Q4 · 跨疾病细胞系基因级覆盖",
            reversal_score=item.get("knockout_tolerability") if item.get("screened") else None,
            confidence=min(0.95, 0.55 + 0.40 * min(1.0, float(item.get("pan_model_count") or 0) / max(1.0, float(module.get("benchmark", {}).get("screened_models") or 1)))) if item.get("screened") else 0.0,
            result={**item, "module": "gene_knockout", "coverage_scope": "gene_level_global", "benchmark": module.get("benchmark"), "provenance": provenance},
            demo=False,
        ))
        record(db, job, JobStatus.running.value, "summarizing_crispr_gene_effect", min(90, 30 + (index + 1) * 60 // len(matched)), f"已汇总 {target.symbol} 的 CRISPR 敲除效应")
    job.output_ref = f"projects/{job.project_id}/perturbations?module=gene_knockout"
    return {
        "adapter": "PerturbationKnockoutSnapshotAdapter",
        "model_name": "DepMap Chronos 跨疾病基因级敲除适应度汇总",
        "model_version": module["model_version"],
        "provenance": provenance,
        "demo": False,
    }


def _validate_downstream_disease(db, job: Job, adapter, module_label: str):
    targets = _latest_targets(db, job)
    target_job_id = str(job.parameters.get("target_job_id") or targets[0].job_id)
    target_run = db.scalar(select(TargetDiscoveryRun).where(TargetDiscoveryRun.job_id == target_job_id))
    if not target_run:
        raise OpenTargetsError("TARGET_RUN_PROVENANCE_MISSING", "指定靶点结果缺少疾病来源，无法安全进入下游分析。")
    try:
        adapter.validate_disease(target_run.disease_id or target_run.canonical_name or target_run.query)
    except OpenTargetsError as exc:
        raise OpenTargetsError(
            "UPSTREAM_DISEASE_MISMATCH",
            f"当前靶点来自“{target_run.canonical_name or target_run.query}”，与{module_label} v1 的已验证疾病不一致。",
            {"target_job_id": target_job_id, "upstream_disease_id": target_run.disease_id},
        ) from exc
    return targets


def _latest_interventions_by_target(db, project_id: str) -> dict[str, PerturbationResult]:
    rows = db.scalars(
        select(PerturbationResult)
        .where(PerturbationResult.project_id == project_id)
        .order_by(PerturbationResult.created_at.desc())
    ).all()
    output: dict[str, PerturbationResult] = {}
    for row in rows:
        if row.target_id and (row.result or {}).get("module") == "virtual_intervention":
            output.setdefault(row.target_id, row)
    return output


def process_virtual_cell_real(db, job: Job):
    targets = _latest_targets(db, job)
    target_job_id = str(job.parameters.get("target_job_id") or targets[0].job_id)
    target_run = db.scalar(select(TargetDiscoveryRun).where(TargetDiscoveryRun.job_id == target_job_id))
    if not target_run:
        raise OpenTargetsError("TARGET_RUN_PROVENANCE_MISSING", "指定靶点结果缺少疾病来源，无法进入虚拟细胞分析。")
    disease_name = target_run.canonical_name or target_run.query or "当前疾病"
    eligible = [target for target in targets if target.data_mode == "real" and not target.demo]
    if not eligible:
        raise OpenTargetsError("VIRTUAL_CELL_INPUT_NOT_ELIGIBLE", "虚拟细胞分析需要可追溯的真实靶点结果。")

    adapter = None
    by_symbol: dict[str, dict] = {}
    disease_specific_provenance = None
    disease_specific_benchmark = None
    disease_specific_qc = None
    snapshot_path = get_settings().virtual_cell_snapshot_path
    if snapshot_path:
        candidate_adapter = VirtualCellSnapshotAdapter(snapshot_path)
        try:
            candidate_adapter.validate_disease(target_run.disease_id or disease_name)
            adapter = candidate_adapter
            by_symbol = {str(item.get("symbol") or "").upper(): item for item in adapter.results()}
            disease_specific_provenance = adapter.provenance()
            disease_specific_benchmark = adapter.payload.get("benchmark")
            disease_specific_qc = adapter.payload.get("qc")
            disease = adapter.payload.get("disease") or {}
            disease_name = disease.get("name_zh") or disease.get("name") or disease_name
        except OpenTargetsError:
            pass

    interventions = _latest_interventions_by_target(db, job.project_id)
    baseline_targets = [target for target in eligible if target.symbol.upper() not in by_symbol]
    baseline_rows, _baseline_benchmark, _baseline_qc, baseline_provenance = build_virtual_cell_baselines(
        baseline_targets, disease_name, interventions
    )
    baseline_by_symbol = {str(item.get("symbol") or "").upper(): item for item in baseline_rows}
    prior_jobs = select(Job.id).where(Job.project_id == job.project_id, Job.type == "virtual_cell")
    db.execute(delete(PerturbationResult).where(PerturbationResult.project_id == job.project_id, PerturbationResult.job_id.in_(prior_jobs)))
    full_count = 0
    baseline_count = 0
    for index, target in enumerate(eligible):
        item = by_symbol.get(target.symbol.upper())
        if item:
            full_count += 1
            result = {**item, "analysis_tier": "disease_specific", "module": "virtual_cell", "benchmark": disease_specific_benchmark, "qc": disease_specific_qc, "provenance": disease_specific_provenance}
            direction = {"inhibit": "抑制", "activate": "激活"}.get(item.get("direction"), "干预")
            cell_context = f"{disease_name} · {item.get('best_cell_type') or '供体级单细胞'}"
            reversal_score = item.get("projected_reversal")
            confidence = item.get("confidence")
        else:
            result = baseline_by_symbol.get(target.symbol.upper())
            if not result:
                continue
            baseline_count += 1
            direction = "抑制" if result.get("direction") == "inhibit" else "激活/恢复"
            cell_context = f"{disease_name} · 细胞情境优先级基线"
            reversal_score = result.get("context_priority_score")
            confidence = result.get("confidence")
        db.add(PerturbationResult(
            project_id=job.project_id,
            job_id=job.id,
            target_id=target.id,
            intervention=f"{direction} {target.symbol} 的细胞情境分析",
            cell_context=cell_context,
            reversal_score=reversal_score,
            confidence=confidence,
            result={**result, "module": "virtual_cell"},
            demo=False,
        ))
        record(db, job, JobStatus.running.value, "prioritizing_cell_context", min(90, 30 + (index + 1) * 60 // len(eligible)), f"已生成 {target.symbol} 的细胞情境验证建议")
    job.output_ref = f"projects/{job.project_id}/perturbations?module=virtual_cell"
    return {
        "adapter": "VirtualCellSnapshotAdapter" if full_count and not baseline_count else "TieredVirtualCellAdapter",
        "model_name": "疾病专属供体级投影 + 跨疾病细胞情境优先级基线",
        "model_version": adapter.payload["model_version"] if adapter and not baseline_count else VIRTUAL_CELL_BASELINE_VERSION,
        "provenance": {
            "analysis_tiers": {"disease_specific": full_count, "context_prior_baseline": baseline_count},
            "disease_specific": disease_specific_provenance,
            "context_prior": baseline_provenance,
        },
        "demo": False,
    }


def process_compound_matching_real(db, job: Job):
    snapshot_path = get_settings().compound_evaluation_snapshot_path
    adapter = CompoundEvaluationSnapshotAdapter(snapshot_path) if snapshot_path else None
    targets = [target for target in _latest_targets(db, job) if target.data_mode == "real" and not target.demo]
    if not targets:
        raise OpenTargetsError("COMPOUND_INPUT_NOT_ELIGIBLE", "化合物匹配需要可追溯的真实靶点结果。")
    requested = {target.symbol.upper() for target in targets}
    intervention_rows = _latest_interventions_by_target(db, job.project_id)
    directions = {
        target.symbol.upper(): str((intervention_rows.get(target.id).result if intervention_rows.get(target.id) else {}).get("direction") or target.direction or "unknown")
        for target in targets
    }
    matched = []
    snapshot_compounds = adapter.pooled_compounds() if adapter else []
    for compound in snapshot_compounds:
        target_matches = [item for item in compound.get("target_matches") or [] if str(item.get("target_symbol") or "").upper() in requested]
        if target_matches:
            target_matches = [dict(item) for item in target_matches]
            for item in target_matches:
                symbol = str(item.get("target_symbol") or "").upper()
                item["intervention_direction"] = directions.get(symbol, "unknown")
                item["direction_evidence"] = 0.5
                item["direction_note"] = "锁定快照中的体外活性可跨疾病复用；机制与本疾病干预方向需再次确认"
            value = {**compound, "target_matches": target_matches}
            value["match_score"] = max(float(item.get("match_score") or 0) for item in target_matches)
            value["evaluation_tier"] = value.get("evaluation_tier") or "locked_snapshot_full_property_baseline"
            matched.append(value)

    snapshot_symbols = {
        str(item.get("target_symbol") or "").upper()
        for compound in matched for item in compound.get("target_matches") or []
    }
    live_targets = [target for target in targets if target.symbol.upper() not in snapshot_symbols]
    live_adapter = ChEMBLRuntimeAdapter()
    try:
        record(db, job, JobStatus.running.value, "querying_live_chembl", 25, "正在检索 ChEMBL 人源单蛋白体外活性与结构记录")
        live = live_adapter.discover(live_targets, directions=directions, max_targets=20, compounds_per_target=4, max_compounds=100) if live_targets else {"compounds": [], "benchmark": {}, "provenance": {}}
    finally:
        live_adapter.close()
    by_id = {str(item.get("molecule_chembl_id")): item for item in matched}
    for item in live.get("compounds") or []:
        molecule_id = str(item.get("molecule_chembl_id") or "")
        if molecule_id not in by_id:
            by_id[molecule_id] = item
        else:
            existing = by_id[molecule_id]
            keys = {(m.get("target_symbol"), m.get("target_chembl_id")) for m in existing.get("target_matches") or []}
            existing["target_matches"].extend(m for m in item.get("target_matches") or [] if (m.get("target_symbol"), m.get("target_chembl_id")) not in keys)
            existing["match_score"] = max(float(existing.get("match_score") or 0), float(item.get("match_score") or 0))
    matched = list(by_id.values())
    matched.sort(key=lambda item: (-float(item.get("match_score") or 0), str(item.get("molecule_chembl_id"))))
    db.execute(delete(CompoundEvaluation).where(CompoundEvaluation.compound_id.in_(select(Compound.id).where(Compound.project_id == job.project_id))))
    db.execute(delete(Ranking).where(Ranking.project_id == job.project_id))
    db.execute(delete(Compound).where(Compound.project_id == job.project_id))
    snapshot_provenance = adapter.pooled_provenance() if adapter else None
    live_benchmark = live.get("benchmark") or {}
    actual_target_symbols = {str(match.get("target_symbol") or "").upper() for item in matched for match in item.get("target_matches") or []}
    benchmark = {
        "version": "cross-disease-compound-matching-v2",
        "requested_target_count": len(targets),
        "chembl_target_count": int(live_benchmark.get("chembl_target_count") or 0) + len(snapshot_symbols),
        "chembl_mapped_targets": int(live_benchmark.get("chembl_mapped_targets") or 0) + len(snapshot_symbols),
        "chembl_activity_targets": len(actual_target_symbols),
        "qualified_compounds": len(matched),
        "snapshot_compound_count": sum(1 for value in matched if value.get("evaluation_tier") == "locked_snapshot_full_property_baseline"),
        "live_compound_count": sum(1 for value in matched if value.get("evaluation_tier") == "experimental_activity_plus_chembl_properties"),
        "full_admet_compound_count": sum(1 for value in matched if bool(value.get("admet_predictions"))),
        "target_registry": live_benchmark.get("target_registry") or [],
        "quality_gates": {"strict_human_single_protein": True, "passed": bool(matched)},
    }
    if adapter and not adapter.adapters:
        benchmark["admet"] = (adapter.payload.get("benchmark") or {}).get("admet")
    provenance = {
        "source": "ChEMBL 锁定快照 + 有边界的 ChEMBL 在线检索",
        "snapshot_id": "cross-disease-compound-v2",
        "model_version": "cross-disease-compound-matching-v2",
        "scientific_status": "experimental_activity_and_property_triage",
        "coverage_scope": "gene_and_molecule_level_global",
        "locked_snapshot": snapshot_provenance,
        "live_lookup": live.get("provenance"),
        "benchmark": benchmark,
        "data_mode": "real",
        "scientific_conclusion": False,
    }
    for index, item in enumerate(matched[:200]):
        best = max(item["target_matches"], key=lambda value: float(value.get("match_score") or 0))
        mechanism = best.get("mechanism") or f"{best.get('target_symbol')} 人源单蛋白结合实验；作用方向尚无机制记录"
        db.add(Compound(
            project_id=job.project_id,
            job_id=job.id,
            name=item.get("name") or item["molecule_chembl_id"],
            identifier=item["molecule_chembl_id"],
            mechanism=mechanism,
            match_score=float(item["match_score"]),
            details={**item, "provenance": provenance},
            demo=False,
        ))
        record(db, job, JobStatus.running.value, "matching_chembl_activities", min(90, 30 + (index + 1) * 60 // min(200, len(matched))), f"已匹配 {item.get('name') or item['molecule_chembl_id']}")
    job.output_ref = f"projects/{job.project_id}/compounds"
    return {"adapter": "TieredChEMBLCompoundAdapter", "model_name": "跨疾病 ChEMBL 人源单蛋白活性与结构匹配", "model_version": "cross-disease-compound-matching-v2", "provenance": provenance, "demo": False}


def process_admet_real(db, job: Job):
    compounds = db.scalars(select(Compound).where(Compound.project_id == job.project_id, Compound.demo.is_(False)).order_by(Compound.match_score.desc())).all()
    if not compounds:
        raise ValueError("COMPOUNDS_REQUIRED")
    db.execute(delete(CompoundEvaluation).where(CompoundEvaluation.compound_id.in_([item.id for item in compounds])))
    provenance = (compounds[0].details or {}).get("provenance") or {}
    for index, compound in enumerate(compounds):
        details = compound.details or {}
        db.add(CompoundEvaluation(
            compound_id=compound.id,
            job_id=job.id,
            efficacy=details.get("efficacy_score"),
            safety=details.get("safety_score"),
            developability=details.get("developability_score"),
            admet={
                "status": "predicted_baseline" if details.get("admet_predictions") else "structure_and_clinical_metadata_baseline",
                "experimental_activity": details.get("target_matches"),
                "descriptors": details.get("descriptors"),
                "predictions": details.get("admet_predictions"),
                "applicability": details.get("admet_applicability"),
                "clinical_metadata": {key: details.get(key) for key in ("max_phase", "first_approval", "oral", "black_box_warning", "withdrawn_flag")},
                "provenance": provenance,
                "not_for_clinical_decision_making": True,
            },
            demo=False,
        ))
        record(db, job, JobStatus.running.value, "evaluating_admet_snapshot", min(90, 30 + (index + 1) * 60 // len(compounds)), f"已评价 {compound.name}")
    job.output_ref = f"projects/{job.project_id}/compounds"
    return {"adapter": "CompoundEvaluationSnapshotAdapter", "model_name": "TDC scaffold 评测 ADMET 基线", "model_version": provenance.get("model_version") or "chembl-rdkit-tdc-rf-v1", "provenance": provenance, "demo": False}


def process_compound_matching(db, job: Job):
    db.execute(delete(CompoundEvaluation).where(CompoundEvaluation.compound_id.in_(select(Compound.id).where(Compound.project_id == job.project_id))))
    db.execute(delete(Ranking).where(Ranking.project_id == job.project_id))
    db.execute(delete(Compound).where(Compound.project_id == job.project_id))
    for index, item in enumerate(COMPOUNDS):
        db.add(Compound(project_id=job.project_id, job_id=job.id, **item))
        record(db, job, JobStatus.running.value, "matching_compounds", 30 + (index + 1) * 18, f"Matched {item['name']}")
    job.output_ref = f"projects/{job.project_id}/compounds"


def process_admet(db, job: Job):
    compounds = db.scalars(select(Compound).where(Compound.project_id == job.project_id).order_by(Compound.match_score.desc())).all()
    if not compounds:
        raise ValueError("COMPOUNDS_REQUIRED")
    db.execute(delete(CompoundEvaluation).where(CompoundEvaluation.compound_id.in_([item.id for item in compounds])))
    for index, compound in enumerate(compounds):
        base = compound.match_score
        db.add(CompoundEvaluation(
            compound_id=compound.id,
            job_id=job.id,
            efficacy=round(base, 3),
            safety=round(max(0.35, base - 0.12 + index * 0.03), 3),
            developability=round(max(0.4, base - 0.08), 3),
            admet={"status": "illustrative", "source": PROVENANCE["label"], "not_for_decision_making": True},
        ))
        record(db, job, JobStatus.running.value, "evaluating_admet", 30 + (index + 1) * 18, f"Evaluated {compound.name}")
    job.output_ref = f"projects/{job.project_id}/compounds"


def process_ranking(db, job: Job):
    compounds = db.scalars(select(Compound).where(Compound.project_id == job.project_id)).all()
    evaluations = {item.compound_id: item for item in db.scalars(select(CompoundEvaluation).join(Compound).where(Compound.project_id == job.project_id)).all()}
    if not compounds or not evaluations:
        raise ValueError("EVALUATIONS_REQUIRED")
    weights = normalize_weights(job.parameters.get("weights"))
    scored = []
    for compound in compounds:
        evaluation = evaluations[compound.id]
        total = weighted_score({
            "compound_match": compound.match_score,
            "efficacy": evaluation.efficacy,
            "safety": evaluation.safety,
            "developability": evaluation.developability,
        }, weights)
        scored.append((compound, total))
    db.execute(delete(Ranking).where(Ranking.project_id == job.project_id))
    for index, (compound, total) in enumerate(sorted(scored, key=lambda item: item[1], reverse=True), start=1):
        db.add(Ranking(
            project_id=job.project_id,
            job_id=job.id,
            compound_id=compound.id,
            rank=index,
            total_score=total,
            weights=weights,
            rationale="透明加权排序；综合分是项目内决策辅助值，不是研发成功概率。",
        ))
        record(db, job, JobStatus.running.value, "ranking_candidates", 30 + index * 18, f"Ranked {compound.name}")
    job.output_ref = f"projects/{job.project_id}/candidates"
    data_mode = "real" if compounds and all(not item.demo for item in compounds) else "precomputed"
    if job.adapter_mode in {"real", "runtime"} and data_mode != "real":
        raise ValueError("RANKING_REAL_INPUTS_REQUIRED")
    return {
        "adapter": "RankingAdapter",
        "model_name": "四维透明加权候选排序",
        "model_version": "integrated-ranking-v1",
        "provenance": {
            "data_mode": data_mode,
            "weights": weights,
            "equation": "compound_match*w1 + efficacy*w2 + safety*w3 + developability*w4",
            "scientific_conclusion": False,
        },
        "demo": data_mode != "real",
    }


PROCESSORS = {
    "target_discovery": ("TargetDiscoveryAdapter", process_target_discovery),
    "perturbation": ("PerturbationAdapter", process_perturbation),
    "gene_knockout": ("GeneKnockoutAdapter", process_gene_knockout_real),
    "virtual_cell": ("VirtualCellAdapter", process_virtual_cell_real),
    "compound_matching": ("CompoundMatchingAdapter", process_compound_matching),
    "admet": ("ADMETAdapter", process_admet),
    "ranking": ("RankingAdapter", process_ranking),
    "omics_analysis": ("UploadedOmicsMatrixAdapter", process_omics_analysis),
    "fungal_target": ("UploadedFungalTargetEvidenceAdapter", process_fungal_target),
    "fungal_compound": ("FungalCompoundExplorationAdapter", process_fungal_compound),
    "bacterial_compound": ("BacterialCompoundExplorationAdapter", process_bacterial_compound),
}


_LOCAL_JOB_LOCK = threading.Lock()


def _execute_job_unlocked(job_id: str):
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job or job.status == JobStatus.cancelled.value:
            return
        job.started_at = now()
        record(db, job, JobStatus.running.value, "initializing", 10, "Task started")
        adapter_name, processor = PROCESSORS[job.type]
        run_metadata: dict
        if job.adapter_mode == "not_configured":
            if job.type == "target_discovery":
                process_target_discovery_not_configured(db, job)
            job.result_status = "no_data"
            job.output_ref = None
            run_metadata = {
                "adapter": adapter_name,
                "model_name": "not configured",
                "model_version": "not_configured",
                "provenance": {"data_mode": "not_configured"},
                "demo": False,
            }
        elif job.adapter_mode == "precomputed":
            result = processor(db, job)
            run_metadata = result or {
                "adapter": adapter_name,
                "model_name": "IPF workflow demonstration adapter",
                "model_version": PROVENANCE["version"],
                "provenance": PROVENANCE,
                "demo": True,
            }
            job.result_status = "demonstration"
        elif job.adapter_mode in {"real", "runtime"} and job.type in {"target_discovery", "perturbation", "gene_knockout", "virtual_cell", "compound_matching", "admet", "ranking", "omics_analysis", "fungal_target", "fungal_compound", "bacterial_compound"}:
            real_processors = {
                "target_discovery": process_target_discovery_real,
                "perturbation": process_perturbation_real,
                "gene_knockout": process_gene_knockout_real,
                "virtual_cell": process_virtual_cell_real,
                "compound_matching": process_compound_matching_real,
                "admet": process_admet_real,
                "ranking": process_ranking,
                "omics_analysis": process_omics_analysis,
                "fungal_target": process_fungal_target,
                "fungal_compound": process_fungal_compound,
                "bacterial_compound": process_bacterial_compound,
            }
            run_metadata = real_processors[job.type](db, job)
            job.result_status = "real"
        else:
            raise ValueError("RUNTIME_ADAPTER_NOT_CONFIGURED")
        model_run(db, job, **run_metadata)
        job.completed_at = now()
        record(db, job, JobStatus.completed.value, "completed", 100, "Task completed")
    except Exception as exc:
        db.rollback()
        job = db.get(Job, job_id)
        if job:
            if isinstance(exc, OpenTargetsError):
                job.error_code = exc.code[:80]
                job.error_message = exc.message
            elif isinstance(exc, OmicsAnalysisError):
                job.error_code = exc.code[:80]
                job.error_message = exc.message
            elif isinstance(exc, (FungalTargetError, FungalCompoundError, BacterialCompoundError)):
                job.error_code = exc.code[:80]
                job.error_message = exc.message
            else:
                job.error_code = str(exc)[:80]
                job.error_message = "The configured adapter could not complete this task."
            job.completed_at = now()
            record(db, job, JobStatus.failed.value, "failed", job.progress, job.error_message)
    finally:
        db.close()


def execute_job(job_id: str):
    """Run one in-process analysis at a time to stay inside the API memory budget."""
    with _LOCAL_JOB_LOCK:
        return _execute_job_unlocked(job_id)
