"use client";

import type { Ketcher } from "ketcher-core";
import { Editor } from "ketcher-react";
import { StandaloneStructServiceProvider } from "ketcher-standalone";
import { ArrowDownToLine, LoaderCircle, PencilRuler } from "lucide-react";
import { useMemo, useRef, useState } from "react";

export function KetcherStructureEditor({ structure, onSmiles, onError }: {
  structure: string;
  onSmiles: (smiles: string) => void;
  onError: (message: string) => void;
}) {
  const editor = useRef<Ketcher | null>(null);
  const [ready, setReady] = useState(false);
  const [working, setWorking] = useState(false);
  const provider = useMemo(() => new StandaloneStructServiceProvider(), []);

  async function loadCurrentStructure() {
    if (!editor.current || !structure.trim()) return;
    setWorking(true);
    try {
      await editor.current.setMolecule(structure.trim());
    } catch {
      onError("当前 SMILES / InChI 无法载入结构画板，请先检查结构字符串。");
    } finally {
      setWorking(false);
    }
  }

  async function exportSmiles() {
    if (!editor.current) return;
    setWorking(true);
    try {
      const smiles = (await editor.current.getSmiles()).trim();
      if (!smiles) {
        onError("画板中还没有分子结构。");
        return;
      }
      onSmiles(smiles);
    } catch {
      onError("画板结构暂时无法转换为 SMILES，请检查是否包含反应或不支持的对象。");
    } finally {
      setWorking(false);
    }
  }

  return <div className="fungal-ketcher-shell">
    <div className="fungal-ketcher-heading"><span><PencilRuler size={15} />2D 分子结构绘制</span><small>Ketcher Standalone · 结构只在本页面编辑</small></div>
    <div className="fungal-ketcher-canvas">
      <Editor
        staticResourcesUrl="/apps/ai4science"
        structServiceProvider={provider}
        disableMacromoleculesEditor
        onInit={(instance) => { editor.current = instance; setReady(true); }}
        errorHandler={(message) => onError(String(message || "结构编辑器发生错误"))}
      />
    </div>
    <div className="fungal-ketcher-actions">
      <button type="button" className="secondary" disabled={!ready || working || !structure.trim()} onClick={() => void loadCurrentStructure()}><ArrowDownToLine size={15} />从下方 SMILES 载入画板</button>
      <button type="button" disabled={!ready || working} onClick={() => void exportSmiles()}>{working ? <LoaderCircle className="spin" size={15} /> : <PencilRuler size={15} />}生成 SMILES 并填入</button>
    </div>
  </div>;
}
