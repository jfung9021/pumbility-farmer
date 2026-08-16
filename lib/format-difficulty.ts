export function truncateEstimatedDifficulty(value: number): number {
  return Math.trunc(value * 10 + 1e-9) / 10;
}

export function formatEstimatedDifficulty(value: number): string {
  return truncateEstimatedDifficulty(value).toFixed(1);
}

export function truncateCoopEstimatedDifficulty(value: number): number {
  return Math.trunc(value + 1e-9);
}

export function formatCoopEstimatedDifficulty(value: number): string {
  return String(truncateCoopEstimatedDifficulty(value));
}
