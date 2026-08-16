export function truncateEstimatedDifficulty(value: number): number {
  return Math.trunc(value + 1e-9);
}

export function formatEstimatedDifficulty(value: number): string {
  return String(truncateEstimatedDifficulty(value));
}
