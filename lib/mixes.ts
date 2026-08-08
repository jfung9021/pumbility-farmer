export type MixKey = "phoenix1" | "phoenix2";

export interface MixInfo {
  key: MixKey;
  apiValue: "Phoenix" | "Phoenix2";
  label: "Phoenix 1" | "Phoenix 2";
}

export interface CombinedMixInfo {
  key: "combined";
  apiValue: "Phoenix+Phoenix2";
  label: "Phoenix 1 + 2";
}

export const COMBINED_MIX: CombinedMixInfo = {
  key: "combined",
  apiValue: "Phoenix+Phoenix2",
  label: "Phoenix 1 + 2",
};

export interface MixDefinition extends MixInfo {
  archive: {
    url: string;
    reratesUrl: string;
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
      url: "/data/phoenix1.json",
      reratesUrl: "/data/phoenix1-rerates.json",
      frozenAtUtc: "2026-08-08T06:31:56.956799Z",
      sha256: "c08ffab481408d8cbbdc4f1a257e3f8a21fae6b45e3148f9822de89c332cb194",
    },
  },
  phoenix2: {
    key: "phoenix2",
    apiValue: "Phoenix2",
    label: "Phoenix 2",
    archive: null,
  },
};

export function archiveForMix(
  mix: MixKey,
  localAnalysis = false,
): MixDefinition["archive"] {
  return localAnalysis ? null : MIXES[mix].archive;
}

export function isMixKey(value: unknown): value is MixKey {
  return value === "phoenix1" || value === "phoenix2";
}

export function mixFromSearchParams(params: URLSearchParams): MixKey {
  const value = params.get("mix");
  return isMixKey(value) ? value : DEFAULT_MIX;
}
