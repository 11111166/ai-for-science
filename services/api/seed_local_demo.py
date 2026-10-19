"""Create the local demonstration project used by the browser preview."""
import os, sys
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("DATABASE_URL", "sqlite:///./drug_discovery.db")
sys.path.insert(0, os.getcwd())
from sqlalchemy import select
from app.database import Base, SessionLocal, engine
from app.models import Project, ProjectMember

Base.metadata.create_all(engine)
with SessionLocal() as db:
    existing = db.scalar(select(Project).where(Project.name == "L-苯丙氨酸 酶法转化—结晶精制 工艺开发演示"))
    if existing:
        print("EXISTS", existing.id)
        raise SystemExit(0)
    project = Project(name="L-苯丙氨酸 酶法转化—结晶精制 工艺开发演示", disease="苯丙酮尿症（PKU）",
                      disease_id="MONDO_0019218", owner_id="researcher",
                      objective="演示：从实验条件到概念 PFD、CAD 图纸与 SVG/DXF 导出的完整链路。")
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id="researcher", role="owner"))
    db.commit()
    print("CREATED", project.id)