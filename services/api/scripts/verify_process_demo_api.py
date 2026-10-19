"""Isolated end-to-end verification for the process demo case and CAD sheet API.

Runs against a staging API bound to a database copy. Verifies that the shipped
demonstration case fills every input field, that the concept CAD sheet renders
as SVG and DXF, and that project isolation still holds for the new routes.

Usage: python verify_process_demo_api.py http://127.0.0.1:19133/api/v1/drug-discovery
"""
from __future__ import annotations

import json
import sys

import httpx

DISCLAIMER = "非施工图或已验证 P&ID"
PENDING = "待补"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(base_url: str) -> int:
    api = base_url.rstrip("/")
    headers = {"X-Dev-User": "demo-verify-owner", "X-Dev-Role": "researcher"}
    other = {"X-Dev-User": "demo-verify-other", "X-Dev-Role": "researcher"}
    summary: dict = {}
    with httpx.Client(timeout=60.0) as client:
        mine = client.post(f"{api}/projects", json={"name": "演示验收项目 A", "disease": "工艺验收", "objective": "演示案例与图纸预览验收"}, headers=headers)
        check(mine.status_code == 201, f"项目创建失败 {mine.status_code} {mine.text[:200]}")
        project_id = mine.json()["id"]
        theirs = client.post(f"{api}/projects", json={"name": "演示验收项目 B", "disease": "工艺验收", "objective": "权限隔离验收"}, headers=other)
        check(theirs.status_code == 201, f"第二个项目创建失败 {theirs.status_code}")
        other_project_id = theirs.json()["id"]

        base = f"{api}/projects/{project_id}/process"
        before = client.get(f"{base}/scale-up/assessments", headers=headers).json()

        cases = client.get(f"{base}/demo/cases", headers=headers)
        check(cases.status_code == 200, f"演示案例列表失败 {cases.status_code}")
        listed = cases.json()
        check(len(listed) >= 1, "演示案例列表为空")
        slug = listed[0]["slug"]
        summary["cases"] = [item["slug"] for item in listed]

        detail = client.get(f"{base}/demo/cases/{slug}", headers=headers)
        check(detail.status_code == 200, f"演示案例详情失败 {detail.status_code}")
        payload = detail.json()
        spec = payload["assessment"]
        missing = [key for key, value in spec.items() if value in (None, "", [], {})]
        check(not missing, f"演示案例输入框存在空值：{missing}")
        check(len(spec) >= 25, f"演示案例输入字段过少：{len(spec)}")
        summary["spec_fields"] = len(spec)
        for material in spec["materials"]:
            check(material.get("unit_cost_cny_per_kg") is not None, f"物料 {material.get('name')} 缺少单价")
            check(material.get("molecular_weight") is not None, f"物料 {material.get('name')} 缺少分子量")
        check(spec.get("reaction_enthalpy_kj_mol") is not None, "缺少反应焓")

        graph = payload["graph"]
        check(len(graph["nodes"]) >= 15, f"演示图纸节点过少：{len(graph['nodes'])}")
        check(len(graph["edges"]) >= 15, f"演示图纸连线过少：{len(graph['edges'])}")
        kinds = {node["kind"] for node in graph["nodes"]}
        check(len(kinds) >= 8, f"演示图纸设备类型过少：{len(kinds)}")
        tags = [node["tag"] for node in graph["nodes"]]
        check(all(tags), "存在缺失设备位号的节点")
        check(len(set(tags)) == len(tags), "设备位号重复")
        for node in graph["nodes"]:
            check(node["params"], f"节点 {node['id']} 没有参数")
            for param in node["params"]:
                check(bool(param["name"]), f"节点 {node['id']} 参数缺少名称")
                check(bool(str(param["value"]).strip()), f"节点 {node['id']} 参数 {param['name']} 缺少数值")
                check(bool(param["source"]), f"节点 {node['id']} 参数 {param['name']} 缺少来源")
        for edge in graph["edges"]:
            for param in edge["params"]:
                check(bool(param["source"]), f"连线 {edge['id']} 参数 {param['name']} 缺少来源")
        summary["nodes"] = len(graph["nodes"])
        summary["edges"] = len(graph["edges"])
        summary["kinds"] = len(kinds)
        check(bool(payload.get("result_summary")), "演示案例缺少结果摘要")

        meta = {"project": "演示验收", "product": payload["product"], "version": "v1", "revision": "1", "source": "演示案例"}
        sheet = {"title": "演示概念图纸", "graph": graph, "meta": meta}

        svg = client.post(f"{base}/pfd/preview/sheet.svg", json=sheet, headers=headers)
        check(svg.status_code == 200, f"图纸预览 SVG 失败 {svg.status_code} {svg.text[:200]}")
        check(svg.headers["content-type"].startswith("image/svg+xml"), "SVG 内容类型错误")
        text = svg.text
        check("概念级工艺流程图" in text, "SVG 缺少概念级声明")
        check(DISCLAIMER.replace("&", "&amp;") in text or DISCLAIMER in text, "SVG 缺少非施工图声明")
        check("<script" not in text.lower(), "SVG 出现可执行脚本")
        check(PENDING in text, "SVG 未保留待补标记")
        check("设备一览表" in text and "物流表" in text, "SVG 缺少设备一览表或物流表")
        check("V-101" in text or "R-201" in text, "SVG 缺少设备位号")
        summary["sheet_svg_bytes"] = len(svg.content)

        dxf = client.post(f"{base}/pfd/preview/sheet.dxf", json=sheet, headers=headers)
        check(dxf.status_code == 200, f"图纸预览 DXF 失败 {dxf.status_code}")
        dxf_text = dxf.content.decode("gbk")
        check(dxf_text.startswith("0\nSECTION"), "DXF 结构不完整")
        check("AC1009" in dxf_text, "DXF 缺少版本标记")
        check(dxf_text.rstrip().endswith("EOF"), "DXF 缺少 EOF")
        check("PFD_EQUIPMENT" in dxf_text and "PFD_FLOW" in dxf_text, "DXF 图层缺失")
        check(DISCLAIMER in dxf_text, "DXF 缺少非施工图声明")
        check("?" not in dxf_text, "DXF 出现无法编码的字符")
        summary["sheet_dxf_bytes"] = len(dxf.content)

        bad_meta = client.post(f"{base}/pfd/preview/sheet.svg", json={"title": "越界字段", "graph": graph, "meta": {"drawing_no": "X"}}, headers=headers)
        check(bad_meta.status_code == 422, f"未知字段未被拒绝：{bad_meta.status_code}")

        unknown = client.get(f"{base}/demo/cases/not-a-real-case", headers=headers)
        check(unknown.status_code == 404, f"未知案例未返回 404：{unknown.status_code}")

        forbidden = client.get(f"{api}/projects/{other_project_id}/process/demo/cases", headers=headers)
        check(forbidden.status_code == 403, f"跨项目读取演示案例未被拒绝：{forbidden.status_code}")
        forbidden_detail = client.get(f"{api}/projects/{other_project_id}/process/demo/cases/{slug}", headers=headers)
        check(forbidden_detail.status_code == 403, f"跨项目读取案例详情未被拒绝：{forbidden_detail.status_code}")
        summary["isolation_status"] = forbidden.status_code

        after = client.get(f"{base}/scale-up/assessments", headers=headers).json()
        check(before == after, "读取演示案例改写了已保存的放大评估")
        summary["demo_read_only"] = True

    summary["status"] = "ok"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_process_demo_api.py <api-base-url>")
    try:
        raise SystemExit(main(sys.argv[1]))
    except AssertionError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)