"use client";

import { Suspense, createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { track } from "@vercel/analytics";
import type { RecommendationPlayerSummary, RecommendationPlayersResponse } from "../../lib/types";
import { isPersonalPlayerRoute, playerProfileHref, PLAYER_PROFILE_STORAGE_KEY, PLAYER_SETUP_STORAGE_KEY, readPlayerPreference, serializePlayerPreference } from "../../lib/player-profile";
import { PlayerChooserDialog } from "./player-chooser-dialog";
import { PlayerUrlSync } from "./player-url-sync";

type PlayerProfile = {
  playerKey: string; player: RecommendationPlayerSummary | null; players: RecommendationPlayersResponse | null;
  loading: boolean; directoryLoading: boolean; error: string; hydrated: boolean;
  openChooser: () => void; clearPlayer: () => void; profileHref: (href: string) => string; retry: () => void;
  temporary: boolean; hasSelection: boolean; savedPlayer: RecommendationPlayerSummary | null; useSavedPlayer: () => void;
  saveTemporaryPlayer: () => void; storageNotice: string; setupDismissed: boolean; dismissSetup: () => void;
};

const PlayerProfileContext = createContext<PlayerProfile | null>(null);

function removePlayerOverride() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("player")) return;
  url.searchParams.delete("player");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

export function PlayerProfileProvider({ children }: { children: ReactNode }) {
  const [savedKey, setSavedKey] = useState("");
  const [temporaryKey, setTemporaryKey] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [players, setPlayers] = useState<RecommendationPlayersResponse | null>(null);
  const [directoryLoading, setDirectoryLoading] = useState(true);
  const [directoryError, setDirectoryError] = useState("");
  const [storageNotice, setStorageNotice] = useState("");
  const [chooserOpen, setChooserOpen] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [setupDismissed, setSetupDismissed] = useState(false);

  useEffect(() => {
    try {
      setSavedKey(readPlayerPreference(window.localStorage.getItem(PLAYER_PROFILE_STORAGE_KEY)));
      setSetupDismissed(window.localStorage.getItem(PLAYER_SETUP_STORAGE_KEY) === "dismissed");
    } catch {
      setStorageNotice("This browser could not restore a saved player. You can choose one for this visit.");
    }
    const url = new URL(window.location.href);
    if (isPersonalPlayerRoute(url.pathname)) setTemporaryKey(url.searchParams.get("player"));
    setHydrated(true);
    const onStorage = (event: StorageEvent) => {
      if (event.key === PLAYER_PROFILE_STORAGE_KEY || event.key === null) {
        try { setSavedKey(readPlayerPreference(event.key === null ? null : event.newValue)); }
        catch { setSavedKey(""); setStorageNotice("The saved player changed and could not be restored. Choose a player again."); }
      }
      if (event.key === PLAYER_SETUP_STORAGE_KEY || event.key === null) setSetupDismissed(event.newValue === "dismissed");
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setDirectoryLoading(true);
    setDirectoryError("");
    fetch("/api/recommendations/players", { signal: controller.signal })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "The player directory is unavailable.");
        if (!Array.isArray(data.players)) throw new Error("The player directory is unavailable.");
        if (!controller.signal.aborted) setPlayers(data as RecommendationPlayersResponse);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setDirectoryError(error instanceof Error ? error.message : "The player directory is unavailable.");
      })
      .finally(() => { if (!controller.signal.aborted) setDirectoryLoading(false); });
    return () => controller.abort();
  }, [retryCount]);

  const syncUrlPlayer = useCallback((pathname: string, key: string | null) => {
    if (isPersonalPlayerRoute(pathname)) setTemporaryKey(key);
  }, []);
  const dismissSetup = useCallback(() => {
    setSetupDismissed(true);
    try { window.localStorage.setItem(PLAYER_SETUP_STORAGE_KEY, "dismissed"); } catch { /* Selection remains usable for this visit. */ }
  }, []);
  const choosePlayer = useCallback((key: string) => {
    const selected = players?.players.find((candidate) => candidate.playerKey === key);
    if (!selected) return;
    setSavedKey(key);
    setTemporaryKey(null);
    setStorageNotice("");
    removePlayerOverride();
    try { window.localStorage.setItem(PLAYER_PROFILE_STORAGE_KEY, serializePlayerPreference(key)); }
    catch { setStorageNotice("Player selected for this visit. This browser could not save the choice."); }
    dismissSetup();
    setChooserOpen(false);
    track("recommendation_player_selected", { playerName: selected.displayName });
  }, [dismissSetup, players]);
  const clearPlayer = useCallback(() => {
    setSavedKey("");
    setTemporaryKey(null);
    setStorageNotice("");
    removePlayerOverride();
    try { window.localStorage.removeItem(PLAYER_PROFILE_STORAGE_KEY); }
    catch { setStorageNotice("Browsing without a player for this visit. This browser could not update the saved choice."); }
    dismissSetup();
  }, [dismissSetup]);
  const candidateKey = temporaryKey ?? savedKey;
  const player = players?.players.find((candidate) => candidate.playerKey === candidateKey) ?? null;
  const savedPlayer = players?.players.find((candidate) => candidate.playerKey === savedKey) ?? null;
  const loading = !hydrated || (Boolean(candidateKey) && directoryLoading && !players);
  const error = directoryError || (hydrated && !directoryLoading && players && !player && (candidateKey || temporaryKey !== null)
    ? temporaryKey !== null ? "This player link could not be resolved. Choose a player or use your saved player from Menu." : "Your saved player is no longer in the player directory. Choose another player."
    : "");
  const profileHref = useCallback((href: string) => playerProfileHref(href, temporaryKey), [temporaryKey]);
  const useSavedPlayer = useCallback(() => { setTemporaryKey(null); removePlayerOverride(); }, []);
  const value = useMemo<PlayerProfile>(() => ({
    playerKey: player?.playerKey ?? "", player, players, loading, directoryLoading, error, hydrated,
    openChooser: () => setChooserOpen(true), clearPlayer, profileHref,
    retry: () => setRetryCount((count) => count + 1), temporary: temporaryKey !== null, hasSelection: Boolean(candidateKey) || temporaryKey !== null, savedPlayer,
    useSavedPlayer, saveTemporaryPlayer: () => { if (player) choosePlayer(player.playerKey); },
    storageNotice, setupDismissed, dismissSetup,
  }), [player, players, loading, directoryLoading, error, hydrated, clearPlayer, profileHref, temporaryKey, candidateKey, savedPlayer, useSavedPlayer, choosePlayer, storageNotice, setupDismissed, dismissSetup]);

  return (
    <PlayerProfileContext.Provider value={value}>
      <Suspense fallback={null}><PlayerUrlSync onChange={syncUrlPlayer} /></Suspense>
      {children}
      <PlayerChooserDialog open={chooserOpen} title="Choose your player" players={players?.players ?? []}
        selectedKey={player?.playerKey ?? ""} loading={directoryLoading} error={directoryError}
        onSelect={choosePlayer} onClose={() => setChooserOpen(false)} />
    </PlayerProfileContext.Provider>
  );
}

export function usePlayerProfile(): PlayerProfile {
  const profile = useContext(PlayerProfileContext);
  if (!profile) throw new Error("usePlayerProfile must be used within PlayerProfileProvider.");
  return profile;
}
