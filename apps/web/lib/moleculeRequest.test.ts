import { expect, test, vi } from 'vitest';
vi.mock('./api', () => ({ api: vi.fn() }));
import { api } from './api';
import { moleculeRequest } from './moleculeRequest';
test('serializes catalog lookups and merges duplicate in-flight requests', async () => {
  let release!: (value: unknown) => void;
  vi.mocked(api).mockReset().mockImplementationOnce(() => new Promise(r => { release = r; })).mockResolvedValueOnce({ id: 'second' });
  const first = moleculeRequest('/first');
  const duplicate = moleculeRequest('/first');
  const second = moleculeRequest('/second');
  await Promise.resolve();
  expect(api).toHaveBeenCalledTimes(1);
  expect(duplicate).toBe(first);
  release({ id: 'first' });
  expect(await second).toEqual({ id: 'second' });
  expect(api).toHaveBeenCalledTimes(2);
});
test('a failed lookup releases the queue and can be retried', async () => {
  vi.mocked(api).mockReset().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ok: true });
  await expect(moleculeRequest('/failure')).rejects.toThrow('offline');
  await expect(moleculeRequest('/failure')).resolves.toEqual({ ok: true });
});
