export type MixKey = "phoenix1" | "phoenix2";

export interface MixInfo {
  key: MixKey;
  apiValue: "Phoenix" | "Phoenix2";
  label: "Phoenix 1" | "Phoenix 2";
}

export interface MixDefinition extends MixInfo {
  archive: {
    url: string;
    frozenAtUtc: string;
    sha256: string;
  } | null;
}

export const DEFAULT_MIX: MixKey = "phoenix2";

export const MIXES: Record<MixKey, MixDefinition> = {
  phoenix1: {
    key: "phoenix1",
    apiValue: "Phoenix",
    label: "Phoenix 1",
    archive: {
      url: "/data/phoenix1-20260807.json",
      frozenAtUtc: "2026-08-07T06:10:30.378Z",
      sha256: "95e8c4faf522e034702ea1be67bfd8b05302b714415aeb17f905a6930426d91a",
    },
  },
  phoenix2: {
    key: "phoenix2",
    apiValue: "Phoenix2",
    label: "Phoenix 2",
    archive: null,
  },
};

export function isMixKey(value: unknown): value is MixKey {
  return value === "phoenix1" || value === "phoenix2";
}

export function mixFromSearchParams(params: URLSearchParams): MixKey {
  const value = params.get("mix");
  return isMixKey(value) ? value : DEFAULT_MIX;
}
