export function truncateEstimatedDifficulty(value: number): number {
  return Math.trunc(value * 10 + 1e-9) / 10;
}

export function formatEstimatedDifficulty(value: number): string {
  return truncateEstimatedDifficulty(value).toFixed(1);
}
