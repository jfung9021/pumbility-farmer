export const LIMITED_DATA_CONTRIBUTOR_THRESHOLD = 20;

export function hasLimitedData(nContributors: number): boolean {
  return nContributors < LIMITED_DATA_CONTRIBUTOR_THRESHOLD;
}
