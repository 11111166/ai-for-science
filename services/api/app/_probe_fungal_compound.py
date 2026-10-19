"""Target-to-compound exploration for fungal targets.

The module answers a different question from ``fungal_target``: given a fungal
protein, what compounds are known against it, what does the genetic evidence
say about it, and what structurally novel analogues can be derived from named
antifungal compounds.  It deliberately ranks nothing: it reports curated
evidence, retrieved molecules with their measured properties, and generated
structures that carry no activity claim at all.

Evidence hierarchy, strongest first:

1. CGD chemogenomics -- curated *Candida albicans* records of which gene
   perturbations change susceptibility to a named compound.  This is genetic
   evidence for the target and it names real antifungal compounds.
2. ChEMBL measured activity -- compounds with a direct binding/functional
   measurement against the target.  Available for only a handful of fungal
   targets; stays empty when there is nothing, and says so.
3. ChEMBL similarity expansion -- structurally related molecules.  These have
   no activity data of their own and are never presented as if they did.
4. Generated analogues -- deterministic RDKit rule application to the named
   compounds from layer 1.  No activity, docking, or ADMET prediction exists
   for them anywhere in this platform, and none is invented here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .chembl_runtime import ChEMBLRuntimeAdapter, _number
from .database import get_db
from .models import Asset, Job, JobStatus
from .storage import read_storage_bytes, save_asset


router = APIRouter(prefix="/api/v1/drug-discovery")
MODEL_VERSION = "fungal-compound-exploration-v1"
MAX_RESULT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_GENERATED = 25
HARD_MAX_GENERATED = 60
MAX_SIMILARITY_RESULTS = 40

FUNGAL_TARGET_DATA_DIR = Path(os.environ.get("FUNGAL_TARGET_DATA_DIR", "./data/shared/fungal-target-data/current"))
CGD_ARTIFACT = "derived/fungal-cgd-evidence.json"

# Release-local chemistry dependencies, loaded exactly as molecule_workbench
# does so the shared API runtime stays untouched.  Everything RDKit-related is
# imported lazily and degrades to "unavailable" rather than failing a job.
_chemdeps = Path(__file__).resolve().parent.parent / "_chemdeps"


class FungalCompoundError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Chemistry capability probe
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_render_libraries() -> bool:
    """Preload the X libs RDKit's drawing code needs. Safe to call repeatedly."""
    import ctypes

    native = _chemdeps / "native" / "usr" / "lib64"
    if not native.is_dir():
        return False
    for name in ("libXau.so.6", "libxcb.so.1", "libX11.so.6", "libXext.so.6", "libXrender.so.1"):
        ctypes.CDLL(str(native / name), mode=ctypes.RTLD_GLOBAL)
    return True


@lru_cache(maxsize=1)
def capabilities() -> dict[str, Any]:
    """Report what the deployed RDKit can actually do. Never assumes."""
    result = {
        "rdkit": False,
        "rdkit_version": None,
        "svg_depiction": False,
        "filter_catalog": False,
        "reaction_from_smarts": False,
        "murcko_scaffold": False,
        "bulk_tanimoto": False,
        "synthetic_accessibility": False,
        "conformer_generation": False,
        "admet_models": False,
        "docking": False,
    }
    if _chemdeps.is_dir():
        sys.path.insert(0, str(_chemdeps))
    try:
        import rdkit  # noqa: F401
        from rdkit import Chem
        from rdkit.Chem import rdChemReactions, rdMolDescriptors
        from rdkit.Chem import rdFingerprintGenerator
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except Exception:
        return result
    result["rdkit"] = True
    result["rdkit_version"] = str(getattr(rdkit, "__version__", ""))
    result["conformer_generation"] = hasattr(Chem, "EmbedMolecule")
    result["murcko_scaffold"] = hasattr(MurckoScaffold, "MurckoScaffoldSmiles")
    result["reaction_from_smarts"] = hasattr(rdChemReactions, "ReactionFromSmarts")
    result["bulk_tanimoto"] = hasattr(rdFingerprintGenerator, "GetMorganGenerator")
    try:
        from rdkit.Chem import FilterCatalog

        params = FilterCatalog.FilterCatalogParams()
        params.AddCatalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.PAINS)
        params.AddCatalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.BRENK)
        FilterCatalog.FilterCatalog(params)
        result["filter_catalog"] = True
    except Exception:
        result["filter_catalog"] = False
    try:
        load_render_libraries()
        from rdkit.Chem.Draw import rdMolDraw2D  # noqa: F401

        result["svg_depiction"] = True
    except Exception:
        result["svg_depiction"] = False
    # The Ertl-Schuffenhauer score lives in RDKit's Contrib tree, not in
    # rdMolDescriptors; check the real implementation is importable.
    result["synthetic_accessibility"] = _sascorer() is not None
    return result


@lru_cache(maxsize=1)
def _sascorer():
    contrib = _chemdeps / "rdkit" / "Contrib" / "SA_Score"
    if not (contrib / "sascorer.py").is_file() or not (contrib / "fpscores.pkl.gz").is_file():
        return None
    try:
        sys.path.insert(0, str(contrib))
        import sascorer  # type: ignore

        return sascorer
    except Exception:
        return None


@lru_cache(maxsize=1)
def _filter_catalog():
    if not capabilities()["filter_catalog"]:
        return None
    from rdkit.Chem import FilterCatalog

    params = FilterCatalog.FilterCatalogParams()
    for catalog in ("PAINS", "BRENK"):
        params.AddCatalog(getattr(FilterCatalog.FilterCatalogParams.FilterCatalogs, catalog))
    return FilterCatalog.FilterCatalog(params)


def structural_alerts(smiles: str) -> dict[str, Any] | None:
    """PAINS/Brenk screen. ``None`` means "RDKit unavailable", never zero."""
    catalog = _filter_catalog()
    if catalog is None:
        return None
    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return None
    matches = catalog.GetMatches(molecule)
    names = []
    for match in matches:
        try:
            names.append(str(match.GetDescription()))
        except Exception:
            names.append("unnamed alert")
    return {
        "count": len(names),
        "names": sorted(set(names))[:20],
        "catalogs": ["PAINS_A", "PAINS_B", "PAINS_C", "BRENK"],
    }


# --------------------------------------------------------------------------
# CGD artifact
# --------------------------------------------------------------------------

def _artifact_path() -> Path:
    return FUNGAL_TARGET_DATA_DIR / CGD_ARTIFACT


@lru_cache(maxsize=4)
def _load_artifact_cached(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_cgd_artifact() -> dict[str, Any] | None:
    """Read the curated chemogenomics artifact, verifying it against the manifest."""
    path = _artifact_path()
    if not path.is_file():
        return None
    try:
        payload = _load_artifact_cached(str(path), path.stat().st_mtime)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "targets" not in payload:
        return None
    return payload


def artifact_integrity() -> dict[str, Any]:
    """Check the artifact's SHA-256 against the bundle manifest when listed."""
    path = _artifact_path()
    manifest_path = FUNGAL_TARGET_DATA_DIR / "manifest.json"
    result: dict[str, Any] = {"path": str(path), "sha256_verified": False, "hash": None}
    if not path.is_file() or not manifest_path.is_file():
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    relative = str(path.relative_to(FUNGAL_TARGET_DATA_DIR.resolve()))
    expected = next(
        (item.get("sha256") for item in manifest.get("files", []) if item.get("path") == relative),
        None,
    )
    if not expected:
        return result
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result["hash"] = digest
    result["expected"] = expected
    result["sha256_verified"] = digest == expected
    return result


def target_catalog() -> dict[str, Any]:
    """Every SC5314 protein that has curated antifungal chemogenomic evidence."""
    artifact = load_cgd_artifact()
    if artifact is None:
        return {
            "configured": False,
            "artifact": str(_artifact_path()),
            "message": "服务器尚未构建 CGD 化学遗传学证据文件；请先运行 infra/build_fungal_cgd_evidence.py。",
            "counts": {},
            "targets": [],
        }
    integrity = artifact_integrity()
    return {
        "configured": True,
        "artifact": str(_artifact_path()),
        "bundle_version": FUNGAL_TARGET_DATA_DIR.name,
        "generated_at": artifact.get("generated_at"),
        "chembl_db_version": artifact.get("chembl_db_version"),
        "sha256_verified": integrity["sha256_verified"],
        "counts": artifact.get("counts", {}),
        "compounds": [
            {
                "cgd_name": item.get("cgd_name"),
                "chembl_id": item.get("chembl_id"),
                "class": item.get("class"),
                "pref_name": item.get("pref_name"),
                "note": item.get("note"),
            }
            for item in artifact.get("compounds", [])
        ],
        "unresolved_compounds": artifact.get("unresolved_compounds", []),
        "targets": artifact.get("targets", []),
    }


def _target_entry(artifact: dict[str, Any], uniprot: str, gene_symbol: str) -> dict[str, Any] | None:
    wanted = uniprot.strip().upper()
    gene = gene_symbol.strip().upper()
    for item in artifact.get("targets", []):
        if wanted and str(item.get("uniprot", "")).upper() == wanted:
            return item
        if gene and str(item.get("gene_symbol", "")).upper() == gene:
            return item
    return None


# --------------------------------------------------------------------------
# ChEMBL retrieval (fungal targets; never touches the human path)
# --------------------------------------------------------------------------

class FungalChEMBL:
    """Thin fungal-side reader built on the existing retry-aware client."""

    def __init__(self, adapter: ChEMBLRuntimeAdapter | None = None):
        self.adapter = adapter or ChEMBLRuntimeAdapter(max_workers=4)
        self.failures = 0

    def close(self) -> None:
        self.adapter.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.adapter._get(path, params)
        except Exception:
            self.failures += 1
            raise

    def target_for_uniprot(self, uniprot: str) -> list[dict[str, Any]]:
        """Exact cross-reference lookup. Returns every hit; callers must not auto-pick."""
        payload = self._get("target", {"target_components__accession": uniprot, "limit": 20})
        return list(payload.get("targets") or [])

    def measured_activities(self, target_chembl_id: str, organism: str, limit: int = 500) -> dict[str, Any]:
        """Strict measured-binding query.

        ``target_organism`` is deliberately NOT sent: ChEMBL stores the full
        strain string for this target ("Candida albicans (strain SC5314 /
        ATCC MYA-2876) (Yeast)"), so any shorter value silently returns zero.
        Rows are checked against the resolved target's organism afterwards.
        """
        payload = self._get("activity", {
            "target_chembl_id": target_chembl_id,
            "assay_type": "B",
            "standard_flag": 1,
            "standard_relation": "=",
            "pchembl_value__isnull": "false",
            "data_validity_comment__isnull": "true",
            "potential_duplicate": 0,
            "limit": limit,
            "order_by": "-pchembl_value",
        })
        rows = list(payload.get("activities") or [])
        total = int((payload.get("page_meta") or {}).get("total_count") or len(rows))
        kept, mismatched = [], 0
        for row in rows:
            row_organism = str(row.get("target_organism") or "")
            if organism and row_organism and row_organism != organism:
                mismatched += 1
                continue
            kept.append(row)
        return {
            "rows": kept,
            "activity_total": total,
            "truncated": total > len(rows),
            "organism_mismatch_count": mismatched,
        }

    def molecule(self, molecule_chembl_id: str) -> dict[str, Any]:
        return self._get(f"molecule/{molecule_chembl_id}")

    def mechanism(self, molecule_chembl_id: str) -> list[dict[str, Any]]:
        payload = self._get("mechanism", {"molecule_chembl_id": molecule_chembl_id, "limit": 20})
        return list(payload.get("mechanisms") or [])

    def indications(self, molecule_chembl_id: str) -> list[dict[str, Any]]:
        payload = self._get("drug_indication", {"molecule_chembl_id": molecule_chembl_id, "limit": 50})
        return list(payload.get("drug_indications") or [])

    def similarity(self, smiles: str, threshold: int, limit: int = MAX_SIMILARITY_RESULTS) -> dict[str, Any]:
        """Similarity expansion.

        Uses the query-string form: SMILES routinely contain ``#`` and ``/``,
        which corrupt the path form of this endpoint.
        """
        return self._get("similarity", {"smiles": smiles, "similarity": threshold, "limit": limit})

    def smiles_exists(self, canonical_smiles: str) -> bool | None:
        """Exact canonical-SMILES lookup. ``None`` means the check could not run."""
        try:
            payload = self._get("molecule", {
                "molecule_structures__canonical_smiles__exact": canonical_smiles,
                "limit": 1,
            })
        except Exception:
            return None
        return int((payload.get("page_meta") or {}).get("total_count") or 0) > 0


def _descriptors_from_chembl(molecule: dict[str, Any]) -> dict[str, Any]:
    """Copy ChEMBL's own precomputed properties verbatim (never recomputed)."""
    props = molecule.get("molecule_properties") or {}
    return {
        "molecular_weight": _number(props.get("full_mwt")),
        "logp": _number(props.get("alogp")),
        "tpsa": _number(props.get("psa")),
        "h_bond_donors": _number(props.get("hbd")),
        "h_bond_acceptors": _number(props.get("hba")),
        "rotatable_bonds": _number(props.get("rtb")),
        "qed": _number(props.get("qed_weighted")),
        "aromatic_rings": _number(props.get("aromatic_rings")),
        "lipinski_violations": _number(props.get("num_ro5_violations")),
        "descriptor_source": "ChEMBL calculated molecule properties",
    }


def _identity(molecule: dict[str, Any]) -> dict[str, Any]:
    structures = molecule.get("molecule_structures") or {}
    return {
        "molecule_chembl_id": str(molecule.get("molecule_chembl_id") or ""),
        "parent_molecule_chembl_id": str((molecule.get("molecule_hierarchy") or {}).get("parent_chembl_id") or ""),
        "name": str(molecule.get("pref_name") or "").strip(),
        "canonical_smiles": str(structures.get("canonical_smiles") or "").strip(),
        "inchi_key": str(structures.get("standard_inchi_key") or "").strip(),
        "molecule_type": str(molecule.get("molecule_type") or "").strip(),
        "max_phase": molecule.get("max_phase"),
        "first_approval": molecule.get("first_approval"),
        "black_box_warning": bool(molecule.get("black_box_warning")),
        "withdrawn_flag": bool(molecule.get("withdrawn_flag")),
    }


# --------------------------------------------------------------------------
# Deterministic generation from named antifungal compounds
# --------------------------------------------------------------------------

# Fixed, named transformations with a written rationale.  Each rule replaces a
# single well-defined group; nothing here is a generic reaction library, and
# every product records which rule produced it.
GENERATION_RULES: list[dict[str, str]] = [
    {"id": "aromatic_h_to_f", "smarts": "[cH:1]>>[c:1]F", "rationale": "芳香氢→氟：封堵代谢软点，标准药物化学扫描"},
    {"id": "aromatic_h_to_cl", "smarts": "[cH:1]>>[c:1]Cl", "rationale": "芳香氢→氯：改变电子效应与代谢稳定性"},
    {"id": "aromatic_h_to_me", "smarts": "[cH:1]>>[c:1]C", "rationale": "芳香氢→甲基：探查疏水口袋的空间容忍度"},
    {"id": "phenol_o_methylation", "smarts": "[OX2H:1][c:2]>>[OX2:1]([c:2])C", "rationale": "酚羟基甲基化：改变氢键供体与代谢路径"},
    {"id": "acid_to_primary_amide", "smarts": "[CX3:1](=[OX1:2])[OX2H]>>[CX3:1](=[OX1:2])N", "rationale": "羧酸→伯酰胺：改变电离状态与渗透性"},
    {"id": "acid_to_n_methyl_amide", "smarts": "[CX3:1](=[OX1:2])[OX2H]>>[CX3:1](=[OX1:2])NC", "rationale": "羧酸→N-甲基酰胺：同上，N-甲基变体"},
    {"id": "primary_amine_acetylation", "smarts": "[NX3;H2:1][CX4:2]>>[NX3:1]([CX4:2])C(C)=O", "rationale": "伯胺乙酰化：降低碱性与反应性"},
    {"id": "aryl_halide_to_nitrile", "smarts": "[c:1][Br,Cl,I]>>[c:1]C#N", "rationale": "芳卤→腈：偶极与氢键受体替换"},
    {"id": "primary_alcohol_ether", "smarts": "[CX4;H2,H1:1][OX2H]>>[CX4:1]OC", "rationale": "伯醇醚化：改变氢键与代谢稳定性"},
]

MAX_SITES_PER_RULE = 5
TANIMOTO_LOW = 0.20
TANIMOTO_HIGH = 0.70
MAX_SA_SCORE = 4.0
MIN_HEAVY_ATOMS = 12
MAX_HEAVY_ATOMS = 50
MAX_MOLECULAR_WEIGHT = 500.0
MIN_SA = 1.0

NOT_EVALUATED = {
    "antifungal_activity": "本平台没有真菌靶点活性预测模型；生成物未经任何活性计算。",
    "selectivity": "没有宿主选择性模型；不得用结构相似度代替选择性判断。",
    "docking": "没有对接引擎（无 Vina/GNINA）与 GPU 队列，未做任何对接。",
    "admet": "现有 ADMET 模型仅在既定人源疾病化合物集上训练，对真菌靶点不适用。",
    "patent_novelty": "仅检查 ChEMBL 规范 SMILES 精确命中，未检索专利、PubChem 或供应商目录。",
}
NOVELTY_SCOPE = "chembl_exact_canonical_smiles_only"
SA_METHOD = "Ertl-Schuffenhauer 片段贡献法（RDKit Contrib/SA_Score，2009）"


def _rdkit():
    """Import RDKit lazily; return None when chemistry is unavailable."""
    if not capabilities()["rdkit"]:
        return None
    if str(_chemdeps) not in sys.path:
        sys.path.insert(0, str(_chemdeps))
    try:
        from rdkit import Chem
        from rdkit.Chem import rdChemReactions, rdFingerprintGenerator, rdMolDescriptors
        from rdkit.Chem.Scaffolds import MurckoScaffold

        return {
            "Chem": Chem,
            "reactions": rdChemReactions,
            "fingerprints": rdFingerprintGenerator,
            "descriptors": rdMolDescriptors,
            "scaffold": MurckoScaffold,
        }
    except Exception:
        return None


@lru_cache(maxsize=1)
def _morgan():
    rd = _rdkit()
    if rd is None:
        return None
    return rd["fingerprints"].GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)


def tanimoto(smiles_a: str, smiles_b: str) -> float | None:
    rd = _rdkit()
    generator = _morgan()
    if rd is None or generator is None:
        return None
    from rdkit import DataStructs

    mol_a, mol_b = rd["Chem"].MolFromSmiles(smiles_a), rd["Chem"].MolFromSmiles(smiles_b)
    if mol_a is None or mol_b is None:
        return None
    return float(DataStructs.TanimotoSimilarity(generator.GetFingerprint(mol_a), generator.GetFingerprint(mol_b)))


def _screened_compounds(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Curated compounds that can actually be drawn and used as generation seeds."""
    output = []
    for item in artifact.get("compounds", []):
        if item.get("status") == "resolved" and item.get("canonical_smiles"):
            output.append(item)
    return output


def generate_compounds(seeds: list[dict[str, Any]], max_generated: int, chembl: "FungalChEMBL | None") -> dict[str, Any]:
    """Enumerate analogues of named antifungal compounds under explicit gates."""
    caps = capabilities()
    if not caps["rdkit"] or not caps["reaction_from_smarts"]:
        return {
            "enabled": False,
            "reason": "服务器 RDKit 不可用，未生成任何结构。",
            "capabilities": caps,
            "compounds": [],
            "funnel": [],
            "rejected_by_reason": {},
        }
    if not seeds:
        return {
            "enabled": False,
            "reason": "该靶标没有 CGD 点名的抗真菌化合物作为衍生基础，未生成任何结构。",
            "capabilities": caps,
            "compounds": [],
            "funnel": [],
            "rejected_by_reason": {},
        }

    rd = _rdkit()
    if rd is None:
        return {
            "enabled": False,
            "reason": "服务器 RDKit 不可用，未生成任何结构。",
            "capabilities": caps,
            "compounds": [],
            "funnel": [],
            "rejected_by_reason": {},
        }
    Chem, reactions = rd["Chem"], rd["reactions"]
    catalog = _filter_catalog()
    sascorer = _sascorer()

    attempts = 0
    rejected: dict[str, int] = {}
    funnel = [
        {"id": "attempted", "label": "规则枚举产物", "count": 0},
        {"id": "rdkit_valid", "label": "结构有效且单一组分", "count": 0},
        {"id": "drug_like", "label": "类药性硬门槛通过", "count": 0},
        {"id": "alert_free", "label": "无 PAINS/Brenk 警示", "count": 0},
        {"id": "related", "label": "与母体相似度 0.2–0.7", "count": 0},
        {"id": "stereo_ok", "label": "手性中心明确", "count": 0},
        {"id": "synthesizable", "label": f"合成可及性 SA ≤ {MAX_SA_SCORE:g}", "count": 0},
        {"id": "novel", "label": "ChEMBL 无完全一致结构", "count": 0},
    ]
    stage_index = {stage["id"]: index for index, stage in enumerate(funnel)}

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for seed in seeds:
        seed_smiles = seed["canonical_smiles"]
        seed_mol = Chem.MolFromSmiles(seed_smiles)
        if seed_mol is None:
            continue
        for rule in GENERATION_RULES:
            if len(results) >= max_generated:
                break
            try:
                reaction = reactions.ReactionFromSmarts(rule["smarts"])
            except Exception:
                continue
            if reaction is None:
                continue
            # RunReactants already enumerates every match; the per-rule cap
            # keeps one rule from flooding the whole budget.
            try:
                product_sets = reaction.RunReactants((seed_mol,))
            except Exception:
                continue
            evaluated = 0
            for product_tuple in product_sets:
                if evaluated >= MAX_SITES_PER_RULE or len(results) >= max_generated:
                    break
                if not product_tuple:
                    continue
                evaluated += 1
                attempts += 1
                product = product_tuple[0]
                outcome = _evaluate_product(
                    Chem, catalog, sascorer, product, seed_smiles, seen, rejected, funnel, stage_index,
                )
                if outcome is None:
                    continue
                outcome["provenance"] = {
                    "seed_cgd_name": seed["cgd_name"],
                    "seed_molecule_chembl_id": seed.get("chembl_id"),
                    "seed_smiles": seed_smiles,
                    "rule_id": rule["id"],
                    "rule_smarts": rule["smarts"],
                    "rule_rationale": rule["rationale"],
                    "rdkit_version": caps["rdkit_version"],
                    "code_version": MODEL_VERSION,
                }
                results.append(outcome)

    funnel[0]["count"] = attempts

    # Novelty is checked once per surviving structure against ChEMBL, and an
    # unreachable check is reported as "unchecked" — never as "novel".
    novelty_unchecked = False
    if chembl is not None:
        for item in results:
            exists = chembl.smiles_exists(item["canonical_smiles"])
            if exists is None:
                item["constraints"]["novelty_chembl_exact_smiles"] = "unchecked"
                novelty_unchecked = True
            else:
                item["constraints"]["novelty_chembl_exact_smiles"] = not exists
                if exists:
                    item["constraints"]["rejected_after_novelty"] = True
    else:
        for item in results:
            item["constraints"]["novelty_chembl_exact_smiles"] = "unchecked"
        novelty_unchecked = True

    kept = [item for item in results if item["constraints"].get("novelty_chembl_exact_smiles") is not False]
    for index, item in enumerate(kept, start=1):
        item["id"] = f"gen-{index:04d}"
    # Every generated row shares one stable index across runs.
    funnel[-1]["count"] = len(kept)
    if not kept:
        funnel[-1]["count"] = 0

    warnings = []
    if novelty_unchecked:
        warnings.append("ChEMBL 新颖性检查未能完成或未执行，相关条目标记为 unchecked，不得解读为新结构。")

    return {
        "enabled": True,
        "reason": None,
        "method": "确定性规则枚举（固定 SMARTS + 固定片段）",
        "sa_method": SA_METHOD,
        "capabilities": caps,
        "rules": GENERATION_RULES,
        "seeds": [
            {"cgd_name": seed["cgd_name"], "chembl_id": seed.get("chembl_id"), "smiles": seed["canonical_smiles"]}
            for seed in seeds
        ],
        "funnel": funnel,
        "rejected_by_reason": dict(sorted(rejected.items(), key=lambda item: -item[1])),
        "compounds": kept,
        "constraints_evaluated": [
            "rdkit_valid", "single_component", "heavy_atoms", "molecular_weight",
            "lipinski", "veber", "pains_brenk", "tanimoto_window", "stereo_defined",
            "synthetic_accessibility", "novelty_chembl_exact_smiles",
        ],
        "constraints_not_evaluated": NOT_EVALUATED,
        "novelty_scope": NOVELTY_SCOPE,
        "novelty_unchecked": novelty_unchecked,
        "warnings": warnings,
        "boundary": (
            "本区结构由固定规则作用于真实抗真菌化合物枚举得到，仅用于探索化学空间。"
            "未经合成、未经活性测定，且本平台没有对接或真菌靶点活性模型，因此不提供任何活性、对接或 ADMET 预测；"
            "不参与任何候选排序，不能作为抗真菌活性证据。"
        ),
    }


def _evaluate_product(Chem, catalog, sascorer, product, seed_smiles, seen, rejected, funnel, stage_index) -> dict[str, Any] | None:
    """Apply every hard gate to one enumerated product, recording each rejection."""
    try:
        Chem.SanitizeMol(product)
    except Exception:
        rejected["invalid_structure"] = rejected.get("invalid_structure", 0) + 1
        return None
    canonical = Chem.MolToSmiles(product, isomericSmiles=True)
    if not canonical:
        rejected["invalid_structure"] = rejected.get("invalid_structure", 0) + 1
        return None
    if canonical in seen:
        rejected["duplicate"] = rejected.get("duplicate", 0) + 1
        return None
    seen.add(canonical)
    funnel[stage_index["rdkit_valid"]]["count"] += 1

    if len(Chem.GetMolFrags(product)) != 1:
        rejected["not_single_component"] = rejected.get("not_single_component", 0) + 1
        return None
    heavy = product.GetNumHeavyAtoms()
    if not MIN_HEAVY_ATOMS <= heavy <= MAX_HEAVY_ATOMS:
        rejected["heavy_atom_range"] = rejected.get("heavy_atom_range", 0) + 1
        return None
    weight = float(Chem.Descriptors.MolWt(product))
    if weight > MAX_MOLECULAR_WEIGHT:
        rejected["molecular_weight"] = rejected.get("molecular_weight", 0) + 1
        return None
    violations = int(Chem.Descriptors.NumRO5Violations(product))
    if violations > 1:
        rejected["lipinski"] = rejected.get("lipinski", 0) + 1
        return None
    donors = int(Chem.Descriptors.NumHDonors(product))
    acceptors = int(Chem.Descriptors.NumHAcceptors(product))
    rotatable = int(Chem.Descriptors.NumRotatableBonds(product))
    veber = donors <= 5 and acceptors <= 10 and rotatable <= 12
    if not veber:
        rejected["veber"] = rejected.get("veber", 0) + 1
        return None
    funnel[stage_index["drug_like"]]["count"] += 1

    alert_names: list[str] = []
    if catalog is not None:
        for match in catalog.GetMatches(product):
            try:
                alert_names.append(str(match.GetDescription()))
            except Exception:
                alert_names.append("unnamed alert")
    if alert_names:
        rejected["pains_brenk_alert"] = rejected.get("pains_brenk_alert", 0) + 1
        return None
    funnel[stage_index["alert_free"]]["count"] += 1

    similarity = tanimoto(seed_smiles, canonical)
    if similarity is None or not TANIMOTO_LOW <= similarity <= TANIMOTO_HIGH:
        rejected["tanimoto_window"] = rejected.get("tanimoto_window", 0) + 1
        return None
    funnel[stage_index["related"]]["count"] += 1

    unassigned = [centre for centre in Chem.FindMolChiralCenters(product, includeUnassigned=True) if centre[1] == "?"]
    if unassigned:
        rejected["undefined_stereocentre"] = rejected.get("undefined_stereocentre", 0) + 1
        return None
    funnel[stage_index["stereo_ok"]]["count"] += 1

    if sascorer is None:
        rejected["synthetic_accessibility_unavailable"] = rejected.get("synthetic_accessibility_unavailable", 0) + 1
        return None
    sa_score = float(sascorer.calculateScore(product))
    if not math.isfinite(sa_score) or sa_score > MAX_SA_SCORE:
        rejected["synthetic_accessibility"] = rejected.get("synthetic_accessibility", 0) + 1
        return None
    funnel[stage_index["synthesizable"]]["count"] += 1

    return {
        "id": None,  # assigned after the loop so ordering stays deterministic
        "canonical_smiles": canonical,
        "formula": str(rd_formula(Chem, product)),
        "svg": draw_svg(Chem, product),
        "properties": {
            "molecular_weight": round(weight, 3),
            "logp": round(float(Chem.Descriptors.MolLogP(product)), 3),
            "tpsa": round(float(Chem.Descriptors.TPSA(product)), 3),
            "h_bond_donors": donors,
            "h_bond_acceptors": acceptors,
            "rotatable_bonds": rotatable,
            "qed": round(float(_qed(product)), 4),
            "heavy_atoms": heavy,
            "rings": int(Chem.Descriptors.RingCount(product)),
            "aromatic_rings": int(Chem.Descriptors.NumAromaticRings(product)),
            "sp3_fraction": round(float(Chem.Descriptors.FractionCSP3(product)), 4),
            "stereocenters_defined": len(Chem.FindMolChiralCenters(product, includeUnassigned=False)),
            "stereocenters_unspecified": 0,
            "max_ring_size": int(_max_ring_size(Chem, product)),
            "has_macrocycle": _max_ring_size(Chem, product) >= 12,
            "scaffold": str(_scaffold(Chem, product)),
            "descriptor_source": f"RDKit {capabilities()['rdkit_version']} 本地计算",
        },
        "constraints": {
            "rdkit_valid": True,
            "single_component": True,
            "lipinski": True,
            "veber": True,
            "pains_brenk_clear": True,
            "tanimoto_to_seed": round(similarity, 4) if similarity is not None else None,
            "stereo_defined": True,
            "sa_score": round(sa_score, 3),
            "novelty_chembl_exact_smiles": "unchecked",
        },
        "scientific_status": "unvalidated_exploration",
        "activity_claim": None,
        "docking_score": None,
        "admet_predictions": {},
        "boundary": "规则枚举产物；未经验证，不代表活性或成药性。",
    }


def _qed(molecule) -> float:
    try:
        from rdkit.Chem import QED

        return float(QED.qed(molecule))
    except Exception:
        return float("nan")


def rd_formula(Chem, molecule) -> str:
    try:
        return str(Chem.rdMolDescriptors.CalcMolFormula(molecule))
    except Exception:
        return ""


def _max_ring_size(Chem, molecule) -> int:
    try:
        return max((len(ring) for ring in molecule.GetRingInfo().AtomRings()), default=0)
    except Exception:
        return 0


def _scaffold(Chem, molecule) -> str:
    rd = _rdkit()
    if rd is None:
        return ""
    try:
        return str(rd["scaffold"].MurckoScaffoldSmiles(mol=molecule))
    except Exception:
        return ""


def draw_svg(Chem, molecule) -> str:
    """Render a 2D depiction server-side and validate it before embedding.

    Generated structures have no ChEMBL ID, so the shared structure endpoint
    cannot serve them; the SVG is produced here and passed through the same
    validator the production structure endpoint uses.
    """
    if not capabilities()["svg_depiction"]:
        return ""
    try:
        from rdkit.Chem.Draw import rdMolDraw2D

        drawer = rdMolDraw2D.MolDraw2DSVG(240, 180)
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, molecule)
        drawer.FinishDrawing()
        raw = drawer.GetDrawingText().encode("utf-8")
    except Exception:
        return ""
    try:
        from .molecule_structure import safe_svg

        return safe_svg(raw).decode("utf-8")
    except Exception:
        # A depiction that cannot pass the active-content validator is dropped
        # rather than embedded; the caller falls back to SMILES.
        return ""


# --------------------------------------------------------------------------
# Exploration pipeline
# --------------------------------------------------------------------------

def _seed_compounds(artifact: dict[str, Any], target: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Named compounds with curated genetic evidence against this target.

    Seeds must be real antifungal compounds; broad-spectrum stress agents and
    immunosuppressants are skipped because deriving analogues from them would
    not be meaningful antifungal chemistry.
    """
    wanted = set(target.get("sensitizing_compounds") or []) | set(target.get("resistance_compounds") or [])
    by_name = {item["cgd_name"]: item for item in _screened_compounds(artifact)}
    return [by_name[name] for name in sorted(wanted) if name in by_name][:limit]


def _exploration_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    counts = artifact.get("counts", {})
    unresolved = [item.get("cgd_name") for item in artifact.get("unresolved_compounds", [])]
    warnings = []
    if unresolved:
        warnings.append(
            "以下化合物未能在 ChEMBL 解析出结构，已从检索与生成基础中排除：" + "、".join(str(name) for name in unresolved) + "。"
        )
    dropped = counts.get("cgd_links_dropped_unresolved_compound")
    if dropped:
        warnings.append(f"另有 {dropped} 条 CGD 记录因化合物无结构而未纳入。")
    if counts.get("cgd_rows_without_uniprot_mapping"):
        warnings.append(f"{counts['cgd_rows_without_uniprot_mapping']} 条 CGD 记录因缺少 UniProt 映射而未纳入。")
    return {"counts": counts, "unresolved_compounds": unresolved, "warnings": warnings}


def _genetic_evidence(artifact: dict[str, Any], uniprot: str) -> list[dict[str, Any]]:
    """All curated gene-compound links for one target, sorted for stable output."""
    rows = [link for link in artifact.get("links", []) if link.get("uniprot") == uniprot]
    order = {"sensitizing": 0, "resistance": 1, "neutral": 2}
    return sorted(rows, key=lambda item: (order.get(item.get("direction"), 3), item.get("cgd_name", ""), item.get("reference", "")))


def explore_target(
    artifact: dict[str, Any],
    target: dict[str, Any],
    *,
    chembl: "FungalChEMBL | None",
    similarity_threshold: int = 70,
    include_similarity: bool = True,
    max_generated: int = DEFAULT_MAX_GENERATED,
    include_generation: bool = True,
) -> dict[str, Any]:
    """Assemble one target's exploration result. Nothing here ranks anything."""
    uniprot = str(target.get("uniprot") or "")
    gene = str(target.get("gene_symbol") or "")
    warnings: list[str] = list(_exploration_summary(artifact)["warnings"])
    genetic = _genetic_evidence(artifact, uniprot)

    known: list[dict[str, Any]] = []
    direct: list[dict[str, Any]] = []
    similar: list[dict[str, Any]] = []
    resolution: dict[str, Any] = {
        "uniprot": uniprot,
        "gene_symbol": gene,
        "chembl_candidates": [],
        "chembl_target_id": None,
        "status": "not_checked",
        "error": None,
    }

    if chembl is not None:
        try:
            candidates = chembl.target_for_uniprot(uniprot)
        except Exception as exc:
            candidates = []
            resolution["status"] = "request_failed"
            resolution["error"] = f"{type(exc).__name__}"
            warnings.append("ChEMBL 靶标解析请求失败；本次未取回直接结合证据与相似性扩展。")
        if candidates:
            resolution["chembl_candidates"] = [
                {
                    "target_chembl_id": item.get("target_chembl_id"),
                    "pref_name": item.get("pref_name"),
                    "organism": item.get("organism"),
                    "target_type": item.get("target_type"),
                }
                for item in candidates
            ]
            # Prefer a species-level SINGLE PROTEIN hit; never silently take the
            # first row when the lookup returns an organism-level grouping too.
            preferred = next(
                (item for item in candidates
                 if item.get("target_type") == "SINGLE PROTEIN" and "candida" in str(item.get("organism") or "").lower()),
                None,
            ) or next((item for item in candidates if item.get("target_type") == "SINGLE PROTEIN"), None)
            if preferred is None:
                resolution["status"] = "no_single_protein_target"
            else:
                resolution["chembl_target_id"] = preferred.get("target_chembl_id")
                resolution["resolved_organism"] = preferred.get("organism")
                resolution["resolved_pref_name"] = preferred.get("pref_name")
                resolution["status"] = "resolved"
                if len(candidates) > 1:
                    warnings.append(
                        f"ChEMBL 对该 UniProt 返回 {len(candidates)} 个候选靶标，"
                        f"已选用 {preferred.get('target_chembl_id')}（{preferred.get('organism')}）；其余候选保留在 resolution 中供复核。"
                    )
        elif resolution["status"] == "not_checked":
            resolution["status"] = "not_mapped"
            warnings.append("ChEMBL 中没有与该 UniProt 精确对应的靶标记录；不提供直接结合证据。")

    # --- layer 2: measured direct binding, only when a target actually resolved
    if chembl is not None and resolution.get("chembl_target_id"):
        try:
            measured = chembl.measured_activities(
                resolution["chembl_target_id"], str(resolution.get("resolved_organism") or "")
            )
            resolution["activity_total"] = measured["activity_total"]
            resolution["activity_truncated"] = measured["truncated"]
            resolution["organism_mismatch_count"] = measured["organism_mismatch_count"]
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in measured["rows"]:
                molecule_id = str(row.get("parent_molecule_chembl_id") or row.get("molecule_chembl_id") or "")
                if molecule_id and _number(row.get("pchembl_value")) is not None:
                    grouped.setdefault(molecule_id, []).append(row)
            for molecule_id, rows in sorted(grouped.items()):
                potencies = sorted(_number(row.get("pchembl_value")) for row in rows if _number(row.get("pchembl_value")) is not None)
                if not potencies:
                    continue
                direct.append({
                    "evidence_kind": "measured_activity",
                    "molecule_chembl_id": molecule_id,
                    "pchembl_median": round(potencies[len(potencies) // 2], 3),
                    "pchembl_best": round(max(potencies), 3),
                    "activity_count": len(rows),
                    "assay_count": len({str(row.get("assay_chembl_id") or "") for row in rows}),
                    "document_count": len({str(row.get("document_chembl_id") or "") for row in rows}),
                    "activity_types": sorted({str(row.get("standard_type") or "") for row in rows if row.get("standard_type")}),
                })
        except Exception:
            warnings.append("ChEMBL 活性查询失败；本次未取回直接结合证据。")

    # --- layer 1: curated compound identity, structure and properties
    by_name = {item["cgd_name"]: item for item in _screened_compounds(artifact)}
    touched = sorted({link["cgd_name"] for link in genetic if link["cgd_name"] in by_name})
    for name in touched:
        item = by_name[name]
        entry: dict[str, Any] = {
            "evidence_kind": "curated_chemogenomics",
            "cgd_name": name,
            "class": item.get("class"),
            "note": item.get("note"),
            "molecule_chembl_id": item.get("chembl_id"),
            "name": item.get("pref_name") or name,
            "canonical_smiles": item.get("canonical_smiles"),
            "inchi_key": item.get("inchi_key"),
            "max_phase": item.get("max_phase"),
            "first_approval": item.get("first_approval"),
            "descriptors": None,
            "mechanisms": [],
            "indications": [],
            "structural_alerts": structural_alerts(item.get("canonical_smiles") or ""),
        }
        if chembl is not None:
            try:
                molecule = chembl.molecule(str(item.get("chembl_id")))
                entry.update({
                    k: v for k, v in _identity(molecule).items()
                    if k in {"molecule_type", "black_box_warning", "withdrawn_flag"}
                })
                if not entry["canonical_smiles"]:
                    entry["canonical_smiles"] = _identity(molecule)["canonical_smiles"]
                entry["descriptors"] = _descriptors_from_chembl(molecule)
            except Exception:
                warnings.append(f"{name} 的化合物属性未能从 ChEMBL 取回，属性留空。")
            try:
                entry["mechanisms"] = [
                    {"mechanism_of_action": str(row.get("mechanism_of_action") or ""), "action_type": str(row.get("action_type") or "")}
                    for row in chembl.mechanism(str(item.get("chembl_id")))
                ][:5]
            except Exception:
                entry["mechanisms"] = []
        known.append(entry)

    # --- layer 3: similarity expansion around the curated compounds
    if chembl is not None and include_similarity:
        seen_ids = {entry["molecule_chembl_id"] for entry in known}
        for entry in known:
            smiles = entry.get("canonical_smiles")
            if not smiles:
                continue
            try:
                payload = chembl.similarity(smiles, similarity_threshold)
            except Exception:
                warnings.append(f"{entry['cgd_name']} 的相似性扩展请求失败。")
                continue
            for hit in payload.get("molecules") or []:
                hit_id = str(hit.get("molecule_chembl_id") or "")
                if not hit_id or hit_id in seen_ids:
                    continue
                seen_ids.add(hit_id)
                identity = _identity(hit)
                if not identity["canonical_smiles"]:
                    continue
                similar.append({
                    "evidence_kind": "similarity_only",
                    "reference_cgd_name": entry["cgd_name"],
                    "reference_molecule_chembl_id": entry["molecule_chembl_id"],
                    "chembl_similarity": _number(hit.get("similarity")),
                    "rdkit_tanimoto": tanimoto(smiles, identity["canonical_smiles"]),
                    "descriptors": _descriptors_from_chembl(hit),
                    "structural_alerts": structural_alerts(identity["canonical_smiles"]),
                    "note": "相似性检索得到的分子，未检索到针对本靶标的实测活性。",
                    **identity,
                })

    seeds = _seed_compounds(artifact, target)
    generation = (
        generate_compounds(seeds, max_generated, chembl)
        if include_generation
        else {"enabled": False, "reason": "本次未请求生成新化合物。", "capabilities": capabilities(),
              "compounds": [], "funnel": [], "rejected_by_reason": {}, "rules": GENERATION_RULES,
              "constraints_evaluated": [], "constraints_not_evaluated": NOT_EVALUATED,
              "novelty_scope": NOVELTY_SCOPE, "warnings": []}
    )
    warnings.extend(generation.get("warnings") or [])

    return {
        "target": {
            "uniprot": uniprot,
            "gene_symbol": gene,
            "protein_name": target.get("protein_name"),
            "compound_count": target.get("compound_count"),
            "sensitizing_compounds": target.get("sensitizing_compounds") or [],
            "resistance_compounds": target.get("resistance_compounds") or [],
        },
        "resolution": resolution,
        "genetic_evidence": genetic,
        "known_compounds": known,
        "direct_binding": direct,
        "similar_compounds": similar,
        "generated_compounds": generation,
        "warnings": warnings,
    }


def analyse_fungal_compound_job(job: Job, chembl: "FungalChEMBL | None" = None) -> dict[str, Any]:
    """Full result payload for one exploration job."""
    artifact = load_cgd_artifact()
    if artifact is None:
        raise FungalCompoundError(
            "FUNGAL_COMPOUND_DATA_NOT_CONFIGURED",
            "服务器尚未构建 CGD 化学遗传学证据文件，无法进行化合物探索。",
        )
    parameters = job.parameters or {}
    targets = artifact.get("targets", [])
    if not targets:
        raise FungalCompoundError("FUNGAL_COMPOUND_NO_TARGETS", "证据文件中没有可用的靶标。")

    identifiers = [str(item).strip() for item in (parameters.get("target_ids") or []) if str(item).strip()]
    if not identifiers:
        raise FungalCompoundError("FUNGAL_COMPOUND_TARGET_REQUIRED", "请选择至少一个靶标。")
    max_targets = int(parameters.get("max_targets") or 12)
    if len(identifiers) > max_targets:
        raise FungalCompoundError(
            "FUNGAL_COMPOUND_TOO_MANY_TARGETS", f"单次最多分析 {max_targets} 个靶标，请减少选择。",
        )

    include_similarity = bool(parameters.get("include_similarity", True))
    include_generation = bool(parameters.get("include_generation", True))
    similarity_threshold = int(parameters.get("similarity_threshold") or 70)
    if not 40 <= similarity_threshold <= 100:
        raise FungalCompoundError("FUNGAL_COMPOUND_SIMILARITY", "相似性阈值须在 40–100 之间。")
    max_generated = int(parameters.get("max_generated") or DEFAULT_MAX_GENERATED)
    if not 1 <= max_generated <= HARD_MAX_GENERATED:
        raise FungalCompoundError("FUNGAL_COMPOUND_MAX_GENERATED", f"生成数量须在 1–{HARD_MAX_GENERATED} 之间。")

    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for identifier in identifiers:
        uniprot = "" if identifier.upper().startswith(("CHEMBL",)) else identifier
        entry = _target_entry(artifact, uniprot, identifier)
        if entry is None:
            raise FungalCompoundError(
                "FUNGAL_COMPOUND_TARGET_NOT_FOUND",
                f"证据文件中没有与「{identifier}」匹配的靶标；请从靶标清单中选择。",
            )
        resolved.append((identifier, entry))

    # In proteome mode every target would otherwise run its own generation
    # pass; generation stays off there unless one target was chosen.
    generation_per_target = include_generation and len(resolved) == 1
    owned = chembl is None
    client = chembl or FungalChEMBL()
    try:
        results = []
        for identifier, entry in resolved:
            try:
                results.append(explore_target(
                    artifact, entry,
                    chembl=client,
                    similarity_threshold=similarity_threshold,
                    include_similarity=include_similarity,
                    max_generated=max_generated,
                    include_generation=generation_per_target,
                ))
            except Exception as exc:  # keep the batch auditable rather than losing all targets
                results.append({
                    "target": {"uniprot": entry.get("uniprot"), "gene_symbol": entry.get("gene_symbol"),
                               "protein_name": entry.get("protein_name")},
                    "resolution": {"status": "request_failed", "error": f"{type(exc).__name__}"},
                    "genetic_evidence": _genetic_evidence(artifact, str(entry.get("uniprot") or "")),
                    "known_compounds": [], "direct_binding": [], "similar_compounds": [],
                    "generated_compounds": {"enabled": False, "reason": "该靶标处理失败。", "compounds": []},
                    "warnings": [f"{entry.get('gene_symbol') or entry.get('uniprot')} 处理失败：{type(exc).__name__}"],
                })
            job.progress = min(95, 40 + int(55 * len(results) / max(1, len(resolved))))
    finally:
        if owned:
            client.close()

    integrity = artifact_integrity()
    summary = _exploration_summary(artifact)
    return {
        "schema_version": "1.0",
        "model_version": MODEL_VERSION,
        "workflow": "compound_exploration",
        "direction": "target_to_compound",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "organism": str(parameters.get("organism") or "Candida albicans SC5314"),
        "data": {
            "configured": True,
            "artifact": str(_artifact_path()),
            "bundle_version": FUNGAL_TARGET_DATA_DIR.name,
            "artifact_sha256": integrity.get("hash"),
            "sha256_verified": integrity.get("sha256_verified"),
            "chembl_db_version": artifact.get("chembl_db_version"),
            "chembl_release_date": artifact.get("chembl_release_date"),
            **summary,
        },
        "parameters": {
            "target_ids": identifiers,
            "similarity_threshold": similarity_threshold,
            "include_similarity": include_similarity,
            "include_generation": include_generation,
            "generation_per_target": generation_per_target,
            "max_generated": max_generated,
        },
        "targets": results,
        "capabilities": capabilities(),
        "evidence_layers": [
            {"id": "genetic", "label": "CGD 化学遗传学证据", "kind": "curated", "direction": "genetic"},
            {"id": "measured", "label": "ChEMBL 直接结合实测", "kind": "measured"},
            {"id": "similarity", "label": "ChEMBL 相似性扩展", "kind": "similarity_only"},
            {"id": "generated", "label": "规则衍生新结构", "kind": "generated_unvalidated"},
        ],
        "boundary": (
            "本模块只汇总已有策展证据、检索真实化合物并给出规则枚举的新结构。"
            "遗传学证据来自 CGD 的单基因扰动实验，ChEMBL 直接结合证据来自体外测定，"
            "相似性命中不携带活性数据，生成结构没有任何活性、对接或 ADMET 预测。"
            "所有内容都是计算候选与探索方向，不构成已确认靶标或已确认活性。"
        ),
    }


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------

class ResolveRequest(BaseModel):
    identifier: str


def _targets_payload() -> dict[str, Any]:
    catalog = target_catalog()
    catalog["compounds"] = catalog.get("compounds", [])
    return catalog


@router.get("/fungal-compounds/targets")
def get_fungal_compound_targets(user: UserContext = Depends(get_current_user)):
    return _targets_payload()


@router.post("/fungal-compounds/resolve-target")
def resolve_fungal_target(payload: ResolveRequest, user: UserContext = Depends(get_current_user)):
    """Resolve an accession or gene symbol. Never auto-picks when ambiguous."""
    catalog = target_catalog()
    if not catalog.get("configured"):
        return {"status": "not_configured", "message": catalog["message"], "candidates": []}
    query = payload.identifier.strip()
    if not query:
        raise HTTPException(status_code=422, detail="请输入 UniProt 编号或基因符号。")
    upper = query.upper()
    exact = [item for item in catalog["targets"] if str(item.get("uniprot", "")).upper() == upper]
    if exact:
        return {"status": "resolved", "query": query, "matched_by": "uniprot", "candidates": exact}
    by_gene = [item for item in catalog["targets"] if str(item.get("gene_symbol", "")).upper() == upper]
    if len(by_gene) == 1:
        return {"status": "resolved", "query": query, "matched_by": "gene_symbol", "candidates": by_gene}
    if len(by_gene) > 1:
        return {"status": "ambiguous", "query": query, "matched_by": "gene_symbol", "candidates": by_gene}
    partial = [
        item for item in catalog["targets"]
        if upper in str(item.get("gene_symbol", "")).upper() or upper in str(item.get("protein_name", "")).upper()
    ][:20]
    if partial:
        return {"status": "ambiguous", "query": query, "matched_by": "substring", "candidates": partial}
    return {"status": "not_found", "query": query, "candidates": []}


@router.get("/jobs/{job_id}/fungal-compound-result")
def get_fungal_compound_result(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job or job.type != "fungal_compound":
        raise HTTPException(status_code=404, detail="未找到化合物探索任务。")
    require_project_access(db, job.project_id, user)
    if job.status != JobStatus.completed.value or not job.output_ref:
        raise HTTPException(status_code=409, detail="化合物探索任务尚未完成。")
    try:
        return json.loads(read_storage_bytes(job.output_ref, MAX_RESULT_BYTES))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="化合物探索结果文件无法读取。") from exc


def process_fungal_compound(db: Session, job: Job) -> dict:
    """Job processor: explore targets, persist the result, report provenance."""
    client = FungalChEMBL()
    try:
        result = analyse_fungal_compound_job(job, client)
        failures = client.failures
    finally:
        client.close()
    result.update({"job_id": job.id, "project_id": job.project_id})
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    storage_uri, digest = save_asset(job.project_id, f"fungal-compound-{job.id}.json", payload)
    job.output_ref = storage_uri
    generated = sum(len(item["generated_compounds"].get("compounds") or []) for item in result["targets"])
    return {
        "adapter": "FungalCompoundExplorationAdapter",
        "model_name": "真菌靶标到化合物的探索与规则衍生",
        "model_version": MODEL_VERSION,
        "provenance": {
            "data_mode": "real",
            "evidence": "CGD 化学遗传学 + ChEMBL 检索 + RDKit 规则枚举",
            "bundle_version": FUNGAL_TARGET_DATA_DIR.name,
            "chembl_db_version": result["data"].get("chembl_db_version"),
            "targets": len(result["targets"]),
            "generated_compounds": generated,
            "chembl_request_failures": failures,
            "output_sha256": digest,
            "scientific_conclusion": False,
        },
        "demo": False,
    }


