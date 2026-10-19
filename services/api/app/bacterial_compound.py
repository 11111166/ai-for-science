"""Target-to-compound exploration for bacterial targets.

Given a bacterial protein, what compounds are known against it, what does the
curated resistance/virulence evidence say about it, and what structurally novel
analogues can be derived from named antibacterial compounds.  It deliberately
ranks nothing: it reports curated evidence, retrieved molecules with their
measured properties, and generated structures that carry no activity claim at
all.

Evidence hierarchy, strongest first:

1. CARD / PHI-base curation -- CARD records which gene families and mutations
   confer resistance to a named drug class, and PHI-base records which gene
   perturbations change susceptibility to a named compound.  These are the
   bacterial analogue of a chemogenomic evidence spine: they name real
   compounds and carry a curated direction.
2. ChEMBL measured activity -- compounds with a direct binding/functional
   measurement against the target.  Available for only a handful of bacterial
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
import sqlite3
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .chembl_runtime import ChEMBLRuntimeAdapter, _number
from .database import get_db
from .models import Asset, Job, JobStatus
from .storage import read_storage_bytes, save_asset


router = APIRouter(prefix="/api/v1/drug-discovery")
MODEL_VERSION = "bacterial-compound-exploration-v1"
MAX_RESULT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_GENERATED = 25
HARD_MAX_GENERATED = 60

# Genus names used to recognise a bacterial ChEMBL target organism.  Kept as a
# plain tuple so the check stays explicit and reviewable; it never decides
# whether a target *is* valid, only whether an organism-level ChEMBL hit is
# preferred over an organism-grouping hit.
_BACTERIAL_ORGANISM_HINTS = (
    "staphylococcus", "klebsiella", "acinetobacter", "pseudomonas", "enterococcus",
    "enterobacter", "escherichia", "mycobacterium", "streptococcus", "salmonella",
    "listeria", "shigella", "campylobacter", "helicobacter", "neisseria",
    "haemophilus", "moraxella", "serratia", "proteus", "citrobacter", "burkholderia",
    "stenotrophomonas", "clostridioides", "clostridium", "bacteroides", "vibrio",
    "yersinia", "legionella", "chlamydia", "treponema", "borrelia", "rickettsia",
)


def _organism_matches_bacteria(organism: str) -> bool:
    name = organism.strip().lower()
    return bool(name) and any(hint in name for hint in _BACTERIAL_ORGANISM_HINTS)


# Caps keep one exploration job bounded: the whole thing runs inline under the
# shared job lock, and a similarity call per curated compound adds up fast.
MAX_SIMILARITY_RESULTS = 25
MAX_SIMILARITY_PER_TARGET = 40

BACTERIAL_TARGET_DATA_DIR = Path(os.environ.get("BACTERIAL_TARGET_DATA_DIR", "./data/shared/bacterial-discovery-data/current"))
BACTERIAL_DISCOVERY_DATA_DIR = Path(os.environ.get("BACTERIAL_DISCOVERY_DATA_DIR", "./data/shared/bacterial-discovery-data/current"))
CARD_ARTIFACT = "derived/bacterial-card-evidence.json"
DISCOVERY_INDEX = "derived/bacterial-discovery.sqlite3"
DISCOVERY_INDEX_MANIFEST = "derived/index-manifest.json"
VERIFIED_CAS_BY_INCHIKEY = {
    "XMAYWYJOQHXEEK-UHFFFAOYSA-N": {
        "numbers": ["65277-42-1"],
        "source": "https://pubchem.ncbi.nlm.nih.gov/compound/ketoconazole#section=CAS",
        "label": "PubChem CAS",
    },
    "BYBLEWFAAKGYCD-UHFFFAOYSA-N": {
        "numbers": ["22916-47-8"],
        "source": "https://pubchem.ncbi.nlm.nih.gov/compound/4189#section=CAS",
        "label": "PubChem CAS",
    },
    "WSEKTEUGRLFBSE-UHFFFAOYSA-N": {
        "numbers": ["936339-60-5"],
        "source": "https://precision.fda.gov/uniisearch/srs/unii/7B1P18ID9L",
        "label": "FDA GSRS",
    },
}

# Release-local chemistry dependencies, loaded exactly as molecule_workbench
# does so the shared API runtime stays untouched.  Everything RDKit-related is
# imported lazily and degrades to "unavailable" rather than failing a job.
_chemdeps = Path(__file__).resolve().parent.parent / "_chemdeps"


class BacterialCompoundError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


class StructureSearchRequest(BaseModel):
    structure: str
    format: str = "auto"
    similarity_threshold: float = 0.55
    max_results: int = 12


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
# CARD artifact
# --------------------------------------------------------------------------

def _artifact_path() -> Path:
    return BACTERIAL_TARGET_DATA_DIR / CARD_ARTIFACT


def _bundle_version() -> str:
    """Name of the bundle release actually in use.

    The configured directory is the ``current`` symlink, so its basename is
    "current" and says nothing about which release is live.  Resolving it first
    reports the release the operator actually switched to.
    """
    try:
        return BACTERIAL_TARGET_DATA_DIR.resolve().name
    except OSError:
        return BACTERIAL_TARGET_DATA_DIR.name


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
    """Check the artifact's SHA-256 against the bundle manifest when listed.

    The manifest records paths relative to the bundle root, and the configured
    data directory is normally the ``current`` symlink rather than the release
    it points at.  Resolving both sides is therefore required; comparing a
    resolved path against an unresolved one raises ValueError on this host.
    """
    path = _artifact_path()
    manifest_path = BACTERIAL_TARGET_DATA_DIR / "manifest.json"
    result: dict[str, Any] = {"path": str(path), "sha256_verified": False, "hash": None}
    if not path.is_file() or not manifest_path.is_file():
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    try:
        relative = str(path.resolve().relative_to(BACTERIAL_TARGET_DATA_DIR.resolve()))
    except (OSError, ValueError):
        # Layout differs from what the manifest describes; report unverified
        # rather than failing the whole request over a bookkeeping check.
        return result
    expected = next(
        (item.get("sha256") for item in manifest.get("files", []) if item.get("path") == relative),
        None,
    )
    if not expected:
        return result
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return result
    result["hash"] = digest
    result["expected"] = expected
    result["sha256_verified"] = digest == expected
    return result


def target_catalog() -> dict[str, Any]:
    """Every reference-strain protein that has curated CARD/PHI-base evidence."""
    artifact = load_cgd_artifact()
    if artifact is None:
        same_bundle = BACTERIAL_TARGET_DATA_DIR.resolve() == BACTERIAL_DISCOVERY_DATA_DIR.resolve()
        indexed_targets = _activity_target_catalog() if same_bundle else []
        if indexed_targets:
            return {
                "configured": True,
                "artifact": None,
                "bundle_version": _bundle_version(),
                "message": "CARD 策展文件不可用；当前仅展示本地 ChEMBL/BindingDB 实测活性靶标。",
                "counts": {"searchable_targets": len(indexed_targets), "activity_targets": len(indexed_targets)},
                "compounds": [],
                "unresolved_compounds": [],
                "targets": indexed_targets,
                "organisms": [],
            }
        return {
            "configured": False,
            "artifact": str(_artifact_path()),
            "bundle_version": _bundle_version(),
            "message": "服务器尚未构建 CARD/PHI-base 策展证据文件；请先运行 infra/build_bacterial_card_evidence.py。",
            "counts": {},
            "compounds": [],
            "unresolved_compounds": [],
            "targets": [],
        }
    integrity = artifact_integrity()
    curated_targets = list(artifact.get("targets", []))
    indexed_targets = _activity_target_catalog()
    by_accession = {
        str(item.get("uniprot") or "").upper(): item
        for item in curated_targets if item.get("uniprot")
    }
    for item in indexed_targets:
        accession = str(item.get("uniprot") or "").upper()
        current = by_accession.get(accession)
        if current is None:
            curated_targets.append(item)
            by_accession[accession] = item
            continue
        current["activity_evidence_count"] = int(item.get("activity_evidence_count") or 0)
        current["direct_binding_count"] = int(item.get("direct_binding_count") or 0)
        current["activity_target_keys"] = item.get("activity_target_keys") or []
        current["evidence_sources"] = sorted(set([
            *(current.get("evidence_sources") or ["CARD"]),
            *(item.get("evidence_sources") or []),
        ]))
        current["compound_count"] = max(
            int(current.get("compound_count") or 0), int(item.get("compound_count") or 0),
        )
    curated_targets.sort(key=lambda item: (
        -int(item.get("direct_binding_count") or 0),
        -int(item.get("activity_evidence_count") or 0),
        -int(item.get("compound_count") or 0),
        str(item.get("organism") or item.get("organism_slug") or ""),
        str(item.get("gene_symbol") or item.get("uniprot") or ""),
    ))
    homology = _host_homology_map([str(item.get("uniprot") or "") for item in curated_targets])
    for item in curated_targets:
        hit = homology.get(str(item.get("uniprot") or "").upper())
        if hit:
            item.update(hit)
    counts = dict(artifact.get("counts", {}))
    counts.update({
        "searchable_targets": len(curated_targets),
        "activity_targets": len(indexed_targets),
        "curated_targets": len(artifact.get("targets", [])),
    })
    return {
        "configured": True,
        "artifact": str(_artifact_path()),
        "bundle_version": _bundle_version(),
        "generated_at": artifact.get("generated_at"),
        "chembl_db_version": artifact.get("chembl_db_version"),
        "sha256_verified": integrity["sha256_verified"],
        "counts": counts,
        "compounds": [
            {
                "curated_name": item.get("curated_name"),
                "chembl_id": item.get("chembl_id"),
                "class": item.get("class"),
                "pref_name": item.get("pref_name"),
                "note": item.get("note"),
            }
            for item in artifact.get("compounds", [])
        ],
        "unresolved_compounds": artifact.get("unresolved_compounds", []),
        "targets": curated_targets,
        "organisms": artifact.get("organisms", []),
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
# ChEMBL retrieval (bacterial targets; never touches the human path)
# --------------------------------------------------------------------------

class BacterialChEMBL:
    """Thin bacterial-side reader built on the existing retry-aware client."""

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
        strain string for this target, e.g. "Staphylococcus aureus
        (strain NCTC 8325)"), so any shorter value silently returns zero.
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
# Deterministic generation from named antibacterial compounds
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

# Enumeration breadth only; the numeric gates below decide what survives.
MAX_SITES_PER_RULE = 30
TANIMOTO_LOW = 0.20
TANIMOTO_HIGH = 0.70
MAX_SA_SCORE = 4.0
MIN_HEAVY_ATOMS = 12
MAX_HEAVY_ATOMS = 50
MAX_MOLECULAR_WEIGHT = 500.0
MIN_SA = 1.0

NOT_EVALUATED = {
    "antibacterial_activity": "本平台没有细菌靶点活性预测模型；生成物未经任何活性计算。",
    "selectivity": "没有宿主选择性模型；不得用结构相似度代替选择性判断。",
    "docking": "没有对接引擎（无 Vina/GNINA）与 GPU 队列，未做任何对接。",
    "admet": "现有 ADMET 模型仅在既定人源疾病化合物集上训练，对细菌靶点不适用。",
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
        from rdkit.Chem import Descriptors, Lipinski, rdChemReactions, rdFingerprintGenerator, rdMolDescriptors
        from rdkit.Chem.Scaffolds import MurckoScaffold

        return {
            "Chem": Chem,
            "Descriptors": Descriptors,
            "Lipinski": Lipinski,
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


def generate_compounds(seeds: list[dict[str, Any]], max_generated: int, chembl: "BacterialChEMBL | None") -> dict[str, Any]:
    """Enumerate analogues of named antibacterial compounds under explicit gates."""
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
            "reason": "该靶标没有 CARD/PHI-base 点名的抗菌化合物作为衍生基础，未生成任何结构。",
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
                    rd, catalog, sascorer, product, seed_smiles, seen, rejected, funnel, stage_index,
                )
                if outcome is None:
                    continue
                outcome["provenance"] = {
                    "seed_curated_name": seed["curated_name"],
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
            {"curated_name": seed["curated_name"], "chembl_id": seed.get("chembl_id"), "smiles": seed["canonical_smiles"]}
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
            "本区结构由固定规则作用于真实抗菌化合物枚举得到，仅用于探索化学空间。"
            "未经合成、未经活性测定，且本平台没有对接或细菌靶点活性模型，因此不提供任何活性、对接或 ADMET 预测；"
            "不参与任何候选排序，不能作为抗菌活性证据。"
        ),
    }


def _evaluate_product(rd, catalog, sascorer, product, seed_smiles, seen, rejected, funnel, stage_index) -> dict[str, Any] | None:
    """Apply every hard gate to one enumerated product, recording each rejection."""
    Chem, Descriptors, Lipinski = rd["Chem"], rd["Descriptors"], rd["Lipinski"]
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
    weight = float(Descriptors.MolWt(product))
    if weight > MAX_MOLECULAR_WEIGHT:
        rejected["molecular_weight"] = rejected.get("molecular_weight", 0) + 1
        return None
    donors = int(Lipinski.NumHDonors(product))
    acceptors = int(Lipinski.NumHAcceptors(product))
    rotatable = int(Lipinski.NumRotatableBonds(product))
    # This RDKit build exposes no NumRO5Violations, so the classic four-rule
    # count is computed explicitly (same rule the offline pipeline applies).
    logp = float(Descriptors.MolLogP(product))
    tpsa_value = float(Descriptors.TPSA(product))
    violations = int(weight > 500) + int(logp > 5) + int(donors > 5) + int(acceptors > 10)
    if violations > 1:
        rejected["lipinski"] = rejected.get("lipinski", 0) + 1
        return None
    veber = tpsa_value <= 140 and rotatable <= 10
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
        "inchi_key": str(Chem.MolToInchiKey(product) or "") if hasattr(Chem, "MolToInchiKey") else "",
        "formula": _formula(rd, product),
        "svg": draw_svg(product),
        "properties": {
            "molecular_weight": round(weight, 3),
            "logp": round(logp, 3),
            "tpsa": round(tpsa_value, 3),
            "h_bond_donors": donors,
            "h_bond_acceptors": acceptors,
            "rotatable_bonds": rotatable,
            "qed": round(float(_qed(product)), 4),
            "heavy_atoms": heavy,
            "rings": int(Descriptors.RingCount(product)),
            "aromatic_rings": int(Descriptors.NumAromaticRings(product)),
            "sp3_fraction": round(float(Descriptors.FractionCSP3(product)), 4),
            "stereocenters_defined": len(Chem.FindMolChiralCenters(product, includeUnassigned=False)),
            "stereocenters_unspecified": 0,
            "max_ring_size": _max_ring_size(product),
            "has_macrocycle": _max_ring_size(product) >= 12,
            "scaffold": _scaffold_of(rd, product),
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


def _scaffold_of(rd, molecule) -> str:
    try:
        return str(rd["scaffold"].MurckoScaffoldSmiles(mol=molecule))
    except Exception:
        return ""


def _formula(rd, molecule) -> str:
    try:
        return str(rd["descriptors"].CalcMolFormula(molecule))
    except Exception:
        return ""


def _max_ring_size(molecule) -> int:
    try:
        return max((len(ring) for ring in molecule.GetRingInfo().AtomRings()), default=0)
    except Exception:
        return 0


def draw_svg(molecule) -> str:
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

    Seeds must be real antibacterial compounds; broad-spectrum stress agents and
    immunosuppressants are skipped because deriving analogues from them would
    not be meaningful antibacterial chemistry.
    """
    wanted = set(target.get("sensitizing_compounds") or []) | set(target.get("resistance_compounds") or [])
    by_name = {item["curated_name"]: item for item in _screened_compounds(artifact)}
    return [by_name[name] for name in sorted(wanted) if name in by_name][:limit]


def _exploration_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    counts = artifact.get("counts", {})
    unresolved = [item.get("curated_name") for item in artifact.get("unresolved_compounds", [])]
    warnings = []
    if unresolved:
        warnings.append(
            "以下化合物未能在 ChEMBL 解析出结构，已从检索与生成基础中排除：" + "、".join(str(name) for name in unresolved) + "。"
        )
    dropped = counts.get("links_dropped_unresolved_compound")
    if dropped:
        warnings.append(f"另有 {dropped} 条策展记录因化合物无结构而未纳入。")
    if counts.get("hits_below_threshold"):
        warnings.append(
            f"{counts['hits_below_threshold']} 条 CARD 同源命中因未达到 "
            f"一致性≥60% 且覆盖≥80% 的门槛而未纳入。"
        )
    if counts.get("hits_without_compound_mapping"):
        warnings.append(
            f"{counts['hits_without_compound_mapping']} 条 CARD 命中所在药物类别尚未映射到具体化合物，"
            "只保留在耐药索引中，不作为候选化合物基础。"
        )
    return {"counts": counts, "unresolved_compounds": unresolved, "warnings": warnings}


def _genetic_evidence(artifact: dict[str, Any], uniprot: str) -> list[dict[str, Any]]:
    """All curated gene-compound links for one target, sorted for stable output."""
    rows = [link for link in artifact.get("links", []) if link.get("uniprot") == uniprot]
    # The bacterial spine is CARD sequence homology, whose only direction is
    # ``resistance_determinant``; the fungal sensitizing/resistance/neutral
    # order is kept as a fallback so a bundle carrying either vocabulary sorts
    # deterministically rather than crashing.
    order = {"resistance_determinant": 0, "sensitizing": 1, "resistance": 2, "neutral": 3}
    return sorted(rows, key=lambda item: (order.get(item.get("direction"), 4), item.get("curated_name", ""), item.get("reference", "")))


def explore_target(
    artifact: dict[str, Any],
    target: dict[str, Any],
    *,
    chembl: "BacterialChEMBL | None",
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
            # Prefer a species-level SINGLE PROTEIN hit whose organism actually
            # matches a target bacterial genus; never silently take the first
            # row when the lookup returns an organism-level grouping too.
            preferred = next(
                (item for item in candidates
                 if item.get("target_type") == "SINGLE PROTEIN"
                 and _organism_matches_bacteria(str(item.get("organism") or ""))),
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
    by_name = {item["curated_name"]: item for item in _screened_compounds(artifact)}
    touched = sorted({link["curated_name"] for link in genetic if link["curated_name"] in by_name})
    for name in touched:
        item = by_name[name]
        entry: dict[str, Any] = {
            "evidence_kind": "curated_chemogenomics",
            "curated_name": name,
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

    local_evidence = _local_target_activity_evidence(target)
    if local_evidence["activity_count"]:
        known_ids = {str(item.get("molecule_chembl_id") or "") for item in known}
        known.extend(
            item for item in local_evidence["known"]
            if str(item.get("molecule_chembl_id") or "") not in known_ids
        )
        direct_by_id = {str(item.get("molecule_chembl_id") or ""): item for item in direct}
        for item in local_evidence["direct"]:
            molecule_id = str(item.get("molecule_chembl_id") or "")
            if molecule_id not in direct_by_id:
                direct.append(item)
        resolution["local_activity_count"] = int(local_evidence["activity_count"])
        resolution["local_activity_sources"] = target.get("evidence_sources") or []

    # --- layer 3: similarity expansion around the curated compounds
    if chembl is not None and include_similarity:
        seen_ids = {entry["molecule_chembl_id"] for entry in known}
        # The curated compounds themselves come back as 100% hits; they are the
        # reference, not new candidates, so they are never listed here.
        reference_smiles = {entry.get("canonical_smiles") for entry in known if entry.get("canonical_smiles")}
        similarity_failures = 0
        for entry in known:
            if len(similar) >= MAX_SIMILARITY_PER_TARGET:
                break
            smiles = entry.get("canonical_smiles")
            if not smiles:
                continue
            try:
                payload = chembl.similarity(smiles, similarity_threshold)
            except Exception:
                similarity_failures += 1
                continue
            for hit in payload.get("molecules") or []:
                if len(similar) >= MAX_SIMILARITY_PER_TARGET:
                    break
                hit_id = str(hit.get("molecule_chembl_id") or "")
                if not hit_id or hit_id in seen_ids:
                    continue
                identity = _identity(hit)
                if not identity["canonical_smiles"] or identity["canonical_smiles"] in reference_smiles:
                    continue
                seen_ids.add(hit_id)
                similar.append({
                    "evidence_kind": "similarity_only",
                    "reference_curated_name": entry["curated_name"],
                    "reference_molecule_chembl_id": entry["molecule_chembl_id"],
                    "chembl_similarity": _number(hit.get("similarity")),
                    "rdkit_tanimoto": tanimoto(smiles, identity["canonical_smiles"]),
                    "descriptors": _descriptors_from_chembl(hit),
                    "structural_alerts": structural_alerts(identity["canonical_smiles"]),
                    "note": "相似性检索得到的分子，未检索到针对本靶标的实测活性。",
                    **identity,
                })
        if similarity_failures:
            warnings.append(f"{similarity_failures} 个参照化合物的相似性扩展请求失败。")
        if len(similar) >= MAX_SIMILARITY_PER_TARGET:
            warnings.append(f"相似性命中已达单靶标上限 {MAX_SIMILARITY_PER_TARGET} 条，其余未继续取回。")

    seeds = _seed_compounds(artifact, target)
    generation = (
        generate_compounds(seeds, max_generated, chembl)
        if include_generation
        else {"enabled": False, "reason": "本次未请求生成新化合物。", "capabilities": capabilities(),
              "compounds": [], "funnel": [], "rejected_by_reason": {}, "rules": GENERATION_RULES,
              "constraints_evaluated": [], "constraints_not_evaluated": NOT_EVALUATED,
              "novelty_scope": NOVELTY_SCOPE, "warnings": []}
    )
    _enrich_registry_records(similar)
    _enrich_registry_records(generation.get("compounds") or [])
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


def analyse_bacterial_compound_job(job: Job, chembl: "BacterialChEMBL | None" = None) -> dict[str, Any]:
    """Full result payload for one exploration job."""
    artifact = load_cgd_artifact()
    if artifact is None:
        raise BacterialCompoundError(
            "BACTERIAL_COMPOUND_DATA_NOT_CONFIGURED",
            "服务器尚未构建 CARD/PHI-base 策展证据文件，无法进行化合物探索。",
        )
    parameters = job.parameters or {}
    catalog = target_catalog()
    targets = catalog.get("targets", [])
    if not targets:
        raise BacterialCompoundError("BACTERIAL_COMPOUND_NO_TARGETS", "证据文件中没有可用的靶标。")

    identifiers = [str(item).strip() for item in (parameters.get("target_ids") or []) if str(item).strip()]
    if not identifiers:
        raise BacterialCompoundError("BACTERIAL_COMPOUND_TARGET_REQUIRED", "请选择至少一个靶标。")
    max_targets = int(parameters.get("max_targets") or 12)
    if len(identifiers) > max_targets:
        raise BacterialCompoundError(
            "BACTERIAL_COMPOUND_TOO_MANY_TARGETS", f"单次最多分析 {max_targets} 个靶标，请减少选择。",
        )

    include_similarity = bool(parameters.get("include_similarity", True))
    include_generation = bool(parameters.get("include_generation", True))
    similarity_threshold = int(parameters.get("similarity_threshold") or 70)
    if not 40 <= similarity_threshold <= 100:
        raise BacterialCompoundError("BACTERIAL_COMPOUND_SIMILARITY", "相似性阈值须在 40–100 之间。")
    max_generated = int(parameters.get("max_generated") or DEFAULT_MAX_GENERATED)
    if not 1 <= max_generated <= HARD_MAX_GENERATED:
        raise BacterialCompoundError("BACTERIAL_COMPOUND_MAX_GENERATED", f"生成数量须在 1–{HARD_MAX_GENERATED} 之间。")

    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for identifier in identifiers:
        uniprot = "" if identifier.upper().startswith(("CHEMBL",)) else identifier
        entry = _target_entry(catalog, uniprot, identifier)
        if entry is None:
            raise BacterialCompoundError(
                "BACTERIAL_COMPOUND_TARGET_NOT_FOUND",
                f"证据文件中没有与「{identifier}」匹配的靶标；请从靶标清单中选择。",
            )
        resolved.append((identifier, entry))

    # In proteome mode every target would otherwise run its own generation
    # pass; generation stays off there unless one target was chosen.
    generation_per_target = include_generation and len(resolved) == 1
    owned = chembl is None
    client = chembl or BacterialChEMBL()
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
        "organism": str(parameters.get("organism") or "Staphylococcus aureus NCTC 8325"),
        "data": {
            "configured": True,
            "artifact": str(_artifact_path()),
            "bundle_version": _bundle_version(),
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
            {"id": "genetic", "label": "CARD/PHI-base 策展证据", "kind": "curated", "direction": "genetic"},
            {"id": "measured", "label": "ChEMBL 直接结合实测", "kind": "measured"},
            {"id": "similarity", "label": "ChEMBL 相似性扩展", "kind": "similarity_only"},
            {"id": "generated", "label": "规则衍生新结构", "kind": "generated_unvalidated"},
        ],
        "boundary": (
            "本模块只汇总已有策展证据、检索真实化合物并给出规则枚举的新结构。"
            "耐药证据来自 CARD 的序列同源比对与 PHI-base 的策展表型记录，"
            "ChEMBL 直接结合证据来自体外测定，相似性命中不携带活性数据，"
            "生成结构没有任何活性、对接或 ADMET 预测。"
            "CARD 同源命中只表示该菌株携带同类耐药决定簇，不等同于已确认的自有基因功能。"
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


def _discovery_index_path() -> Path:
    return BACTERIAL_DISCOVERY_DATA_DIR / DISCOVERY_INDEX


def _discovery_index_manifest() -> dict[str, Any]:
    path = BACTERIAL_DISCOVERY_DATA_DIR / DISCOVERY_INDEX_MANIFEST
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _open_discovery_index() -> sqlite3.Connection | None:
    path = _discovery_index_path()
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        return None


def _host_homology_map(accessions: list[str]) -> dict[str, dict[str, Any]]:
    """Return the locally computed best human-proteome hit for target accessions."""
    wanted = sorted({value.strip().upper() for value in accessions if value.strip()})
    if not wanted:
        return {}
    connection = _open_discovery_index()
    if connection is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='host_homology'"
        ).fetchone():
            return {}
        for offset in range(0, len(wanted), 400):
            chunk = wanted[offset:offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""SELECT uniprot,human_uniprot,percent_identity,query_coverage,
                           subject_coverage,similarity_score,evalue,method
                    FROM host_homology WHERE upper(uniprot) IN ({placeholders})""",
                chunk,
            ).fetchall()
            for row in rows:
                result[str(row["uniprot"]).upper()] = {
                    "human_uniprot": row["human_uniprot"],
                    "human_identity": round(float(row["percent_identity"] or 0) / 100.0, 4),
                    "human_query_coverage": round(float(row["query_coverage"] or 0) / 100.0, 4),
                    "human_subject_coverage": round(float(row["subject_coverage"] or 0) / 100.0, 4),
                    "human_similarity": round(float(row["similarity_score"] or 0), 4),
                    "human_homology_evalue": float(row["evalue"] or 0),
                    "human_homology_method": row["method"],
                }
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    return result


def _activity_target_catalog(limit: int = 5000) -> list[dict[str, Any]]:
    """Expose locally indexed ChEMBL/BindingDB targets as searchable targets.

    The compact database has already applied the release's bacterial taxonomy
    policy.  One row is emitted per UniProt accession so gene-symbol ambiguity
    across species remains visible to the resolver instead of being silently
    collapsed.
    """
    connection = _open_discovery_index()
    if connection is None:
        return []
    try:
        rows = connection.execute(
            """SELECT t.target_key, t.preferred_name, t.organism, t.tax_id,
                      t.uniprot_accessions, t.gene_names, t.target_type,
                      t.source_list, t.evidence_count,
                      COUNT(DISTINCT a.compound_key) AS compound_count,
                      SUM(CASE WHEN a.direct_binding=1 THEN 1 ELSE 0 END) AS direct_binding_count
               FROM activity_targets t
               JOIN bacterial_activities a ON a.target_key=t.target_key
               GROUP BY t.target_key
               ORDER BY t.evidence_count DESC, t.preferred_name
               LIMIT ?""",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        connection.close()
        return []
    connection.close()

    combined: dict[str, dict[str, Any]] = {}
    for row in rows:
        accessions = _json_list(row["uniprot_accessions"])
        genes = _json_list(row["gene_names"])
        sources = _json_list(row["source_list"])
        for position, accession in enumerate(accessions):
            key = accession.upper()
            item = combined.setdefault(key, {
                "uniprot": accession,
                "gene_symbol": genes[min(position, len(genes) - 1)] if genes else "",
                "protein_name": row["preferred_name"],
                "organism": row["organism"],
                "tax_id": row["tax_id"],
                "target_type": row["target_type"],
                "compound_count": 0,
                "activity_evidence_count": 0,
                "direct_binding_count": 0,
                "sensitizing_compounds": [],
                "resistance_compounds": [],
                "evidence_sources": [],
                "activity_target_keys": [],
                "catalog_layer": "measured_activity",
            })
            item["compound_count"] += int(row["compound_count"] or 0)
            item["activity_evidence_count"] += int(row["evidence_count"] or 0)
            item["direct_binding_count"] += int(row["direct_binding_count"] or 0)
            item["evidence_sources"] = sorted(set([*item["evidence_sources"], *sources]))
            item["activity_target_keys"].append(str(row["target_key"]))
    return sorted(
        combined.values(),
        key=lambda item: (
            -int(item["direct_binding_count"]), -int(item["activity_evidence_count"]),
            str(item["organism"]), str(item["gene_symbol"]),
        ),
    )


def _local_target_activity_evidence(target: dict[str, Any], limit: int = 100) -> dict[str, Any]:
    """Return locally indexed measured compounds for one expanded target."""
    keys = [str(value) for value in target.get("activity_target_keys") or [] if str(value)]
    if not keys:
        return {"known": [], "direct": [], "activity_count": 0}
    connection = _open_discovery_index()
    if connection is None:
        return {"known": [], "direct": [], "activity_count": 0}
    placeholders = ",".join("?" for _ in keys)
    try:
        rows = connection.execute(
            f"""SELECT c.*, a.source, a.standard_type, a.pchembl_value,
                       a.direct_binding, a.assay_id, a.document_doi, a.pubmed_id
                FROM bacterial_activities a
                JOIN activity_compounds c ON c.compound_key=a.compound_key
                WHERE a.target_key IN ({placeholders})
                  AND c.chembl_id IS NOT NULL AND c.chembl_id != ''
                ORDER BY a.direct_binding DESC, a.pchembl_value DESC,
                         c.evidence_count DESC""",
            keys,
        ).fetchall()
    except sqlite3.Error:
        connection.close()
        return {"known": [], "direct": [], "activity_count": 0}
    connection.close()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["chembl_id"]), []).append(row)
    known: list[dict[str, Any]] = []
    direct: list[dict[str, Any]] = []
    for chembl_id, evidence_rows in list(grouped.items())[:limit]:
        row = evidence_rows[0]
        sources = sorted({str(value["source"]) for value in evidence_rows if value["source"]})
        activity_types = sorted({str(value["standard_type"]) for value in evidence_rows if value["standard_type"]})
        potencies = sorted(
            float(value["pchembl_value"]) for value in evidence_rows if value["pchembl_value"] is not None
        )
        has_direct = any(bool(value["direct_binding"]) for value in evidence_rows)
        known.append({
            "evidence_kind": "direct_binding_activity" if has_direct else "measured_activity",
            "curated_name": str(row["preferred_name"] or chembl_id),
            "class": "measured_bacterial_activity",
            "note": (
                f"本地 {' + '.join(sources)} 实测记录；"
                + ("包含直接结合证据。" if has_direct else "活性记录不自动等同于直接结合。")
            ),
            "molecule_chembl_id": chembl_id,
            "name": str(row["preferred_name"] or chembl_id),
            "canonical_smiles": str(row["canonical_smiles"] or ""),
            "inchi_key": str(row["standard_inchi_key"] or ""),
            "max_phase": None,
            "first_approval": None,
            "descriptors": {
                "molecular_weight": row["molecular_weight"],
                "logp": row["alogp"],
                "tpsa": row["psa"],
                "h_bond_donors": row["hbd"],
                "h_bond_acceptors": row["hba"],
                "rotatable_bonds": row["rtb"],
                "qed": None,
                "lipinski_violations": row["ro5_violations"],
                "descriptor_source": "ChEMBL 37 local compact index",
            },
            "mechanisms": [],
            "indications": [],
            "structural_alerts": structural_alerts(str(row["canonical_smiles"] or "")),
            "activity_types": activity_types,
            "activity_count": len(evidence_rows),
            "evidence_sources": sources,
        })
        if has_direct:
            direct.append({
                "evidence_kind": "measured_activity",
                "molecule_chembl_id": chembl_id,
                "pchembl_median": round(potencies[len(potencies) // 2], 3) if potencies else None,
                "pchembl_best": round(max(potencies), 3) if potencies else None,
                "activity_count": len(evidence_rows),
                "assay_count": len({str(value["assay_id"] or "") for value in evidence_rows}),
                "document_count": len({
                    str(value["document_doi"] or value["pubmed_id"] or "")
                    for value in evidence_rows if value["document_doi"] or value["pubmed_id"]
                }),
                "activity_types": activity_types,
                "sources": sources,
            })
    return {"known": known, "direct": direct, "activity_count": len(rows)}


def _registry_values(value: Any, pattern: str) -> list[str]:
    return sorted(set(re.findall(pattern, str(value or ""))))


def _chebi_row_payload(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    inchikey = str(row["inchikey"] or "").upper()
    cas_numbers = _registry_values(row["cas_ids"], r"\b\d{2,7}-\d{2}-\d\b")
    verified = VERIFIED_CAS_BY_INCHIKEY.get(inchikey) if not cas_numbers else None
    return {
        "chebi_id": row["chebi_id"],
        "canonical_smiles": row["smiles"],
        "inchi_key": row["inchikey"],
        "molecular_formula": row["formula"],
        "molecular_weight": row["mass"],
        "cas_numbers": cas_numbers or list((verified or {}).get("numbers") or []),
        "cas_source": (verified or {}).get("source") or "ChEBI SDF",
        "cas_source_label": (verified or {}).get("label") or "ChEBI",
        "pubchem_cids": _registry_values(row["pubchem_ids"], r"\b\d+\b"),
        "chebi_star": row["star"],
    }


def _chebi_record(connection: sqlite3.Connection, name: str = "", inchikey: str = "") -> dict[str, Any]:
    row = None
    if inchikey:
        row = connection.execute(
            "SELECT * FROM chebi_compounds WHERE upper(inchikey) = upper(?) LIMIT 1", (inchikey,),
        ).fetchone()
    if row is None and name:
        needle = " ".join(name.casefold().split())
        row = connection.execute(
            """SELECT c.* FROM chebi_compounds c
               JOIN chebi_terms t ON t.chebi_id = c.chebi_id
               WHERE t.term_norm = ? ORDER BY c.star DESC LIMIT 1""",
            (needle,),
        ).fetchone()
    return _chebi_row_payload(row)


def _enrich_registry_records(records: list[dict[str, Any]]) -> None:
    """Attach exact local registry matches; never infer a CAS number from a name."""
    connection = _open_discovery_index()
    try:
        for record in records:
            record.setdefault("cas_numbers", [])
            record.setdefault("pubchem_cids", [])
            record.setdefault("chebi_id", None)
            record["registry_lookup_scope"] = ["ChEBI exact InChIKey", "ChEMBL exact canonical SMILES"]
            inchikey = str(record.get("inchi_key") or "")
            chebi = _chebi_record(connection, inchikey=inchikey) if connection is not None and inchikey else {}
            if chebi:
                for key in (
                    "chebi_id", "cas_numbers", "cas_source", "cas_source_label", "pubchem_cids",
                    "molecular_formula", "molecular_weight",
                ):
                    if chebi.get(key) not in (None, "", []):
                        record[key] = chebi[key]
                record["registry_status"] = "exact_chebi_inchikey_match"
                record["cas_status"] = "registered" if record.get("cas_numbers") else "chebi_match_without_cas"
            else:
                record["registry_status"] = "not_found_in_connected_registries"
                record["cas_status"] = "not_assigned_or_not_found"
    finally:
        if connection is not None:
            connection.close()


def _indexed_source_status() -> dict[str, dict[str, Any]]:
    manifest = _discovery_index_manifest()
    return {
        str(item.get("id") or ""): item
        for item in manifest.get("sources", [])
        if isinstance(item, dict) and item.get("id")
    }


def _raw_source_status() -> dict[str, Any]:
    """Return download/index provenance without making raw files web-readable."""
    manifest_path = BACTERIAL_DISCOVERY_DATA_DIR / "manifest.json"
    if not manifest_path.is_file():
        return {"configured": False, "status": "not_configured", "sources": []}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"configured": False, "status": "invalid_manifest", "sources": []}
    indexed = _indexed_source_status()
    return {
        "configured": True,
        "bundle_version": manifest.get("bundle_version"),
        "generated_at": manifest.get("generated_at"),
        "status": manifest.get("status"),
        "counts": manifest.get("counts") or {},
        "index": _discovery_index_manifest().get("index") or {},
        "index_counts": _discovery_index_manifest().get("counts") or {},
        "sources": [
            {
                "id": item.get("id"), "name": item.get("name"), "version": item.get("version"),
                "status": item.get("status"), "bytes": item.get("bytes"), "license": item.get("license"),
                "role": item.get("role"),
                "index_status": (indexed.get(str(item.get("id") or "")) or {}).get("status"),
                "index_records": (indexed.get(str(item.get("id") or "")) or {}).get("records"),
            }
            for item in manifest.get("sources", [])
        ],
    }


def _phi_species_catalog(query: str, limit: int = 20) -> list[dict[str, Any]]:
    connection = _open_discovery_index()
    if connection is None:
        return []
    needle = " ".join(query.casefold().split())
    try:
        species_rows = connection.execute(
            """SELECT * FROM phi_species
               WHERE species_norm = ? OR species_norm LIKE ?
               ORDER BY CASE WHEN species_norm = ? THEN 0 ELSE 1 END,
                        record_count DESC, species
               LIMIT ?""",
            (needle, f"%{needle}%", needle, limit),
        ).fetchall()
        results = []
        for species_row in species_rows:
            taxid = str(species_row["tax_id"])
            compound_rows = connection.execute(
                """SELECT COALESCE(NULLIF(compound, ''), anti_infective) AS compound_name,
                          COALESCE(NULLIF(compound_norm, ''), anti_infective_norm) AS compound_key,
                          COUNT(*) AS association_count,
                          COUNT(DISTINCT COALESCE(NULLIF(protein_id, ''), NULLIF(gene_locus_id, ''), gene_name)) AS target_count
                   FROM phi_interactions
                   WHERE tax_id = ? AND (compound_norm <> '' OR anti_infective_norm <> '')
                   GROUP BY COALESCE(NULLIF(compound_norm, ''), anti_infective_norm)
                   ORDER BY association_count DESC, compound_name
                   LIMIT 100""",
                (taxid,),
            ).fetchall()
            target_rows = connection.execute(
                """SELECT protein_id, gene_locus_id, gene_name, function_text,
                          COUNT(*) AS evidence_count,
                          COUNT(DISTINCT COALESCE(NULLIF(compound_norm, ''), anti_infective_norm)) AS compound_count
                   FROM phi_interactions WHERE tax_id = ?
                   GROUP BY COALESCE(NULLIF(protein_id, ''), NULLIF(gene_locus_id, ''), gene_name)
                   ORDER BY evidence_count DESC LIMIT 500""",
                (taxid,),
            ).fetchall()
            strain_row = connection.execute(
                """SELECT strain, COUNT(*) AS n FROM phi_interactions
                   WHERE tax_id = ? AND strain <> '' GROUP BY strain ORDER BY n DESC LIMIT 1""",
                (taxid,),
            ).fetchone()
            strain = str(strain_row["strain"] if strain_row else "")
            known_compounds = []
            for row in compound_rows:
                if not row["compound_name"]:
                    continue
                chebi = _chebi_record(connection, str(row["compound_name"]))
                evidence_rows = connection.execute(
                    """SELECT record_id, protein_id, gene_locus_id, gene_name, phenotype,
                              experimental_evidence, literature_id, doi, function_text,
                              chemical_accession, compound, anti_infective
                       FROM phi_interactions
                       WHERE tax_id = ?
                         AND COALESCE(NULLIF(compound_norm, ''), anti_infective_norm) = ?
                       ORDER BY id LIMIT 500""",
                    (taxid, row["compound_key"]),
                ).fetchall()
                known_compounds.append({
                    "name": row["compound_name"], "pref_name": row["compound_name"],
                    "chembl_id": None, "class": "anti_infective",
                    "association_count": int(row["association_count"]),
                    "target_count": int(row["target_count"]), "directions": ["PHI-base 表型"],
                    "evidence_kind": "phi_base_antiinfective_phenotype",
                    "evidence_records": [{
                        "source": "PHI-base", "record_id": evidence["record_id"],
                        "compound": evidence["compound"] or evidence["anti_infective"] or row["compound_name"],
                        "uniprot": evidence["protein_id"] or evidence["gene_locus_id"] or evidence["gene_name"],
                        "gene_symbol": evidence["gene_name"] or evidence["gene_locus_id"],
                        "protein_name": evidence["function_text"], "direction": evidence["phenotype"],
                        "phenotype_text": evidence["phenotype"],
                        "experiment_type": evidence["experimental_evidence"],
                        "reference": evidence["doi"] or evidence["literature_id"],
                        "chemical_accession": evidence["chemical_accession"],
                        "relationship_label": "PHI-base 抗感染表型关联（非直接结合）",
                    } for evidence in evidence_rows],
                    **chebi,
                })
            results.append({
                "id": f"phi-{taxid}", "species": species_row["species"], "strain": strain,
                "organism": f"{species_row['species']} {strain}".strip(),
                "aliases": [species_row["species"], f"NCBI Taxonomy {taxid}"],
                "coverage": "phi_base_phenotype_evidence",
                "known_compound_count": len(known_compounds),
                "associated_target_count": int(species_row["gene_count"] or species_row["protein_count"]),
                "known_compounds": known_compounds,
                "targets": [{
                    "uniprot": row["protein_id"] or row["gene_locus_id"] or row["gene_name"],
                    "gene_symbol": row["gene_name"] or row["gene_locus_id"] or row["protein_id"],
                    "protein_name": row["function_text"], "compound_count": int(row["compound_count"]),
                    "evidence_count": int(row["evidence_count"]),
                } for row in target_rows],
                "phi_base_record_count": int(species_row["record_count"]),
                "ncbi_taxonomy_id": taxid,
            })
        return results
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _chebi_matches(query: str, limit: int = 20) -> list[dict[str, Any]]:
    connection = _open_discovery_index()
    if connection is None:
        return []
    needle = " ".join(query.casefold().split())
    try:
        rows = connection.execute(
            """SELECT DISTINCT c.* FROM chebi_compounds c
               LEFT JOIN chebi_terms t ON t.chebi_id = c.chebi_id
               WHERE lower(c.chebi_id) = ? OR lower(COALESCE(c.inchikey, '')) = ?
                  OR t.term_norm = ? OR (length(?) >= 4 AND t.term_norm LIKE ?)
               ORDER BY CASE WHEN t.term_norm = ? THEN 0 ELSE 1 END, c.star DESC, c.name
               LIMIT ?""",
            (needle, needle, needle, needle, f"{needle}%", needle, limit),
        ).fetchall()
        return [{
            "name": row["name"], "pref_name": row["name"], "chembl_id": None,
            "class": "chemical_entity", "source": "ChEBI", "star": row["star"],
            **_chebi_row_payload(row),
        } for row in rows]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _phi_compound_groups(terms: list[str], limit: int = 5000) -> list[dict[str, Any]]:
    normalized_terms = sorted({" ".join(term.casefold().split()) for term in terms if term.strip()})[:25]
    if not normalized_terms:
        return []
    connection = _open_discovery_index()
    if connection is None:
        return []
    placeholders = ",".join("?" for _ in normalized_terms)
    accession_predicate = " OR ".join("chemical_accession_norm LIKE ?" for _ in normalized_terms)
    parameters: list[Any] = [*normalized_terms, *normalized_terms, *[f"%{term}%" for term in normalized_terms], limit]
    try:
        rows = connection.execute(
            f"""SELECT * FROM phi_interactions
                 WHERE compound_norm IN ({placeholders})
                    OR anti_infective_norm IN ({placeholders})
                    OR ({accession_predicate})
                 LIMIT ?""",
            parameters,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        species = str(row["species"] or "")
        group = grouped.setdefault(species, {
            "organism": species, "species": species, "strain": str(row["strain"] or ""),
            "compound_matches": 1, "association_count": 0, "target_count": 0, "targets": [],
            "evidence_source": "PHI-base",
        })
        identifier = row["protein_id"] or row["gene_locus_id"] or row["gene_name"] or f"PHI-{row['id']}"
        compound = row["compound"] or row["anti_infective"] or normalized_terms[0]
        group["targets"].append({
            "compound": compound, "uniprot": identifier,
            "gene_symbol": row["gene_name"] or row["gene_locus_id"] or identifier,
            "protein_name": row["function_text"], "direction": row["phenotype"] or "PHI-base 表型",
            "phenotype_text": row["phenotype"], "experiment_type": row["experimental_evidence"],
            "reference": row["doi"] or row["literature_id"],
            "evidence_source": "PHI-base", "record_id": row["record_id"],
            "chemical_accession": row["chemical_accession"],
            "relationship": "phi_base_antiinfective_phenotype",
            "relationship_label": "PHI-base 抗感染表型关联（非直接结合）",
        })
        group["association_count"] += 1
    for group in grouped.values():
        unique_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in group["targets"]:
            key = (item["compound"], item["uniprot"], item["direction"])
            if key not in unique_by_key:
                item["evidence_count"] = 0
                item["references"] = []
                unique_by_key[key] = item
            aggregated = unique_by_key[key]
            aggregated["evidence_count"] += 1
            if item.get("reference") and item["reference"] not in aggregated["references"]:
                aggregated["references"].append(item["reference"])
        unique = list(unique_by_key.values())
        group["targets"] = unique
        group["association_count"] = len(unique)
        group["target_count"] = len({item["uniprot"] for item in unique if item["uniprot"]})
    return sorted(grouped.values(), key=lambda item: (-int(item["association_count"]), item["species"]))


def _json_list(value: Any) -> list[str]:
    """Read a compact-index JSON list without trusting malformed source text."""
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item or "").strip()]


def _mibig_identity(row: sqlite3.Row) -> dict[str, Any]:
    identifiers = _json_list(row["database_ids"])
    chembl_id = next((value.split(":", 1)[1].upper() for value in identifiers
                      if value.casefold().startswith("chembl:")), None)
    pubchem = [value.split(":", 1)[1] for value in identifiers
               if value.casefold().startswith("pubchem:")]
    activities = _json_list(row["activities"])
    return {
        "name": row["compound_name"], "pref_name": row["compound_name"],
        "chembl_id": chembl_id, "class": "antibacterial_metabolite",
        "canonical_smiles": row["canonical_smiles"],
        "molecular_formula": row["molecular_formula"],
        "molecular_weight": row["molecular_weight"], "pubchem_cids": pubchem,
        "source": "MIBiG 3.1", "mibig_accession": row["accession"],
        "activities": activities,
    }


def _mibig_species_catalog(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Expose bacterial natural products and annotated targets from MIBiG."""
    connection = _open_discovery_index()
    if connection is None:
        return []
    needle = " ".join(query.casefold().split())
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mibig_clusters'"
        ).fetchone():
            return []
        clusters = connection.execute(
            """SELECT * FROM mibig_clusters
               WHERE organism_norm=? OR organism_norm LIKE ? OR lower(accession)=?
               ORDER BY compound_count DESC, accession LIMIT ?""",
            (needle, f"%{needle}%", needle, limit),
        ).fetchall()
        if not clusters:
            return []
        accessions = [str(row["accession"]) for row in clusters]
        placeholders = ",".join("?" for _ in accessions)
        compounds = connection.execute(
            f"SELECT * FROM mibig_compounds WHERE accession IN ({placeholders}) ORDER BY compound_name",
            accessions,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    compounds_by_accession: dict[str, list[sqlite3.Row]] = {}
    for row in compounds:
        compounds_by_accession.setdefault(str(row["accession"]), []).append(row)
    grouped: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        organism_name = str(cluster["organism_name"] or "")
        words = organism_name.replace("(", " ").split()
        species = " ".join(words[:2]) if len(words) >= 2 else organism_name
        group = grouped.setdefault(species.casefold(), {
            "id": f"mibig-{hashlib.sha1(species.casefold().encode('utf-8')).hexdigest()[:12]}",
            "species": species, "strain": organism_name[len(species):].strip(),
            "organism": organism_name, "aliases": [species],
            "coverage": "mibig_biosynthetic_cluster", "known_compound_count": 0,
            "associated_target_count": 0, "known_compounds": [], "targets": [],
        })
        known_names = {str(item.get("canonical_smiles") or item.get("name") or "").casefold()
                       for item in group["known_compounds"]}
        known_targets = {str(item.get("gene_symbol") or "").casefold() for item in group["targets"]}
        for row in compounds_by_accession.get(str(cluster["accession"]), []):
            identity = _mibig_identity(row)
            targets = _json_list(row["targets"])
            key = str(identity.get("canonical_smiles") or identity["name"]).casefold()
            if key not in known_names:
                identity.update({
                    "association_count": max(1, len(targets)), "target_count": len(targets),
                    "directions": _json_list(row["activities"]),
                    "evidence_kind": "mibig_biosynthetic_cluster",
                    "evidence_records": [{
                        "source": "MIBiG", "record_id": row["accession"],
                        "compound": row["compound_name"], "gene_symbol": target or "BGC",
                        "protein_name": target or "天然产物生物合成基因簇",
                        "direction": "MIBiG 活性注释",
                        "phenotype_text": " / ".join(_json_list(row["activities"])),
                        "experiment_type": "MIBiG 策展的 BGC–天然产物关系",
                        "reference": row["accession"],
                        "relationship_label": "MIBiG 天然产物靶标注释（非直接结合测定）",
                    } for target in (targets or [""])],
                })
                group["known_compounds"].append(identity)
                known_names.add(key)
            for target in targets:
                target_key = target.casefold()
                if target_key in known_targets:
                    continue
                group["targets"].append({
                    "uniprot": f"MIBIG:{cluster['accession']}:{hashlib.sha1(target_key.encode('utf-8')).hexdigest()[:8]}",
                    "gene_symbol": target, "protein_name": "MIBiG 策展的天然产物靶标名称",
                    "compound_count": 1, "evidence_count": 1,
                })
                known_targets.add(target_key)
        group["known_compound_count"] = len(group["known_compounds"])
        group["associated_target_count"] = len(group["targets"])
    return sorted(grouped.values(), key=lambda item: (-int(item["known_compound_count"]), item["species"]))


def _mibig_compound_search(query: str, limit: int = 20) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection = _open_discovery_index()
    if connection is None:
        return [], []
    needle = " ".join(query.casefold().split())
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mibig_compounds'"
        ).fetchone():
            return [], []
        rows = connection.execute(
            """SELECT m.*,c.organism_name,c.ncbi_tax_id
               FROM mibig_compounds m JOIN mibig_clusters c ON c.accession=m.accession
               WHERE m.compound_norm=? OR m.compound_norm LIKE ?
                  OR lower(m.database_ids) LIKE ? OR lower(m.canonical_smiles)=?
               ORDER BY CASE WHEN m.compound_norm=? THEN 0 ELSE 1 END,m.compound_name LIMIT ?""",
            (needle, f"%{needle}%", f"%{needle}%", needle, needle, limit),
        ).fetchall()
    except sqlite3.Error:
        return [], []
    finally:
        connection.close()
    matches: list[dict[str, Any]] = []
    bacteria_index: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = _mibig_identity(row)
        matches.append(identity)
        organism = str(row["organism_name"] or "")
        words = organism.replace("(", " ").split()
        species = " ".join(words[:2]) if len(words) >= 2 else organism
        group = bacteria_index.setdefault(organism, {
            "organism": organism, "species": species,
            "strain": organism[len(species):].strip(), "compound_matches": 0,
            "association_count": 0, "target_count": 0, "targets": [],
        })
        group["compound_matches"] += 1
        targets = _json_list(row["targets"])
        for target in targets or ["BGC"]:
            group["targets"].append({
                "compound": row["compound_name"],
                "uniprot": f"MIBIG:{row['accession']}:{hashlib.sha1(target.casefold().encode('utf-8')).hexdigest()[:8]}",
                "gene_symbol": target, "protein_name": "MIBiG 策展的天然产物靶标名称",
                "direction": "MIBiG 活性注释",
                "phenotype_text": " / ".join(_json_list(row["activities"])),
                "experiment_type": "MIBiG 策展的 BGC–天然产物关系",
                "reference": row["accession"], "references": [str(row["accession"])],
                "relationship": "mibig_biosynthetic_cluster",
                "relationship_label": "MIBiG 天然产物靶标注释（非直接结合测定）",
                "evidence_source": "MIBiG", "record_id": row["accession"], "evidence_count": 1,
            })
    for group in bacteria_index.values():
        group["association_count"] = len(group["targets"])
        group["target_count"] = len({item["gene_symbol"].casefold() for item in group["targets"]})
    return matches, list(bacteria_index.values())


def _indexed_activity_search(query: str, limit: int = 20) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve locally indexed ChEMBL/BindingDB bacterial activity evidence.

    The activity index is optional so an older two-source bundle remains
    readable during an atomic index upgrade.  Only measured rows are returned;
    ChEMBL rows in this table have an exact bacterial target and high-confidence
    binding assay, while BindingDB Ki/Kd rows are labelled direct binding and
    IC50/EC50 rows remain measured activity.
    """
    connection = _open_discovery_index()
    if connection is None:
        return [], []
    needle = " ".join(query.casefold().split())
    exact = query.strip().upper()
    try:
        rows = connection.execute(
            """SELECT * FROM activity_compounds
               WHERE name_norm = ? OR upper(COALESCE(chembl_id, '')) = ?
                  OR upper(COALESCE(standard_inchi_key, '')) = ?
                  OR upper(COALESCE(chebi_id, '')) = ?
                  OR (length(?) >= 4 AND (name_norm LIKE ? OR aliases_norm LIKE ?))
               ORDER BY CASE
                  WHEN name_norm = ? THEN 0
                  WHEN upper(COALESCE(chembl_id, '')) = ? THEN 0
                  WHEN upper(COALESCE(standard_inchi_key, '')) = ? THEN 0
                  ELSE 1 END,
                  evidence_count DESC, preferred_name
               LIMIT ?""",
            (needle, exact, exact, exact, needle, f"{needle}%", f"%{needle}%",
             needle, exact, exact, limit),
        ).fetchall()
    except sqlite3.Error:
        connection.close()
        return [], []
    if not rows:
        connection.close()
        return [], []

    compounds: list[dict[str, Any]] = []
    keys = []
    for row in rows:
        keys.append(str(row["compound_key"]))
        sources = _json_list(row["source_list"])
        item = {
            "name": row["preferred_name"] or row["chembl_id"] or row["bindingdb_monomer_id"],
            "pref_name": row["preferred_name"], "chembl_id": row["chembl_id"],
            "bindingdb_monomer_id": row["bindingdb_monomer_id"],
            "chebi_id": row["chebi_id"], "pubchem_cids": _json_list(row["pubchem_cids"]),
            "canonical_smiles": row["canonical_smiles"], "inchi_key": row["standard_inchi_key"],
            "molecular_formula": row["molecular_formula"], "molecular_weight": row["molecular_weight"],
            "class": "measured_bacterial_activity", "source": " + ".join(sources),
            "activity_evidence_count": int(row["evidence_count"] or 0),
            "registry_lookup_scope": ["ChEMBL 37 local index", "BindingDB 2026-09 local index"],
        }
        chebi = _chebi_record(connection, str(row["preferred_name"] or ""), str(row["standard_inchi_key"] or ""))
        for field in ("cas_numbers", "cas_source", "cas_source_label"):
            if chebi.get(field) not in (None, "", []):
                item[field] = chebi[field]
        compounds.append(item)

    placeholders = ",".join("?" for _ in keys)
    try:
        activity_rows = connection.execute(
            f"""SELECT t.organism, t.tax_id, t.target_key, t.preferred_name AS target_name,
                       t.uniprot_accessions, t.gene_names, t.target_type,
                       c.compound_key, c.preferred_name AS compound_name, c.chembl_id,
                       a.source, a.assay_type, a.standard_type, a.standard_relation,
                       MIN(a.standard_value) AS min_value, MAX(a.standard_value) AS max_value,
                       a.standard_units, MAX(a.pchembl_value) AS max_pchembl,
                       MAX(a.direct_binding) AS direct_binding,
                       COUNT(*) AS evidence_count,
                       GROUP_CONCAT(DISTINCT NULLIF(a.document_doi, '')) AS dois,
                       GROUP_CONCAT(DISTINCT NULLIF(a.pubmed_id, '')) AS pmids
                FROM bacterial_activities a
                JOIN activity_targets t ON t.target_key = a.target_key
                JOIN activity_compounds c ON c.compound_key = a.compound_key
                WHERE a.compound_key IN ({placeholders})
                GROUP BY t.organism, t.target_key, c.compound_key, a.source,
                         a.assay_type, a.standard_type, a.standard_relation, a.standard_units
                ORDER BY direct_binding DESC, max_pchembl DESC, evidence_count DESC
                LIMIT 5000""",
            keys,
        ).fetchall()
    except sqlite3.Error:
        connection.close()
        return compounds, []
    finally:
        connection.close()

    grouped: dict[str, dict[str, Any]] = {}
    for row in activity_rows:
        organism = str(row["organism"] or "未注明细菌")
        species = " ".join(organism.replace("(", " ").split()[:2])
        group = grouped.setdefault(organism, {
            "organism": organism, "species": species, "strain": "",
            "compound_matches": 0, "association_count": 0, "target_count": 0,
            "targets": [], "evidence_source": "ChEMBL / BindingDB",
            "_compounds": set(), "_targets": set(),
        })
        compound_name = str(row["compound_name"] or row["chembl_id"] or row["compound_key"])
        uniprots = _json_list(row["uniprot_accessions"])
        genes = _json_list(row["gene_names"])
        min_value = row["min_value"]
        max_value = row["max_value"]
        units = str(row["standard_units"] or "")
        if min_value is None:
            measurement = str(row["standard_type"] or "实测活性")
        elif max_value is not None and float(max_value) != float(min_value):
            measurement = f"{row['standard_type']} {min_value:g}–{max_value:g} {units}".strip()
        else:
            measurement = f"{row['standard_type']} {min_value:g} {units}".strip()
        direct = bool(row["direct_binding"])
        references = []
        for field, prefix in ((row["dois"], "DOI:"), (row["pmids"], "PMID:")):
            for value in str(field or "").split(","):
                value = value.strip()
                if value and f"{prefix}{value}" not in references:
                    references.append(f"{prefix}{value}")
        group["targets"].append({
            "compound": compound_name, "chembl_id": row["chembl_id"],
            "uniprot": uniprots[0] if uniprots else row["target_key"],
            "uniprot_accessions": uniprots, "gene_symbol": genes[0] if genes else "",
            "protein_name": row["target_name"], "target_type": row["target_type"],
            "direction": measurement, "phenotype_text": measurement,
            "experiment_type": row["assay_type"], "reference": references[0] if references else "",
            "references": references, "relationship": "direct_binding_activity" if direct else "measured_activity",
            "relationship_label": "直接结合实测" if direct else "实测活性（不自动等于直接结合）",
            "evidence_source": row["source"], "evidence_count": int(row["evidence_count"] or 0),
            "pchembl_value": row["max_pchembl"],
        })
        group["_compounds"].add(row["compound_key"])
        group["_targets"].add(row["target_key"])
        group["association_count"] += int(row["evidence_count"] or 0)
    result = []
    for group in grouped.values():
        group["compound_matches"] = len(group.pop("_compounds"))
        group["target_count"] = len(group.pop("_targets"))
        result.append(group)
    result.sort(key=lambda item: (-int(item["target_count"]), -int(item["association_count"]), item["organism"]))
    return compounds, result


def _activity_species_catalog(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return organism-first ChEMBL/BindingDB evidence from the compact index."""
    connection = _open_discovery_index()
    if connection is None:
        return []
    needle = " ".join(query.casefold().split())
    try:
        organisms = connection.execute(
            """SELECT organism, organism_norm, tax_id, COUNT(DISTINCT target_key) AS target_count,
                      SUM(evidence_count) AS evidence_count
               FROM activity_targets
               WHERE organism_norm = ? OR organism_norm LIKE ?
               GROUP BY organism, organism_norm, tax_id
               ORDER BY CASE WHEN organism_norm = ? THEN 0 ELSE 1 END,
                        evidence_count DESC, organism
               LIMIT ?""",
            (needle, f"%{needle}%", needle, limit),
        ).fetchall()
    except sqlite3.Error:
        connection.close()
        return []
    result = []
    try:
        for organism_row in organisms:
            organism_norm = str(organism_row["organism_norm"])
            compound_rows = connection.execute(
                """SELECT c.*, COUNT(*) AS association_count,
                          COUNT(DISTINCT a.target_key) AS target_count,
                          GROUP_CONCAT(DISTINCT a.standard_type) AS activity_types,
                          MAX(a.direct_binding) AS has_direct_binding
                   FROM activity_targets t
                   JOIN bacterial_activities a ON a.target_key=t.target_key
                   JOIN activity_compounds c ON c.compound_key=a.compound_key
                   WHERE t.organism_norm=?
                   GROUP BY c.compound_key
                   ORDER BY association_count DESC, c.preferred_name
                   LIMIT 40""",
                (organism_norm,),
            ).fetchall()
            evidence_by_compound: dict[str, list[sqlite3.Row]] = {}
            compound_keys = [str(row["compound_key"]) for row in compound_rows]
            if compound_keys:
                placeholders = ",".join("?" for _ in compound_keys)
                evidence_rows = connection.execute(
                    f"""SELECT a.compound_key, a.source, a.source_record_id, a.standard_type,
                               a.standard_relation, a.standard_value, a.standard_units,
                               a.pchembl_value, a.direct_binding, a.document_doi, a.pubmed_id,
                               a.assay_type, t.preferred_name AS target_name,
                               t.uniprot_accessions, t.gene_names, t.target_key
                        FROM activity_targets t
                        JOIN bacterial_activities a ON a.target_key=t.target_key
                        WHERE t.organism_norm=? AND a.compound_key IN ({placeholders})
                        ORDER BY a.direct_binding DESC, a.pchembl_value DESC""",
                    [organism_norm, *compound_keys],
                ).fetchall()
                for evidence_row in evidence_rows:
                    bucket = evidence_by_compound.setdefault(str(evidence_row["compound_key"]), [])
                    if len(bucket) < 100:
                        bucket.append(evidence_row)
            registry_by_key: dict[str, dict[str, Any]] = {}
            inchikeys = sorted({str(row["standard_inchi_key"] or "").upper() for row in compound_rows if row["standard_inchi_key"]})
            chebi_ids = sorted({str(row["chebi_id"] or "").upper() for row in compound_rows if row["chebi_id"]})
            for values, field in ((inchikeys, "upper(inchikey)"), (chebi_ids, "chebi_id")):
                for offset in range(0, len(values), 400):
                    chunk = values[offset:offset + 400]
                    if not chunk:
                        continue
                    placeholders = ",".join("?" for _ in chunk)
                    for registry_row in connection.execute(
                        f"SELECT * FROM chebi_compounds WHERE {field} IN ({placeholders})", chunk,
                    ).fetchall():
                        payload = _chebi_row_payload(registry_row)
                        if registry_row["inchikey"]:
                            registry_by_key[str(registry_row["inchikey"]).upper()] = payload
                        registry_by_key[str(registry_row["chebi_id"]).upper()] = payload
            known_compounds = []
            for compound in compound_rows:
                evidence = []
                for row in evidence_by_compound.get(str(compound["compound_key"]), []):
                    uniprots = _json_list(row["uniprot_accessions"])
                    genes = _json_list(row["gene_names"])
                    relation = str(row["standard_relation"] or "")
                    measurement = str(row["standard_type"] or "实测活性")
                    if row["standard_value"] is not None:
                        measurement += f" {relation}{float(row['standard_value']):g} {row['standard_units'] or ''}".rstrip()
                    references = []
                    if row["document_doi"]:
                        references.append(f"DOI:{row['document_doi']}")
                    if row["pubmed_id"]:
                        references.append(f"PMID:{row['pubmed_id']}")
                    evidence.append({
                        "source": row["source"], "record_id": row["source_record_id"],
                        "compound": compound["preferred_name"] or compound["compound_key"],
                        "uniprot": uniprots[0] if uniprots else row["target_key"],
                        "gene_symbol": genes[0] if genes else "", "protein_name": row["target_name"],
                        "direction": measurement, "phenotype_text": measurement,
                        "experiment_type": row["assay_type"],
                        "reference": references[0] if references else "", "references": references,
                        "relationship_label": "直接结合实测" if row["direct_binding"] else "实测活性（不自动等于直接结合）",
                    })
                sources = _json_list(compound["source_list"])
                identity = {
                    "name": compound["preferred_name"] or compound["compound_key"],
                    "pref_name": compound["preferred_name"], "chembl_id": compound["chembl_id"],
                    "bindingdb_monomer_id": compound["bindingdb_monomer_id"],
                    "chebi_id": compound["chebi_id"], "pubchem_cids": _json_list(compound["pubchem_cids"]),
                    "class": "measured_bacterial_activity", "canonical_smiles": compound["canonical_smiles"],
                    "inchi_key": compound["standard_inchi_key"], "molecular_formula": compound["molecular_formula"],
                    "molecular_weight": compound["molecular_weight"], "source": " + ".join(sources),
                    "association_count": int(compound["association_count"] or 0),
                    "target_count": int(compound["target_count"] or 0),
                    "directions": [value for value in str(compound["activity_types"] or "").split(",") if value],
                    "evidence_kind": "direct_binding_activity" if compound["has_direct_binding"] else "measured_activity",
                    "evidence_records": evidence,
                }
                chebi = registry_by_key.get(str(compound["standard_inchi_key"] or "").upper()) or registry_by_key.get(str(compound["chebi_id"] or "").upper()) or {}
                for field in ("cas_numbers", "cas_source", "cas_source_label"):
                    if chebi.get(field) not in (None, "", []):
                        identity[field] = chebi[field]
                known_compounds.append(identity)
            target_rows = connection.execute(
                """SELECT * FROM activity_targets WHERE organism_norm=?
                   ORDER BY evidence_count DESC, preferred_name LIMIT 500""",
                (organism_norm,),
            ).fetchall()
            organism = str(organism_row["organism"] or "")
            parts = organism.replace("(", " ").split()
            species = " ".join(parts[:2])
            result.append({
                "id": "activity-" + hashlib.sha1(organism_norm.encode("utf-8")).hexdigest()[:12],
                "species": species, "strain": "", "organism": organism,
                "aliases": [species, f"NCBI Taxonomy {organism_row['tax_id']}"] if organism_row["tax_id"] else [species],
                "coverage": "measured_bacterial_activity", "known_compound_count": len(known_compounds),
                "associated_target_count": int(organism_row["target_count"] or 0),
                "known_compounds": known_compounds,
                "targets": [{
                    "uniprot": (_json_list(row["uniprot_accessions"]) or [row["target_key"]])[0],
                    "gene_symbol": (_json_list(row["gene_names"]) or [""])[0],
                    "protein_name": row["preferred_name"], "compound_count": 0,
                    "evidence_count": int(row["evidence_count"] or 0),
                } for row in target_rows],
            })
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return result


def _merge_organism_evidence(base: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge source layers by species so the UI keeps one expandable strain card."""
    by_species = {str(item.get("species") or "").casefold(): item for item in base}
    for incoming in additions:
        key = str(incoming.get("species") or "").casefold()
        existing = by_species.get(key)
        if existing is None:
            base.append(incoming)
            by_species[key] = incoming
            continue
        compounds = existing.setdefault("known_compounds", [])
        compound_index = {
            str(item.get("inchi_key") or item.get("chembl_id") or item.get("chebi_id") or item.get("name") or "").casefold(): item
            for item in compounds
        }
        for compound in incoming.get("known_compounds") or []:
            compound_key = str(compound.get("inchi_key") or compound.get("chembl_id") or compound.get("chebi_id") or compound.get("name") or "").casefold()
            current = compound_index.get(compound_key)
            if current is None:
                compounds.append(compound)
                compound_index[compound_key] = compound
                continue
            current["evidence_records"] = [*(current.get("evidence_records") or []), *(compound.get("evidence_records") or [])]
            current["association_count"] = int(current.get("association_count") or 0) + int(compound.get("association_count") or 0)
            current["target_count"] = max(int(current.get("target_count") or 0), int(compound.get("target_count") or 0))
            current["directions"] = sorted(set([*(current.get("directions") or []), *(compound.get("directions") or [])]))
            evidence_rank = {
                "curated_chemogenomic_association": 1, "phi_base_antiinfective_phenotype": 1,
                "measured_activity": 2, "direct_binding_activity": 3,
            }
            if evidence_rank.get(str(compound.get("evidence_kind") or ""), 0) > evidence_rank.get(str(current.get("evidence_kind") or ""), 0):
                current["evidence_kind"] = compound.get("evidence_kind")
            merged_sources = [value for value in (current.get("source"), compound.get("source")) if value]
            if merged_sources:
                current["source"] = " + ".join(dict.fromkeys(" + ".join(merged_sources).split(" + ")))
        targets = existing.setdefault("targets", [])
        known_target_keys = {str(item.get("uniprot") or item.get("gene_symbol") or "") for item in targets}
        for target in incoming.get("targets") or []:
            target_key = str(target.get("uniprot") or target.get("gene_symbol") or "")
            if target_key and target_key not in known_target_keys:
                targets.append(target)
                known_target_keys.add(target_key)
        existing["known_compound_count"] = len(compounds)
        existing["associated_target_count"] = len(known_target_keys)
        existing["coverage"] = "+".join(sorted(set(str(existing.get("coverage") or "").split("+") + [str(incoming.get("coverage") or "")])))
    return base


def _bacterium_catalog() -> dict[str, Any]:
    artifact = load_cgd_artifact()
    if artifact is None:
        return {"configured": False, "organisms": [], "raw_sources": _raw_source_status()}
    links = artifact.get("links") or []
    targets_by_uniprot = {str(item.get("uniprot") or ""): item for item in artifact.get("targets") or []}
    organisms_meta = artifact.get("organisms") or []
    species_by_slug = {str(item.get("slug") or item.get("id") or ""): item for item in organisms_meta}

    # Group curated links by organism so each reference strain is scored on its
    # own determinants.  Mixing strains would silently attribute e.g. an
    # E. coli beta-lactamase to S. aureus.
    links_by_slug: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        links_by_slug.setdefault(str(link.get("organism_slug") or ""), []).append(link)
    targets_by_slug: dict[str, dict[str, dict[str, Any]]] = {}
    for uniprot, target in targets_by_uniprot.items():
        targets_by_slug.setdefault(str(target.get("organism_slug") or ""), {})[uniprot] = target

    discovery_connection = _open_discovery_index()
    organisms: list[dict[str, Any]] = []
    for order, meta in enumerate(organisms_meta):
        slug = str(meta.get("slug") or meta.get("id") or "")
        slug_links = links_by_slug.get(slug, [])
        slug_targets = targets_by_slug.get(slug, {})
        if not slug_links and order != 0:
            continue
        compounds: list[dict[str, Any]] = []
        for drug in artifact.get("compounds") or []:
            name = str(drug.get("curated_name") or "")
            compound_links = [item for item in slug_links
                              if str(item.get("curated_name") or "").casefold() == name.casefold()]
            if not compound_links:
                continue
            chebi = _chebi_record(
                discovery_connection, name, str(drug.get("inchi_key") or ""),
            ) if discovery_connection is not None else {}
            compounds.append({
                "name": name,
                "pref_name": drug.get("pref_name"),
                "chembl_id": drug.get("chembl_id"),
                "class": drug.get("class"),
                "note": drug.get("note"),
                "canonical_smiles": drug.get("canonical_smiles"),
                "inchi_key": drug.get("inchi_key"),
                "association_count": len(compound_links),
                "target_count": len({str(item.get("uniprot") or "") for item in compound_links if item.get("uniprot")}),
                "directions": sorted({str(item.get("direction") or "neutral") for item in compound_links}),
                "evidence_kind": "card_resistance_determinant",
                "evidence_records": [{
                    "source": "CARD", "record_id": item.get("aro_accession"),
                    "compound": name, "uniprot": item.get("uniprot"),
                    "gene_symbol": item.get("gene_symbol") or (slug_targets.get(str(item.get("uniprot") or "")) or {}).get("gene_symbol"),
                    "protein_name": (slug_targets.get(str(item.get("uniprot") or "")) or {}).get("protein_name"),
                    "direction": item.get("direction"), "phenotype_text": item.get("phenotype_text"),
                    "experiment_type": item.get("experiment_type"), "mutant_type": item.get("mutant_type"),
                    "reference": item.get("reference"),
                    "relationship_label": "CARD 序列同源耐药关联（非直接结合）",
                } for item in compound_links],
                **chebi,
            })
        organisms.append({
            "id": slug,
            "species": meta.get("species"),
            "strain": meta.get("strain"),
            "organism": meta.get("organism"),
            "aliases": meta.get("aliases") or [],
            "coverage": meta.get("coverage") or "full_curated_case",
            "known_compound_count": len(compounds),
            "associated_target_count": len(slug_targets),
            "known_compounds": sorted(compounds, key=lambda item: (-int(item["association_count"]), str(item["name"]))),
            "targets": sorted(slug_targets.values(), key=lambda item: (-int(item.get("compound_count") or 0), str(item.get("gene_symbol") or ""))),
        })
    if discovery_connection is not None:
        discovery_connection.close()
    return {
        "configured": True,
        "bundle_version": _bundle_version(),
        "organisms": organisms,
        "raw_sources": _raw_source_status(),
        "boundary": (
            "已知化合物来自 CARD 的序列同源耐药关联与 PHI-base 的策展表型；"
            "CARD 同源命中表示该菌株携带同类耐药决定簇，不等同于化合物与蛋白直接结合，"
            "也不等同于该基因已被实验确认为耐药蛋白。未知化合物为后续结构生成或相似性扩展候选，必须实验验证。"
        ),
    }


def _match_compound(compound: dict[str, Any], query: str) -> bool:
    needle = query.casefold().strip()
    if not needle:
        return False
    fields = (
        compound.get("curated_name"), compound.get("pref_name"), compound.get("chembl_id"),
        compound.get("inchi_key"), compound.get("canonical_smiles"),
    )
    return any(needle in str(value or "").casefold() for value in fields)


def _standardize_structure(molecule: Any) -> tuple[Any, dict[str, Any]]:
    """Build the neutral parent used for matching while disclosing every change."""
    rd = _rdkit()
    if rd is None:
        raise ValueError("服务器结构检索组件暂不可用。")
    Chem = rd["Chem"]
    input_fragments = len(Chem.GetMolFrags(molecule))
    raw_isomeric = str(Chem.MolToSmiles(molecule, isomericSmiles=True, canonical=True))
    transformations: list[str] = []
    warnings: list[str] = []
    try:
        from rdkit.Chem.MolStandardize import rdMolStandardize

        standardized = rdMolStandardize.Cleanup(molecule)
        if str(Chem.MolToSmiles(standardized, isomericSmiles=True, canonical=True)) != raw_isomeric:
            transformations.append("官能团与电荷清理")
        if input_fragments > 1:
            standardized = rdMolStandardize.FragmentParent(standardized, skipStandardize=True)
            transformations.append("去盐/母体组分选择")
            warnings.append(
                f"原始输入含 {input_fragments} 个组分；检索仅使用 RDKit 选择的母体组分，请核对盐型或混合物处理是否符合实验语境。"
            )
        before_uncharge = str(Chem.MolToSmiles(standardized, isomericSmiles=True, canonical=True))
        standardized = rdMolStandardize.Uncharger().uncharge(standardized)
        if str(Chem.MolToSmiles(standardized, isomericSmiles=True, canonical=True)) != before_uncharge:
            transformations.append("中和可中和电荷")
        before_tautomer = str(Chem.MolToSmiles(standardized, isomericSmiles=True, canonical=True))
        tautomer_enumerator = rdMolStandardize.TautomerEnumerator()
        # RDKit defaults may remove stereo while enumerating tautomers.  The
        # target-search input must preserve experimentally meaningful @/@@ and
        # double-bond stereo unless the changed tautomer makes it impossible.
        tautomer_enumerator.SetRemoveSp3Stereo(False)
        tautomer_enumerator.SetRemoveBondStereo(False)
        tautomer_enumerator.SetReassignStereo(True)
        tautomer_enumerator.SetMaxTautomers(256)
        tautomer_enumerator.SetMaxTransforms(256)
        standardized = tautomer_enumerator.Canonicalize(standardized)
        if str(Chem.MolToSmiles(standardized, isomericSmiles=True, canonical=True)) != before_tautomer:
            transformations.append("规范互变异构体")
    except (ImportError, AttributeError) as exc:
        if input_fragments > 1:
            raise ValueError("服务器缺少多组分标准化组件；请先去除盐、溶剂和较小组分。") from exc
        standardized = molecule
        warnings.append("当前 RDKit 环境未提供完整 MolStandardize；本次仅执行规范 SMILES 转换。")

    model_isomeric = str(Chem.MolToSmiles(standardized, isomericSmiles=True, canonical=True))
    if not transformations:
        transformations.append("结构校验（无需改写）")
    elif model_isomeric != raw_isomeric and input_fragments == 1:
        warnings.append("标准化后的模型结构与原始结构表示不同；结果中同时保留两者供核对。")
    return standardized, {
        "profile": "rdkit_cleanup_fragment_parent_uncharge_canonical_tautomer_v1",
        "input_fragment_count": input_fragments,
        "transformations": transformations,
        "warnings": warnings,
        "raw_isomeric_smiles": raw_isomeric,
    }


def _parse_structure_query(value: str, structure_format: str = "auto") -> tuple[Any, dict[str, Any]]:
    """Parse one user-supplied structure without guessing identity from a name."""
    if len(value.encode("utf-8")) > 256 * 1024:
        raise ValueError("结构输入超过 256 KB，请只提交一个分子。")
    rd = _rdkit()
    if rd is None:
        raise ValueError("服务器结构检索组件暂不可用。")
    Chem = rd["Chem"]
    raw = value.strip()
    if not raw:
        raise ValueError("请粘贴 SMILES / InChI，或上传 MOL / SDF 文件。")
    selected = structure_format.casefold().strip()
    molecule = None
    try:
        if selected in {"mol", "sdf"} or "M  END" in raw:
            # Molfile line positions are significant; keep leading blank/title
            # lines and trim only the SDF separator/trailing whitespace.
            block = value.split("$$$$", 1)[0].rstrip() + "\n"
            molecule = Chem.MolFromMolBlock(block, sanitize=True, removeHs=True)
        elif selected == "inchi" or raw.startswith("InChI="):
            molecule = Chem.MolFromInchi(raw, sanitize=True, removeHs=True)
        else:
            molecule = Chem.MolFromSmiles(raw)
    except Exception:
        molecule = None
    if molecule is None:
        raise ValueError("结构无法解析；请检查 SMILES、InChI 或 MOL/SDF 内容。")
    molecule, normalization = _standardize_structure(molecule)
    heavy_atoms = int(molecule.GetNumHeavyAtoms())
    if heavy_atoms < 2 or heavy_atoms > 200:
        raise ValueError("结构须包含 2–200 个重原子。")
    canonical = str(Chem.MolToSmiles(molecule, isomericSmiles=False, canonical=True))
    isomeric = str(Chem.MolToSmiles(molecule, isomericSmiles=True, canonical=True))
    identity = {
        "name": "输入的未知结构",
        "canonical_smiles": canonical,
        "isomeric_smiles": isomeric,
        "model_input_smiles": isomeric,
        "raw_isomeric_smiles": normalization["raw_isomeric_smiles"],
        "normalization": normalization,
        "inchi_key": str(Chem.MolToInchiKey(molecule) or "") if hasattr(Chem, "MolToInchiKey") else "",
        "molecular_formula": _formula(rd, molecule),
        "molecular_weight": round(float(rd["Descriptors"].MolWt(molecule)), 3),
        "heavy_atoms": heavy_atoms,
        "svg": draw_svg(molecule),
        "source": "用户结构输入 + RDKit MolStandardize",
    }
    _enrich_registry_records([identity])
    return molecule, identity


def _structure_evidence_candidates(artifact: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Structures that already have a CARD or PHI-base evidence relationship."""
    candidates: list[dict[str, Any]] = []
    for item in (artifact or {}).get("compounds") or []:
        if item.get("canonical_smiles"):
            candidates.append({
                "name": item.get("curated_name") or item.get("pref_name") or item.get("chembl_id"),
                "pref_name": item.get("pref_name"), "chembl_id": item.get("chembl_id"),
                "class": item.get("class"), "canonical_smiles": item.get("canonical_smiles"),
                "inchi_key": item.get("inchi_key"), "source": "CARD + ChEMBL",
            })
    if candidates:
        _enrich_registry_records(candidates)
    connection = _open_discovery_index()
    if connection is not None:
        try:
            # Resolve the small evidence vocabulary first.  Joining the full
            # ChEBI and PHI tables with lower()/OR expressions caused a table
            # scan on every request; bounded IN queries use the existing PKs.
            accessions = {
                str(row[0] or "").upper()
                for row in connection.execute(
                    "SELECT DISTINCT chemical_accession_norm FROM phi_interactions WHERE chemical_accession_norm != ''"
                ).fetchall()
                if str(row[0] or "").upper().startswith("CHEBI:")
            }
            terms = {
                str(value or "")
                for row in connection.execute(
                    "SELECT DISTINCT compound_norm, anti_infective_norm FROM phi_interactions"
                ).fetchall()
                for value in row
                if str(value or "")
            }
            rows_by_id: dict[str, sqlite3.Row] = {}
            for values, sql in (
                (sorted(accessions), "SELECT * FROM chebi_compounds WHERE chebi_id IN ({}) AND smiles IS NOT NULL AND smiles != ''"),
                (sorted(terms), """SELECT DISTINCT c.* FROM chebi_compounds c
                    JOIN chebi_terms t ON t.chebi_id = c.chebi_id
                    WHERE t.term_norm IN ({}) AND c.smiles IS NOT NULL AND c.smiles != ''"""),
            ):
                for offset in range(0, len(values), 400):
                    chunk = values[offset:offset + 400]
                    if not chunk:
                        continue
                    placeholders = ",".join("?" for _ in chunk)
                    for row in connection.execute(sql.format(placeholders), chunk).fetchall():
                        rows_by_id[str(row["chebi_id"])] = row
            for row in rows_by_id.values():
                payload = _chebi_row_payload(row)
                if payload.get("canonical_smiles"):
                    candidates.append({"name": row["name"], "pref_name": row["name"], "class": "chemical_entity", **payload, "source": "ChEBI + PHI-base"})
        finally:
            connection.close()
    return candidates


@lru_cache(maxsize=10000)
def _fingerprint_candidate(smiles: str) -> tuple[str, Any] | None:
    rd = _rdkit()
    generator = _morgan()
    if rd is None or generator is None:
        return None
    molecule = rd["Chem"].MolFromSmiles(smiles)
    if molecule is None:
        return None
    # Evidence candidates already come from curated canonical registry fields.
    # Re-running full tautomer enumeration here is both redundant and capable
    # of turning a cold request into seconds of combinatorial work.
    canonical = str(rd["Chem"].MolToSmiles(molecule, isomericSmiles=True, canonical=True))
    return canonical, generator.GetFingerprint(molecule)


def _rank_structure_candidates(
    query_molecule: Any,
    candidates: list[dict[str, Any]],
    threshold: float,
    limit: int,
) -> list[dict[str, Any]]:
    rd = _rdkit()
    generator = _morgan()
    if rd is None or generator is None:
        return []
    from rdkit import DataStructs

    query_fp = generator.GetFingerprint(query_molecule)
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        smiles = str(candidate.get("canonical_smiles") or "")
        fingerprint = _fingerprint_candidate(smiles) if smiles else None
        if fingerprint is None:
            continue
        canonical, candidate_fp = fingerprint
        if canonical in seen:
            continue
        seen.add(canonical)
        score = float(DataStructs.TanimotoSimilarity(query_fp, candidate_fp))
        if score + 1e-12 < threshold:
            continue
        exact = score >= 0.999999
        ranked.append({
            **candidate,
            "canonical_smiles": canonical,
            "structure_similarity": round(score, 4),
            "match_type": "exact_structure" if exact else "similar_structure",
            "similarity_method": "Morgan radius 2 / 2048 bits / chirality + Tanimoto",
        })
    ranked.sort(key=lambda item: (-float(item["structure_similarity"]), str(item.get("name") or "")))
    return ranked[:limit]


@router.get("/bacterial-discovery/organisms")
def get_bacterial_discovery_organisms(q: str = "", user: UserContext = Depends(get_current_user)):
    catalog = _bacterium_catalog()
    index_counts = (_discovery_index_manifest().get("counts") or {})
    catalog["indexed_organism_count"] = index_counts.get("bacterial_species", 0)
    if not q.strip():
        return catalog
    needle = q.casefold().strip()
    curated = [
        item for item in catalog.get("organisms", [])
        if any(needle in str(value).casefold() for value in [item.get("organism"), item.get("species"), item.get("strain"), *(item.get("aliases") or [])])
    ]
    seen_species = {str(item.get("species") or "").casefold() for item in curated}
    indexed = [
        item for item in _phi_species_catalog(q)
        if str(item.get("species") or "").casefold() not in seen_species
    ]
    organisms = [*curated, *indexed]
    activity = _activity_species_catalog(q)
    mibig = _mibig_species_catalog(q)
    catalog["organisms"] = _merge_organism_evidence(
        _merge_organism_evidence(organisms, activity), mibig,
    )
    if indexed or activity or mibig:
        catalog["boundary"] = (
            "PHI-base 扩展条目来自细菌基因扰动、毒力和宿主互作表型；ChEMBL 高置信结合测定"
            "与 BindingDB Ki/Kd 另列为直接结合，IC50/EC50 只列为实测活性；MIBiG 提供经策展的"
            "细菌生物合成基因簇、天然产物及文本靶标注释，不自动等同于直接结合。只有完整策展案例"
            "才开放结构候选生成。"
        )
    return catalog


@router.get("/bacterial-discovery/compound-search")
def search_bacterial_compound(query: str, user: UserContext = Depends(get_current_user)):
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="请输入至少 2 个字符的化合物名称、ChEMBL ID、InChIKey 或 SMILES。")
    artifact = load_cgd_artifact()
    compounds = [item for item in (artifact or {}).get("compounds") or [] if _match_compound(item, query)][:20]
    names = {str(item.get("curated_name") or "").casefold() for item in compounds}
    links = [item for item in (artifact or {}).get("links") or [] if str(item.get("curated_name") or "").casefold() in names]
    target_index = {str(item.get("uniprot") or ""): item for item in (artifact or {}).get("targets") or []}
    association_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for link in links:
        key = (str(link.get("curated_name") or ""), str(link.get("uniprot") or ""), str(link.get("direction") or ""))
        target = target_index.get(key[1]) or {}
        if key not in association_index:
            association_index[key] = {
                "compound": key[0], "uniprot": key[1],
                "gene_symbol": link.get("gene_symbol") or target.get("gene_symbol"),
                "protein_name": target.get("protein_name"), "direction": link.get("direction"),
                "phenotype_text": link.get("phenotype_text"), "experiment_type": link.get("experiment_type"),
                "reference": link.get("reference"), "relationship": "chemogenomic_association",
                "evidence_source": "CARD", "record_id": link.get("aro_accession") or link.get("reference"),
                "organism_slug": link.get("organism_slug"),
                "relationship_label": "CARD 序列同源耐药关联（非直接结合）",
                "evidence_count": 0, "references": [],
            }
        association = association_index[key]
        association["evidence_count"] += 1
        if link.get("reference") and link["reference"] not in association["references"]:
            association["references"].append(link["reference"])
    associations = list(association_index.values())
    chebi_matches = _chebi_matches(query)
    phi_terms = [query]
    for item in chebi_matches:
        phi_terms.extend([str(item.get("name") or ""), str(item.get("chebi_id") or "")])
    bacteria = []
    if compounds:
        target_slugs = {str(item.get("organism_slug") or "") for item in links}
        for meta in (artifact or {}).get("organisms") or []:
            slug = str(meta.get("slug") or meta.get("id") or "")
            slug_associations = [item for item in associations
                                 if str(item.get("organism_slug") or "") == slug]
            if not slug_associations:
                continue
            bacteria.append({
                "organism": meta.get("organism"),
                "species": meta.get("species"), "strain": meta.get("strain"),
                "compound_matches": len(compounds),
                "association_count": len(slug_associations),
                "target_count": len({item["uniprot"] for item in slug_associations if item["uniprot"]}),
                "targets": sorted(slug_associations, key=lambda item: (str(item.get("gene_symbol") or ""), str(item.get("compound") or ""))),
            })
        if not bacteria and target_slugs:
            # Older bundles carry no organism metadata; fall back to a single
            # entry rather than dropping the curated evidence.
            bacteria.append({
                "organism": str((artifact or {}).get("organism") or ""),
                "species": "", "strain": "",
                "compound_matches": len(compounds),
                "association_count": len(associations),
                "target_count": len({item["uniprot"] for item in associations if item["uniprot"]}),
                "targets": sorted(associations, key=lambda item: (str(item.get("gene_symbol") or ""), str(item.get("compound") or ""))),
            })
    bacteria.extend(_phi_compound_groups(phi_terms))
    activity_matches, activity_bacteria = _indexed_activity_search(query)
    bacteria.extend(activity_bacteria)
    mibig_matches, mibig_bacteria = _mibig_compound_search(query)
    bacteria.extend(mibig_bacteria)
    matches = [{
        "name": item.get("curated_name"), "pref_name": item.get("pref_name"), "chembl_id": item.get("chembl_id"),
        "class": item.get("class"), "canonical_smiles": item.get("canonical_smiles"), "inchi_key": item.get("inchi_key"),
        "source": "CARD + ChEMBL",
    } for item in compounds]
    if matches and chebi_matches:
        for match in matches:
            aliases = {
                str(match.get("name") or "").casefold(),
                str(match.get("pref_name") or "").casefold(),
            }
            chebi = next(
                (item for item in chebi_matches if str(item.get("name") or "").casefold() in aliases),
                None,
            )
            if chebi:
                for key in (
                    "chebi_id", "molecular_formula", "molecular_weight", "cas_numbers",
                    "cas_source", "cas_source_label", "pubchem_cids", "inchi_key", "canonical_smiles",
                ):
                    if chebi.get(key) not in (None, "", []):
                        match[key] = chebi.get(key)
                match["source"] = "CARD + ChEMBL + ChEBI"
    elif not matches:
        matches = chebi_matches
    seen_match_keys = {
        str(item.get("inchi_key") or item.get("chembl_id") or item.get("chebi_id") or item.get("name") or "").casefold()
        for item in matches
    }
    for item in activity_matches:
        key = str(item.get("inchi_key") or item.get("chembl_id") or item.get("chebi_id") or item.get("name") or "").casefold()
        if key and key not in seen_match_keys:
            matches.append(item)
            seen_match_keys.add(key)
    for item in mibig_matches:
        key = str(item.get("inchi_key") or item.get("chembl_id") or item.get("chebi_id") or item.get("canonical_smiles") or item.get("name") or "").casefold()
        if key and key not in seen_match_keys:
            matches.append(item)
            seen_match_keys.add(key)
    return {
        "configured": bool(artifact or _discovery_index_manifest()), "query": query,
        "matches": matches,
        "bacteria": bacteria, "raw_sources": _raw_source_status(),
        "boundary": (
            "CARD/PHI-base 策展证据与 PHI-base 抗感染表型关系均不自动等于直接分子靶标；"
            "ChEBI 只用于化合物身份、同义词和结构标准化。ChEMBL 高置信结合测定与 BindingDB "
            "Ki/Kd 标为直接结合；IC50/EC50 只标为实测活性，不自动升级为直接靶标。MIBiG 的"
            "天然产物与靶标字段属于生物合成基因簇策展注释，也不自动升级为直接结合证据。"
        ),
    }


@router.post("/bacterial-discovery/structure-search")
def search_bacterial_compound_structure(
    payload: StructureSearchRequest,
    user: UserContext = Depends(get_current_user),
):
    if not 0.40 <= payload.similarity_threshold <= 1.0:
        raise HTTPException(status_code=422, detail="结构相似度阈值须在 0.40–1.00 之间。")
    if not 1 <= payload.max_results <= 30:
        raise HTTPException(status_code=422, detail="结构命中数须在 1–30 之间。")
    if payload.format.casefold() not in {"auto", "smiles", "inchi", "mol", "sdf"}:
        raise HTTPException(status_code=422, detail="结构格式仅支持 SMILES、InChI、MOL 或 SDF。")
    try:
        query_molecule, query_identity = _parse_structure_query(payload.structure, payload.format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    artifact = load_cgd_artifact()
    matches = _rank_structure_candidates(
        query_molecule,
        _structure_evidence_candidates(artifact),
        payload.similarity_threshold,
        payload.max_results,
    )
    bacterium_index: dict[str, dict[str, Any]] = {}
    for match in matches:
        # The preferred name joins both the curated CARD artifact and PHI-base;
        # a registry-only identifier can otherwise hide the CARD strain record.
        lookup = str(match.get("name") or match.get("chembl_id") or match.get("chebi_id") or "")
        if len(lookup) < 2:
            continue
        evidence = search_bacterial_compound(query=lookup, user=user)
        for bacterium in evidence.get("bacteria") or []:
            organism = str(bacterium.get("organism") or "")
            grouped = bacterium_index.setdefault(organism, {
                "organism": organism,
                "species": bacterium.get("species"), "strain": bacterium.get("strain"),
                "compound_matches": 0, "association_count": 0, "target_count": 0,
                "targets": [], "_compounds": set(), "_target_ids": set(), "_rows": set(),
            })
            match_name = str(match.get("pref_name") or match.get("name") or lookup)
            grouped["_compounds"].add(match_name)
            for target in bacterium.get("targets") or []:
                row = dict(target)
                row["matched_reference_compound"] = match_name
                row["structure_similarity"] = match["structure_similarity"]
                row["structure_match_type"] = match["match_type"]
                row["similarity_method"] = match["similarity_method"]
                if match["match_type"] != "exact_structure":
                    row["reference_relationship_label"] = row.get("relationship_label")
                    row["relationship"] = "structure_similarity_inference"
                    row["relationship_label"] = f"结构相似候选（证据属于 {match_name}）"
                    row["evidence_source"] = f"RDKit 结构相似 + {row.get('evidence_source') or '策展证据'}"
                key = (
                    str(row.get("uniprot") or ""), str(row.get("direction") or ""),
                    match_name, str(row.get("relationship") or ""),
                )
                if key in grouped["_rows"]:
                    continue
                grouped["_rows"].add(key)
                grouped["targets"].append(row)
                if row.get("uniprot"):
                    grouped["_target_ids"].add(str(row["uniprot"]))

    bacteria = []
    for grouped in bacterium_index.values():
        grouped["targets"].sort(key=lambda item: (-float(item.get("structure_similarity") or 0), str(item.get("gene_symbol") or "")))
        grouped["compound_matches"] = len(grouped.pop("_compounds"))
        grouped["association_count"] = len(grouped["targets"])
        grouped["target_count"] = len(grouped.pop("_target_ids"))
        grouped.pop("_rows")
        bacteria.append(grouped)
    bacteria.sort(key=lambda item: (-int(item["target_count"]), str(item["organism"])))

    return {
        "configured": bool(artifact or _discovery_index_manifest()),
        "search_mode": "structure_similarity",
        "query_structure": query_identity,
        "similarity_threshold": payload.similarity_threshold,
        "matches": matches,
        "bacteria": bacteria,
        "method": {
            "fingerprint": "Morgan/ECFP-like circular fingerprint",
            "radius": 2, "bits": 2048, "chirality": True, "metric": "Tanimoto",
            "standardization": query_identity.get("normalization", {}).get("profile"),
            "candidate_scope": "仅搜索当前已接入 CARD 或 PHI-base 证据关系且具有可解析结构的化合物",
        },
        "raw_sources": _raw_source_status(),
        "boundary": (
            "精确结构命中可展示该登记结构已有的策展关系；相似结构命中只用于候选优先级，"
            "其细菌与靶标证据属于参照化合物，不证明输入的未知结构具有相同活性、选择性或直接结合。"
        ),
    }


@router.get("/bacterial-discovery/compound-structure")
def get_bacterial_discovery_compound_structure(
    chebi_id: str, user: UserContext = Depends(get_current_user),
):
    if not re.fullmatch(r"CHEBI:\d{1,12}", chebi_id.upper()):
        raise HTTPException(status_code=422, detail="无效的 ChEBI 标识。")
    connection = _open_discovery_index()
    if connection is None:
        raise HTTPException(status_code=503, detail="化合物结构索引尚未配置。")
    try:
        row = connection.execute(
            "SELECT name, smiles FROM chebi_compounds WHERE chebi_id = ?", (chebi_id.upper(),),
        ).fetchone()
    finally:
        connection.close()
    if row is None or not row["smiles"]:
        raise HTTPException(status_code=404, detail="该 ChEBI 记录没有可用结构。")
    rd = _rdkit()
    molecule = rd["Chem"].MolFromSmiles(str(row["smiles"])) if rd is not None else None
    svg = draw_svg(molecule) if molecule is not None else ""
    if not svg:
        raise HTTPException(status_code=503, detail="二维结构图暂不可生成。")
    return Response(
        svg, media_type="image/svg+xml",
        headers={
            "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        },
    )


@router.get("/bacterial-compounds/targets")
def get_bacterial_compound_targets(user: UserContext = Depends(get_current_user)):
    return _targets_payload()


@router.post("/bacterial-compounds/resolve-target")
def resolve_bacterial_target(payload: ResolveRequest, user: UserContext = Depends(get_current_user)):
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


@router.get("/jobs/{job_id}/bacterial-compound-result")
def get_bacterial_compound_result(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job or job.type != "bacterial_compound":
        raise HTTPException(status_code=404, detail="未找到化合物探索任务。")
    require_project_access(db, job.project_id, user)
    if job.status != JobStatus.completed.value or not job.output_ref:
        raise HTTPException(status_code=409, detail="化合物探索任务尚未完成。")
    try:
        return json.loads(read_storage_bytes(job.output_ref, MAX_RESULT_BYTES))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="化合物探索结果文件无法读取。") from exc


def process_bacterial_compound(db: Session, job: Job) -> dict:
    """Job processor: explore targets, persist the result, report provenance."""
    client = BacterialChEMBL()
    try:
        result = analyse_bacterial_compound_job(job, client)
        failures = client.failures
    finally:
        client.close()
    result.update({"job_id": job.id, "project_id": job.project_id})
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    storage_uri, digest = save_asset(job.project_id, f"bacterial-compound-{job.id}.json", payload)
    job.output_ref = storage_uri
    generated = sum(len(item["generated_compounds"].get("compounds") or []) for item in result["targets"])
    return {
        "adapter": "BacterialCompoundExplorationAdapter",
        "model_name": "细菌靶标到化合物的探索与规则衍生",
        "model_version": MODEL_VERSION,
        "provenance": {
            "data_mode": "real",
            "evidence": "CARD/PHI-base 策展证据 + ChEMBL 检索 + RDKit 规则枚举",
            "bundle_version": _bundle_version(),
            "chembl_db_version": result["data"].get("chembl_db_version"),
            "targets": len(result["targets"]),
            "generated_compounds": generated,
            "chembl_request_failures": failures,
            "output_sha256": digest,
            "scientific_conclusion": False,
        },
        "demo": False,
    }
