"""Project-scoped optimization loop over a versioned public HTE snapshot."""
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, JSON, String, Integer, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import UserContext, get_current_user, require_project_access
from .database import Base, get_db
from .models import AuditEvent, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/hte")


class HteSession(Base):
    __tablename__ = "process_hte_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionInput(Strict):
    dataset_id: Literal["buchwald-hartwig-48"] = "buchwald-hartwig-48"
    method: Literal["bayesian", "random"] = "bayesian"


class StepInput(Strict):
    revision: int
    row_index: int


@lru_cache(maxsize=1)
def dataset() -> dict:
    path = Path(__file__).with_name("data") / "hte_buchwald_hartwig_subset.json"
    return json.loads(path.read_text(encoding="utf-8"))


def dataset_summary() -> dict:
    d = dataset()
    return {k: d[k] for k in ("id", "name", "description", "reaction", "target", "source", "fixed", "legends")} | {"size": len(d["rows"])}


def encode_rows(rows: list[list]) -> list[list[float]]:
    d = dataset()
    orders = [list(d["legends"][name]) for name in ("配体", "添加剂", "碱")]
    return [[float(value == level) / math.sqrt(2) for levels, value in zip(orders, row[:3]) for level in levels] for row in rows]


def next_index(session_id: str, data: dict) -> tuple[int | None, str, float | None, float | None]:
    import numpy as np

    rows = dataset()["rows"]
    used = {item["row_index"] for item in data["observations"]}
    available = [i for i in range(len(rows)) if i not in used]
    if not available:
        return None, "全部公开实验已读取", None, None
    if data["method"] == "random":
        seed = int(hashlib.sha256(f"{session_id}:{len(used)}".encode()).hexdigest()[:16], 16)
        return random.Random(seed).choice(available), "随机选择，用来比较智能推荐是否更有效", None, None
    x = np.asarray(encode_rows(rows), dtype=float)
    if len(used) < 4:
        if not used:
            index = available[0]
        else:
            chosen = x[sorted(used)]
            distance = ((x[:, None, :] - chosen[None, :, :]) ** 2).sum(axis=2).min(axis=1)
            index = max(available, key=lambda i: (float(distance[i]), -i))
        return index, "优先选择差异较大的条件，先了解整个可选范围", None, None
    observed = data["observations"]
    train_i = [item["row_index"] for item in observed]
    train_x = x[train_i]
    y = np.asarray([item["yield"] for item in observed], dtype=float)
    kernel = lambda a, b: np.exp(-((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2) / (2 * .8 ** 2))
    k = kernel(train_x, train_x) + np.eye(len(train_x)) * .08
    try:
        alpha = np.linalg.solve(k, y)
        cross = kernel(x, train_x)
        mean = cross @ alpha
        solved = np.linalg.solve(k, cross.T)
        sd = np.sqrt(np.maximum(1 - (cross * solved.T).sum(axis=1), 1e-8)) * max(float(y.std()), 8.0)
        improvement = mean - float(y.max()) - .01
        z = improvement / sd
        cdf = np.asarray([.5 * (1 + math.erf(float(v) / math.sqrt(2))) for v in z])
        pdf = np.exp(-.5 * z ** 2) / math.sqrt(2 * math.pi)
        ei = improvement * cdf + sd * pdf
        index = max(available, key=lambda i: (float(ei[i]), -i))
        return index, "兼顾较高收率的可能性和还没有充分尝试的条件", round(float(mean[index]), 2), round(float(sd[index]), 2)
    except np.linalg.LinAlgError:
        index = available[0]
        return index, "当前预测不稳定，改为优先覆盖更多不同条件", None, None


def output(session: HteSession) -> dict:
    d = dataset()
    index, model, mean, sd = next_index(session.id, session.data)
    observations = session.data["observations"]
    best = max(observations, key=lambda item: item["yield"], default=None)
    suggestion = None if index is None else {
        "row_index": index,
        "conditions": dict(zip(("配体", "添加剂", "碱"), d["rows"][index][:3])),
        "model": model,
        "predicted_yield": mean,
        "uncertainty": sd,
    }
    return {"id": session.id, "revision": session.revision, "method": session.data["method"],
            "dataset": dataset_summary(), "observations": observations, "best": best, "next": suggestion,
            "remaining": len(d["rows"]) - len(observations)}


@router.get("/datasets")
def datasets(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [dataset_summary()]


@router.get("/sessions")
def sessions(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [output(item) for item in db.scalars(select(HteSession).where(HteSession.project_id == project_id).order_by(HteSession.id)).all()]


@router.post("/sessions")
def create_session(project_id: str, payload: SessionInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    if len(db.scalars(select(HteSession.id).where(HteSession.project_id == project_id)).all()) >= 20:
        raise HTTPException(422, "每个项目最多保留 20 个公开实验演示")
    item = HteSession(project_id=project_id, data={"dataset_id": payload.dataset_id, "method": payload.method, "observations": [], "created_at": datetime.now(timezone.utc).isoformat()})
    db.add(item); db.flush()
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.hte_created", entity_type="process_hte_session", entity_id=item.id, details=item.data))
    db.commit(); db.refresh(item)
    return output(item)


@router.post("/sessions/{session_id}/step")
def execute_step(project_id: str, session_id: str, payload: StepInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    item = db.get(HteSession, session_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "公开实验会话不存在")
    if item.revision != payload.revision:
        raise HTTPException(409, "记录已更新，请刷新后继续")
    expected, model, mean, sd = next_index(item.id, item.data)
    if expected is None or payload.row_index != expected:
        raise HTTPException(409, "推荐条件已经变化，请重新加载")
    row = dataset()["rows"][expected]
    observation = {"round": len(item.data["observations"]) + 1, "row_index": expected,
                   "conditions": dict(zip(("配体", "添加剂", "碱"), row[:3])), "yield": row[3],
                   "recommended_by": model, "predicted_yield": mean, "uncertainty": sd}
    new_data = {**item.data, "observations": item.data["observations"] + [observation], "updated_at": datetime.now(timezone.utc).isoformat()}
    changed = db.execute(update(HteSession).where(HteSession.id == item.id, HteSession.revision == payload.revision).values(data=new_data, revision=payload.revision + 1))
    if changed.rowcount != 1:
        db.rollback(); raise HTTPException(409, "记录已更新，请刷新后继续")
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.hte_executed", entity_type="process_hte_session", entity_id=item.id, details=observation))
    db.commit(); db.refresh(item)
    return output(item)
