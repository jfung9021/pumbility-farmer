import { stat } from "node:fs/promises";

/** Share one load per file revision, including requests arriving during validation. */
export function cachedFileReader<T>(load: (filePath: string) => Promise<T>) {
  let current: { revision: string; pending: Promise<T> } | undefined;
  return async (filePath: string): Promise<T> => {
    const file = await stat(filePath, { bigint: true });
    const revision = [filePath, file.dev, file.ino, file.size, file.mtimeNs, file.ctimeNs].join(":");
    if (current?.revision === revision) return current.pending;
    const entry = { revision, pending: undefined as unknown as Promise<T> };
    entry.pending = Promise.resolve().then(() => load(filePath)).catch(error => {
      if (current === entry) current = undefined;
      throw error;
    });
    current = entry;
    return entry.pending;
  };
}
