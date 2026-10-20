export const PLAYER_PROFILE_STORAGE_KEY = "pumbility-farmer.player.v1";
export const PLAYER_SETUP_STORAGE_KEY = "pumbility-farmer.setup.v1";

export function readPlayerPreference(value: string | null): string {
  if (!value) return "";
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || !("version" in parsed)
    || parsed.version !== 1 || !("playerKey" in parsed) || typeof parsed.playerKey !== "string"
    || !parsed.playerKey.trim()) throw new Error("The saved player could not be restored.");
  return parsed.playerKey;
}

export function serializePlayerPreference(playerKey: string): string {
  return JSON.stringify({ version: 1, playerKey });
}

export function isPersonalPlayerRoute(pathname: string): boolean {
  return ["/tier-list", "/recommendations", "/skill-radar"].includes(pathname);
}

export function playerProfileHref(href: string, temporaryPlayerKey: string | null): string {
  if (temporaryPlayerKey === null || !href.startsWith("/") || href.startsWith("//")) return href;
  const url = new URL(href, "https://pumbility-farmer.invalid");
  if (!isPersonalPlayerRoute(url.pathname)) return href;
  url.searchParams.set("player", temporaryPlayerKey);
  return `${url.pathname}${url.search}${url.hash}`;
}
