"""Project-owned molecules and measured validation results; no external structure uploads."""
import csv
from functools import lru_cache
import io
import sys
from pathlib import Path

# Release-local chemistry dependencies keep the shared API runtime unchanged.
_chemdeps = Path(__file__).resolve().parent.parent / "_chemdeps"
if _chemdeps.is_dir():
    sys.path.insert(0, str(_chemdeps))
from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from .database import Base, get_db
from .models import Compound, AuditEvent, new_id
from .auth import UserContext, get_current_user, require_project_access

router = APIRouter(prefix='/api/v1/drug-discovery/projects/{project_id}/workbench')

class LibraryMolecule(Base):
    __tablename__ = 'library_molecules'
    __table_args__ = (UniqueConstraint('project_id', 'smiles'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(180))
    smiles: Mapped[str] = mapped_column(String(2000))
    details: Mapped[dict] = mapped_column(JSON)

class ValidationTask(Base):
    __tablename__ = 'molecule_validation_tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    data: Mapped[dict] = mapped_column(JSON)


@lru_cache(maxsize=1)
def load_render_libraries():
    import ctypes
    native = _chemdeps / 'native' / 'usr' / 'lib64'
    if native.is_dir():
        for name in ('libXau.so.6', 'libxcb.so.1', 'libX11.so.6', 'libXext.so.6', 'libXrender.so.1'):
            ctypes.CDLL(str(native / name), mode=ctypes.RTLD_GLOBAL)


def chemistry(smiles, depict=True):
    load_render_libraries()
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
    from rdkit.Chem.Scaffolds import MurckoScaffold
    from rdkit.Chem.Draw import rdMolDraw2D
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumAtoms() == 0 or mol.GetNumAtoms() > 250:
        raise ValueError('结构无效或超过250个原子')
    canonical = Chem.MolToSmiles(mol, isomericSmiles=True)
    svg = ""
    if depict:
        drawing = rdMolDraw2D.MolDraw2DSVG(240, 180)
        rdMolDraw2D.PrepareAndDrawMolecule(drawing, mol)
        drawing.FinishDrawing()
        svg = drawing.GetDrawingText()
    return mol, {'svg': svg, 'smiles': canonical, 'formula': rdMolDescriptors.CalcMolFormula(mol),
        'weight': round(Descriptors.MolWt(mol), 2), 'logp': round(Descriptors.MolLogP(mol), 2),
        'scaffold': MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=True),
        'fragments': len(Chem.GetMolFrags(mol)),
        'unspecified_stereo': sum(label == '?' for _, label in Chem.FindMolChiralCenters(mol, includeUnassigned=True))}


def parse_import(content, filename):
    from rdkit import Chem
    if len(content) > 2_000_000:
        raise HTTPException(413, '单次文件不超过2MB')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(422, '请使用UTF-8编码')
    if filename.lower().endswith('.sdf'):
        molecules = list(Chem.ForwardSDMolSupplier(io.BytesIO(content)))
        rows = [{'name': m.GetProp('_Name') if m is not None and m.HasProp('_Name') else '', 'smiles': Chem.MolToSmiles(m) if m is not None else ''} for m in molecules]
    elif filename.lower().endswith('.csv'):
        rows = [{str(k).strip().lower(): v for k, v in row.items() if k is not None} for row in csv.DictReader(io.StringIO(text))]
    else:
        rows = [{'smiles': line.split()[0], 'name': ' '.join(line.split()[1:])} for line in text.splitlines() if line.strip()]
    if not rows or len(rows) > 200:
        raise HTTPException(422, '每次导入1–200个分子；CSV需包含name、smiles列')
    result = []
    seen = set()
    for index, row in enumerate(rows, 1):
        try:
            smiles = str(row.get('smiles') or '').strip()
            if len(smiles) > 2000:
                raise ValueError('SMILES过长')
            _, data = chemistry(smiles)
            duplicate = data['smiles'] in seen
            seen.add(data['smiles'])
            result.append({'row': index, 'name': str(row.get('name') or f'自有分子 {index}')[:180], **data, 'duplicate': duplicate})
        except (ValueError, RuntimeError):
            result.append({'row': index, 'error': '结构无法解析，请核对SMILES或SDF记录'})
    return result

@router.post('/import')
def import_molecules(project_id: str, commit: bool = False, file: UploadFile = File(...), db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, 'member')
    rows = parse_import(file.file.read(2_000_001), file.filename or '')
    existing = set(db.scalars(select(LibraryMolecule.smiles).where(LibraryMolecule.project_id == project_id)).all())
    for row in rows:
        if not row.get('error') and row['smiles'] in existing:
            row['duplicate'] = True
    accepted = [r for r in rows if not r.get('error') and not r['duplicate']]
    if len(existing) + len(accepted) > 1000:
        raise HTTPException(422, '每个项目最多保存1000个自有分子')
    if commit:
        for row in accepted:
            db.add(LibraryMolecule(project_id=project_id, name=row['name'], smiles=row['smiles'], details=row))
        db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="library.imported", entity_type="project", entity_id=project_id, details={"accepted": len(accepted)}))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "其他操作已导入相同分子，请重新检查文件")
    return {'rows': rows, 'accepted': len(accepted), 'saved': len(accepted) if commit else 0}

@router.get('/molecules')
def list_molecules(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [{'id': m.id, 'name': m.name, **m.details} for m in db.scalars(select(LibraryMolecule).where(LibraryMolecule.project_id == project_id)).all()]

class Search(BaseModel):
    smiles: str = Field(min_length=1, max_length=2000)
    mode: Literal['similarity', 'scaffold', 'substructure'] = 'similarity'

@router.post('/search')
def search(project_id: str, payload: Search, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    from rdkit import DataStructs
    from rdkit.Chem import rdFingerprintGenerator
    try:
        query, q = chemistry(payload.smiles, depict=False)
    except (ValueError, RuntimeError):
        raise HTTPException(422, '查询结构无效')
    fp = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
    qfp = fp.GetFingerprint(query)
    own = db.scalars(select(LibraryMolecule).where(LibraryMolecule.project_id == project_id)).all()
    public = db.scalars(select(Compound).where(Compound.project_id == project_id).order_by(Compound.created_at.desc()).limit(1000)).all()
    candidates = [(m.id, m.name, m.smiles, '自有分子') for m in own] + [(m.identifier, m.name, (m.details or {}).get('canonical_smiles', ''), '项目公开分子') for m in public]
    results, seen = [], set()
    skipped = 0
    for identifier, name, smiles, origin in candidates:
        if (identifier, origin) in seen:
            continue
        seen.add((identifier, origin))
        try:
            mol, detail = chemistry(smiles, depict=False)
        except (ValueError, RuntimeError):
            skipped += 1
            continue
        same = bool(q['scaffold']) and detail['scaffold'] == q['scaffold']
        if payload.mode == 'scaffold' and not same:
            continue
        if payload.mode == 'substructure' and not mol.HasSubstructMatch(query, useChirality=True):
            continue
        results.append({'id': identifier, 'name': name, 'origin': origin, **detail,
            'similarity': round(DataStructs.TanimotoSimilarity(qfp, fp.GetFingerprint(mol)), 3), 'same_scaffold': same,
            'weight_delta': round(detail['weight'] - q['weight'], 2), 'logp_delta': round(detail['logp'] - q['logp'], 2)})
    results.sort(key=lambda r: (-r['similarity'], r['id']))
    for item in results[:50]:
        item['svg'] = chemistry(item['smiles'])[1]['svg']
    return {'query': q, 'items': results[:50], 'matched': len(results), 'skipped': skipped, 'searched': len(seen)}

class TaskInput(BaseModel):
    molecule: str = Field(min_length=1, max_length=180)
    target: str = Field(min_length=1, max_length=100)
    endpoint: Literal['IC50', 'EC50', 'Ki', 'Kd', '细胞活性', '溶解度']
    unit: Literal['nM', 'µM', '%', 'mg/mL']
    criterion: Literal['<=', '>=']
    threshold: float = Field(ge=0, allow_inf_nan=False)
    model: str = Field(min_length=1, max_length=500)
    status: Literal['planned', 'running', 'completed'] = 'planned'
    measured: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    predicted: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    notes: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def check(self):
        if self.status == 'completed' and self.measured is None:
            raise ValueError('完成实验需要实测值')
        if self.endpoint in ('IC50', 'EC50', 'Ki', 'Kd') and self.unit not in ('nM', 'µM'):
            raise ValueError('活性终点需使用摩尔浓度单位')
        if self.endpoint == '细胞活性' and self.unit != '%':
            raise ValueError('细胞活性使用百分比')
        if self.endpoint == '溶解度' and self.unit != 'mg/mL':
            raise ValueError('溶解度使用mg/mL')
        return self


def task_result(row):
    data = row.data
    measured = data.get('measured')
    met = measured is not None and (measured <= data['threshold'] if data['criterion'] == '<=' else measured >= data['threshold'])
    outcome = '待验证' if data['status'] != 'completed' else ('达到本项阈值；优先补充独立复测与选择性验证' if met else '未达本项阈值；优先复核实验条件，暂缓据此推进')
    return {'id': row.id, **data, 'outcome': outcome}

@router.get('/tasks')
def list_tasks(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [task_result(t) for t in db.scalars(select(ValidationTask).where(ValidationTask.project_id == project_id)).all()]

@router.post('/tasks')
def create_task(project_id: str, payload: TaskInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, 'member')
    row = ValidationTask(project_id=project_id, data=payload.model_dump())
    db.add(row); db.flush()
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="validation.created", entity_type="validation_task", entity_id=row.id, details=payload.model_dump()))
    db.commit(); db.refresh(row)
    return task_result(row)

@router.put('/tasks/{task_id}')
def update_task(project_id: str, task_id: str, payload: TaskInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, 'member')
    row = db.get(ValidationTask, task_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, '未找到本项目验证任务')
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="validation.updated", entity_type="validation_task", entity_id=row.id, details={"before": row.data, "after": payload.model_dump()}))
    row.data = payload.model_dump(); db.commit()
    return task_result(row)

@router.post('/task-results')
def import_results(project_id: str, commit: bool = False, file: UploadFile = File(...), db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, 'member')
    content = file.file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(413, '文件不超过2MB')
    try:
        rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
    except (UnicodeDecodeError, csv.Error):
        raise HTTPException(422, '请使用UTF-8 CSV文件')
    if not rows or len(rows) > 200:
        raise HTTPException(422, '每次回填1–200条实验结果')
    checked, updates, seen = [], [], set()
    for index, data in enumerate(rows, 1):
        task_id = str(data.get('task_id') or '').strip()
        row = db.get(ValidationTask, task_id)
        if row is None or row.project_id != project_id or task_id in seen:
            checked.append({'row': index, 'error': '任务不存在、非本项目或文件内重复'})
            continue
        seen.add(task_id)
        try:
            measured = float(data.get('measured', ''))
            payload = TaskInput(**{**row.data, 'status': 'completed', 'measured': measured, 'notes': data.get('notes') or row.data.get('notes', '')})
        except (ValueError, TypeError):
            checked.append({'row': index, 'error': '实测值须为非负有限数值，备注不超过2000字'})
            continue
        checked.append({'row': index, 'name': row.data['molecule'], 'measured': measured, 'unit': row.data['unit']})
        updates.append((row, payload.model_dump()))
    if commit:
        for row, data in updates:
            db.add(AuditEvent(project_id=project_id, actor_id=user.id, action='validation.result_imported', entity_type='validation_task', entity_id=row.id, details={'before': row.data, 'after': data}))
            row.data = data
        db.commit()
    return {'rows': checked, 'accepted': len(updates), 'saved': len(updates) if commit else 0}


@router.get('/task-results-template')
def result_template(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    rows = db.scalars(select(ValidationTask).where(ValidationTask.project_id == project_id)).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['task_id', 'measured', 'notes'])
    for row in rows:
        writer.writerow([row.id, '', ''])
    return Response(('\ufeff'+output.getvalue()).encode('utf-8'), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="experiment-results.csv"'})
