import { api } from './api';

// The catalog service permits only two concurrent external lookups. Share a
// queue across identities, dossiers and target catalogs within this page.
let tail: Promise<unknown> = Promise.resolve();
const pending = new Map<string, Promise<unknown>>();
export function moleculeRequest<T>(path: string): Promise<T> {
  const existing = pending.get(path);
  if (existing) return existing as Promise<T>;
  const request = tail.then(async () => {
    for (let attempt = 0; ; attempt += 1) {
      const value = await api<T>(path);
      const warnings = (value as { warnings?: string[] })?.warnings;
      if (attempt >= 2 || !warnings?.some(w => w.includes('资料查询繁忙'))) return value;
      await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
    }
  });
  pending.set(path, request);
  tail = request.catch(() => undefined);
  void request.finally(() => pending.delete(path)).catch(() => undefined);
  return request;
}
