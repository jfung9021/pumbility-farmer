"use client";

import { usePlayerProfile } from "./player-profile-provider";
import styles from "./player-shell.module.css";

export function PlayerRequired({ feature }: { feature: string }) {
  const { loading, error, openChooser, retry } = usePlayerProfile();
  return (
    <section className={styles.playerGate} aria-live="polite">
      {loading ? <p>Restoring your player…</p> : <>
        <h2>Choose a player to see {feature}</h2>
        <p>{error || "Choose once and use the same player across the app. Your choice is saved in this browser."}</p>
        <div className={styles.inlineActions}>
          <button type="button" className={styles.primaryButton} onClick={openChooser}>Choose player</button>
          {error ? <button type="button" className={styles.secondaryButton} onClick={retry}>Retry directory</button> : null}
        </div>
      </>}
    </section>
  );
}
