"""Isolated end-to-end verification for the concept PFD/CAD workspace API.

Run against a staging API bound to a database copy. Uses project-scoped
"researcher" identity so project isolation is exercised for real.

Usage: python verify_process_pfd_api.py http://127.0.0.1:19133/api/v1/drug-discovery
"""
from __future__ import annotations

import json
import sys

import httpx

SCALE = {
    "name": "验收用 API-X1 百公斤放大初评", "product": "API-X1", "product_molecular_weight": 250,
    "target_product_kg": 100, "conversion_pct": 90, "isolation_yield_pct": 80,
    "product_concentration_kg_l": .1, "temperature_c": 60, "pressure_bar": 1,
    "reaction_time_h": 6, "separation": "结晶", "solvent": "乙腈",
    "solvent_density_kg_l": .786, "heat_capacity_kj_kg_k": 2, "fill_fraction": .7,
    "corrosive": False,
    "materials": [
        {"name": "限量原料 A", "role": "limiting", "molecular_weight": 180, "equivalents": 1},
        {"name": "试剂 B", "role": "reagent", "molecular_weight": 90, "equivalents": 1.1},
    ],
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(base_url: str) -> int:
    api = base_url.rstrip("/")
    headers = {"X-Dev-User": "pfd-verify-owner", "X-Dev-Role": "researcher"}
    other = {"X-Dev-User": "pfd-verify-other", "X-Dev-Role": "researcher"}
    summary: dict = {}
    with httpx.Client(timeout=60.0) as client:
        mine = client.post(f"{api}/projects", json={"name": "PFD 验收项目 A", "disease": "工艺验收", "objective": "概念 PFD 端到端验收"}, headers=headers)
        check(mine.status_code == 201, f"项目创建失败 {mine.status_code} {mine.text[:200]}")
        project_id = mine.json()["id"]
        theirs = client.post(f"{api}/projects", json={"name": "PFD 验收项目 B", "disease": "工艺验收", "objective": "权限隔离验收"}, headers=other)
        check(theirs.status_code == 201, f"第二个项目创建失败 {theirs.status_code}")
        other_project_id = theirs.json()["id"]
        summary["project_id"] = project_id

        base = f"{api}/projects/{project_id}/process"
        created = client.post(f"{base}/scale-up/assessments", json=SCALE, headers=headers)
        check(created.status_code == 200, f"放大评估创建失败 {created.status_code} {created.text[:200]}")
        assessment = created.json()
        summary["assessment_id"] = assessment["id"]

        draft = client.get(f"{base}/pfd/draft-from-assessment", params={"assessment_id": assessment["id"]}, headers=headers)
        check(draft.status_code == 200, f"草稿生成失败 {draft.status_code} {draft.text[:200]}")
        graph = draft.json()["graph"]
        check(len(graph["nodes"]) >= 4 and len(graph["edges"]) >= 3, "草稿节点/连线数量异常")
        check(any(node["kind"] == "reactor" for node in graph["nodes"]), "草稿缺少反应设备节点")
        for node in graph["nodes"]:
            for param in node["params"]:
                check(bool(param["source"]), f"参数 {param['name']} 缺少来源")
        reactor = next(node for node in graph["nodes"] if node["kind"] == "reactor")
        names = {param["name"]: param["value"] for param in reactor["params"]}
        check(names.get("管径") == "待补", "管径必须标为待补而不是编造数值")
        check(names.get("名义容积（初选）") == "2000", "设备名义容积应来自放大评估")
        summary["draft_nodes"] = len(graph["nodes"])
        summary["draft_edges"] = len(graph["edges"])

        diagram = client.post(f"{base}/pfd/diagrams", json={"source_assessment_id": assessment["id"]}, headers=headers)
        check(diagram.status_code == 200, f"图纸创建失败 {diagram.status_code} {diagram.text[:200]}")
        saved = diagram.json()
        check(saved["version"] == 1 and saved["revision"] == 1, "初始版本/修订号异常")
        diagram_id = saved["id"]

        moved = json.loads(json.dumps(saved["graph"]))
        moved["nodes"][0]["label"] = "验收改动节点"
        moved["nodes"][0]["x"] = moved["nodes"][0]["x"] + 37
        updated = client.put(f"{base}/pfd/diagrams/{diagram_id}", json={"title": "验收图纸", "graph": moved, "base_revision": 1}, headers=headers)
        check(updated.status_code == 200, f"图纸更新失败 {updated.status_code} {updated.text[:200]}")
        check(updated.json()["revision"] == 2, "更新后修订号未递增")
        stale = client.put(f"{base}/pfd/diagrams/{diagram_id}", json={"title": "过期写入", "graph": moved, "base_revision": 1}, headers=headers)
        check(stale.status_code == 409, "过期修订号未被拒绝")
        check(client.get(f"{base}/pfd/diagrams/{diagram_id}", headers=headers).json()["title"] == "验收图纸", "重新打开未读到已保存标题")

        version = client.post(f"{base}/pfd/diagrams/{diagram_id}/versions", json={}, headers=headers)
        check(version.status_code == 200, f"建立新版本失败 {version.status_code}")
        check(version.json()["version"] == 2 and version.json()["previous_id"] == diagram_id, "版本链异常")
        summary["versions"] = len(client.get(f"{base}/pfd/diagrams", headers=headers).json())

        svg = client.get(f"{base}/pfd/diagrams/{diagram_id}/export", headers=headers)
        check(svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml"), "SVG 导出失败")
        check("非施工图或已验证 P&amp;ID" in svg.text, "SVG 缺少概念级声明")
        check("<script" not in svg.text.lower(), "SVG 出现可执行脚本")
        check("#0a0c0c" in svg.text and "设备一览表" in svg.text and "物流表" in svg.text,
              "导出 SVG 不是 CAD 图纸（缺少黑图纸底或设备/物流表）")
        check("物料守恒校核" in svg.text, "导出 SVG 缺少物料守恒校核表")
        legend_svg = client.get(f"{base}/pfd/diagrams/{diagram_id}/export", params={"view": "legend"}, headers=headers)
        check(legend_svg.status_code == 200, "图例图纸导出失败")
        check("concept-pfd-legend-v1.svg" in legend_svg.headers.get("content-disposition", ""), "图例图纸文件名异常")
        check("线型、介质与流向图例" in legend_svg.text and "物料流向与守恒说明" in legend_svg.text,
              "图例图纸缺少线型图例或物料守恒说明")
        check("设备一览表" not in legend_svg.text and "<polyline" not in legend_svg.text,
              "图例图纸不应画流程节点与物流线")
        legend_dxf = client.get(f"{base}/pfd/diagrams/{diagram_id}/export.dxf", params={"view": "legend"}, headers=headers)
        check("concept-pfd-legend-v1.dxf" in legend_dxf.headers.get("content-disposition", ""), "图例 DXF 文件名异常")
        legend_text = legend_dxf.content.decode("gbk")
        check("DASHED" in legend_text and "?" not in legend_text, "图例 DXF 缺少虚线线型或出现乱码")
        check(client.get(f"{base}/pfd/diagrams/{diagram_id}/export", params={"view": "plant3d"}, headers=headers).status_code == 400,
              "非法 view 未被拒绝")
        legacy_svg = client.get(f"{base}/pfd/diagrams/{diagram_id}/export.svg", headers=headers)
        check(legacy_svg.status_code == 303, f"旧 .svg 链接应 303 跳转，实际 {legacy_svg.status_code}")
        check(not legacy_svg.content, "旧 .svg 链接不得返回图纸内容（否则会被门户静态缓存回放）")
        followed = client.get(f"{base}/pfd/diagrams/{diagram_id}/export.svg", headers=headers, follow_redirects=True)
        check(followed.status_code == 200 and "非施工图或已验证 P&amp;ID" in followed.text,
              "旧 .svg 链接跟随跳转后应拿到图纸")
        summary["export_path_has_no_static_suffix"] = True
        summary["legend_sheet_svg_bytes"] = len(legend_svg.content)
        summary["svg_bytes"] = len(svg.content)

        dxf = client.get(f"{base}/pfd/diagrams/{diagram_id}/export.dxf", headers=headers)
        check(dxf.status_code == 200, "DXF 导出失败")
        text = dxf.content.decode("gbk")
        check(text.startswith("0\nSECTION") and "AC1009" in text and text.rstrip().endswith("EOF"), "DXF 结构不完整")
        check("PFD_EQUIPMENT" in text and "PFD_FLOW" in text, "DXF 图层缺失")
        check("POLYLINE" in text and "SOLID" in text, "导出 DXF 缺少正交管线或流向箭头实体")
        check("PFD_TITLEBLOCK" in text, "导出 DXF 缺少图签栏图层")
        check("?" not in text, "导出 DXF 出现乱码占位符")
        check("待补" in text, "DXF 未保留待补标记")
        summary["dxf_bytes"] = len(dxf.content)

        forbidden = client.get(f"{api}/projects/{other_project_id}/process/pfd/diagrams", headers=headers)
        check(forbidden.status_code == 403, f"跨项目读取未被拒绝：{forbidden.status_code}")
        forbidden_draft = client.get(f"{api}/projects/{other_project_id}/process/pfd/draft-from-assessment", params={"assessment_id": assessment["id"]}, headers=headers)
        check(forbidden_draft.status_code == 403, "跨项目草稿生成未被拒绝")
        write_forbidden = client.post(f"{api}/projects/{other_project_id}/process/pfd/diagrams", json={"title": "越权图纸", "graph": {"nodes": [], "edges": []}}, headers=headers)
        check(write_forbidden.status_code == 403, "跨项目写入未被拒绝")
        summary["isolation_status"] = forbidden.status_code

        unchanged = client.get(f"{base}/scale-up/assessments", headers=headers).json()
        original = next(item for item in unchanged if item["id"] == assessment["id"])
        check(original["spec"]["name"] == SCALE["name"], "保存图纸改写了原放大评估")
        check(original["result"]["pfd"] == assessment["result"]["pfd"], "原评估概念流程被改写")
        summary["assessment_unchanged"] = True

        empty = client.post(f"{base}/pfd/diagrams", json={"title": "验收空图", "graph": {"nodes": [], "edges": []}}, headers=headers)
        check(empty.status_code == 200 and empty.json()["node_count"] == 0, "空图保存失败")
        summary["empty_state_supported"] = True

        # CAD drafting sheet: the preview and the downloads must be the same CAD output.
        payload = {"title": "验收 CAD 图纸", "graph": moved,
                   "meta": {"project": "PFD 验收项目 A", "product": "API-X1", "version": "v2",
                            "revision": "2", "source": "由已保存放大评估生成"}}
        sheet_svg = client.post(f"{base}/pfd/preview/sheet.svg", json=payload, headers=headers)
        check(sheet_svg.status_code == 200, f"CAD 图纸预览失败 {sheet_svg.status_code} {sheet_svg.text[:200]}")
        check(sheet_svg.headers["content-type"].startswith("image/svg+xml"), "预览未返回 SVG")
        check("设备一览表" in sheet_svg.text and "物流表" in sheet_svg.text, "CAD 图纸缺少设备/物流表")
        check("物料守恒校核" in sheet_svg.text, "CAD 图纸缺少物料守恒校核表")
        legend_preview = client.post(f"{base}/pfd/preview/sheet.svg", json={**payload, "view": "legend"}, headers=headers)
        check(legend_preview.status_code == 200 and "线型、介质与流向图例" in legend_preview.text, "图例图纸预览失败")
        check("非施工图或已验证 P&amp;ID" in sheet_svg.text, "CAD 图纸缺少概念级声明")
        check("<script" not in sheet_svg.text.lower(), "CAD 图纸出现可执行脚本")
        sheet_dxf = client.post(f"{base}/pfd/preview/sheet.dxf", json=payload, headers=headers)
        check(sheet_dxf.status_code == 200, "CAD 图纸 DXF 预览失败")
        dxf_text = sheet_dxf.content.decode("gbk")
        check(dxf_text.startswith("0\nSECTION") and "AC1009" in dxf_text.upper(), "CAD 图纸 DXF 版本异常")
        check(dxf_text.rstrip().endswith("EOF"), "CAD 图纸 DXF 未正常结束")
        check("POLYLINE" in dxf_text and "SOLID" in dxf_text, "CAD 图纸缺少正交管线或流向箭头实体")
        check("PFD_TITLEBLOCK" in dxf_text and "PFD_EQUIPMENT" in dxf_text, "CAD 图纸缺少图签栏或设备图层")
        check("?" not in dxf_text, "CAD 图纸 DXF 出现乱码占位符")
        summary["cad_sheet_svg_bytes"] = len(sheet_svg.content)
        summary["cad_sheet_dxf_bytes"] = len(sheet_dxf.content)

        for label, response in (
            ("preview", client.post(f"{base}/pfd/preview/sheet.svg", json=payload, headers=other)),
            ("draft", client.get(f"{base}/pfd/draft-from-assessment", params={"assessment_id": assessment["id"]}, headers=other)),
        ):
            check(response.status_code == 403, f"跨项目 {label} 未被拒绝：{response.status_code}")

        unknown = client.get(f"{base}/pfd/draft-from-assessment",
                             params={"assessment_id": "00000000-0000-0000-0000-000000000000"}, headers=headers)
        check(unknown.status_code == 404, f"未知评估应返回 404，实际 {unknown.status_code}")

    summary["status"] = "ok"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_process_pfd_api.py <api-base-url>")
    try:
        raise SystemExit(main(sys.argv[1]))
    except AssertionError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
