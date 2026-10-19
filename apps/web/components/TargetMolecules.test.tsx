// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { TargetMolecules } from './TargetMolecules';
import { moleculeRequest } from '@/lib/moleculeRequest';

vi.mock('@/lib/moleculeRequest', () => ({ moleculeRequest: vi.fn() }));
vi.mock('./DrugDossier', () => ({ DrugDossier: () => null }));
vi.mock('./MoleculeComparison', () => ({ MoleculeComparison: () => null }));

it('shows PubChem assay evidence when ChEMBL mapping is unavailable', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(moleculeRequest).mockResolvedValueOnce({
    status: 'partial', warnings: ['ChEMBL 实时靶点映射暂不可用'], items: [],
    source_status: { chembl: 'unavailable', snapshot: 'not_covered', pubchem: 'available', open_targets: 'no_match' },
    clinical_items: [], pubchem_items: [{ identifier: 'CID:4670', name: 'Test molecule', source_url: 'https://pubchem.ncbi.nlm.nih.gov/compound/4670', assays: [{ aid: 414229, assay_name: 'Human iPLA2 inhibition', result_type: 'Inhibition', reported_value: 92, pubmed_ids: ['19053783'] }] }],
  });
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<TargetMolecules targetId="t1" symbol="PLA2G6" />));
    const disclosure = host.querySelector('details.target-molecules')!;
    await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); });
    expect(moleculeRequest).toHaveBeenCalledWith('/targets/t1/molecules');
    expect(host.textContent).toContain('PubChem 公开实验分子 1 个');
    expect(host.textContent).toContain('已找到可核对的关联线索');
    expect(host.textContent).toContain('AID 414229');
    expect(host.textContent).toContain('ChEMBL 本次没有可显示分子');
    expect(host.textContent).not.toContain('已有批准记录的药物');
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it('retry requests a fresh target lookup instead of repeating a cached partial response', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(moleculeRequest).mockReset().mockResolvedValue({
    status: 'partial', warnings: ['ChEMBL 暂未取回'], items: [],
    pubchem_items: [{ identifier: 'CID:1', name: 'Compound', source_url: 'https://pubchem.ncbi.nlm.nih.gov/compound/1', assays: [] }],
  });
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<TargetMolecules targetId="t2" symbol="PLA2G6" />));
    const disclosure = host.querySelector('details.target-molecules')!;
    await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); });
    const button = [...host.querySelectorAll('button')].find(element => element.textContent?.includes('更新缺失的来源'))!;
    await act(async () => button.click());
    expect(moleculeRequest).toHaveBeenLastCalledWith('/targets/t2/molecules?refresh=true');
  } finally { await act(async () => root.unmount()); host.remove(); }
});
