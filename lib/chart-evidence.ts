export const LIMITED_DATA_CONTRIBUTOR_THRESHOLD = 10;

export function hasLimitedData(nContributors: number): boolean {
  return !Number.isFinite(nContributors) || nContributors < LIMITED_DATA_CONTRIBUTOR_THRESHOLD;
}
