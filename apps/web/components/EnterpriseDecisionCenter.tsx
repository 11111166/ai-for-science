import { ArrowRight, CheckCircle2, CircleAlert, Clock3, Gauge, ShieldCheck } from "lucide-react";
import type { DecisionGate, ProjectDecisionBrief } from "@/lib/decisionSupport";

const statusIcon = (gate: DecisionGate) => gate.status === "complete" ? <CheckCircle2 /> : gate.status === "attention" ? <CircleAlert /> : <Clock3 />;

export function EnterpriseDecisionCenter({ brief, go }: { brief: ProjectDecisionBrief; go: (view: DecisionGate["view"]) => void }) {
  return <section className="enterprise-decision-center">
    <header>
      <div><span><Gauge size={15} /> 项目决策驾驶舱</span><h3>{brief.headline}</h3><p>{brief.recommendation}</p></div>
      <div className="readiness-ring" style={{ "--readiness": `${brief.readiness * 3.6}deg` } as React.CSSProperties}><b>{brief.readiness}%</b><small>决策就绪度</small></div>
    </header>
    <div className="decision-gate-grid">{brief.gates.map((gate, index) => <button key={gate.id} className={gate.status} onClick={() => go(gate.view)}>
      <span className="gate-index">{String(index + 1).padStart(2, "0")}</span>
      <i>{statusIcon(gate)}</i>
      <strong>{gate.label}</strong>
      <em>{gate.statusLabel}</em>
      <small>{gate.evidence}</small>
      {gate.status !== "complete" && <p>{gate.action}</p>}
    </button>)}</div>
    <footer>
      <div><ShieldCheck /><p><strong>当前阻塞项</strong><span>{brief.blockers.length ? brief.blockers.join("；") : "没有系统内阻塞项，可进入正式项目评审。"}</span></p></div>
      <button onClick={() => go(brief.nextView)}>{brief.readiness === 100 ? "生成决策报告" : "处理下一项"}<ArrowRight size={16} /></button>
    </footer>
  </section>;
}
