"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { SCORE_SYNC_URL } from "./score-sync-link";
import { usePlayerProfile } from "./player-profile-provider";
import styles from "./player-shell.module.css";

type SiteHeaderProps = { active?: "recommendations" | "tier-list" | "skill-radar" | "official-comparison" | "about" };
const destinations = [
  { label: "Home", href: "/", active: undefined },
  { label: "Recommendations", href: "/recommendations", active: "recommendations" },
  { label: "Tier Lists", href: "/tier-list", active: "tier-list" },
  { label: "Skill Radar", href: "/skill-radar", active: "skill-radar" },
  { label: "Player Comparison", href: "/official-leaderboard-comparison", active: "official-comparison" },
  { label: "About", href: "/about", active: "about" },
];

export function SiteHeader({ active }: SiteHeaderProps) {
  const [open, setOpen] = useState(false);
  const drawer = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { player, loading, error, openChooser, clearPlayer, profileHref, temporary, hasSelection, savedPlayer,
    saveTemporaryPlayer, useSavedPlayer, storageNotice, retry } = usePlayerProfile();
  useEffect(() => {
    const node = drawer.current;
    if (open && node && !node.open) node.showModal();
    if (!open && node?.open) node.close();
  }, [open]);
  const choose = () => { setOpen(false); openChooser(); };
  return (
    <header className="site-header">
      <Link className="brand" href="/" aria-label="Pumbility Farmer home">
        <span className="brand-mark">PF</span><span>Pumbility <b>Farmer</b></span>
      </Link>
      <button type="button" className={styles.menuButton} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
        <span aria-hidden="true">☰</span> Menu
      </button>
      <dialog ref={drawer} className={styles.drawer} aria-labelledby={titleId}
        onCancel={(event) => { event.preventDefault(); setOpen(false); }}
        onClick={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
        <div className={styles.drawerInner}>
          <div className={styles.dialogHeading}><h2 id={titleId}>Menu</h2>
            <button type="button" autoFocus className={styles.iconButton} aria-label="Close menu" onClick={() => setOpen(false)}>×</button>
          </div>
          <section className={styles.playerBlock} aria-label="Your player">
            <p className={styles.eyebrow}>{temporary ? "Shared-link player" : "Your player"}</p>
            <p className={styles.playerName}>{loading ? "Restoring player…" : player?.displayName || "No player selected"}</p>
            {error ? <p className={styles.notice}>{error}</p> : null}
            <div className={styles.stackActions}>
              <button type="button" className={styles.primaryButton} onClick={choose}>{player ? "Change player" : "Choose player"}</button>
              {temporary && player ? <button type="button" className={styles.secondaryButton} onClick={saveTemporaryPlayer}>Use this player</button> : null}
              {temporary && savedPlayer ? <button type="button" className={styles.secondaryButton} onClick={useSavedPlayer}>Use saved player: {savedPlayer.displayName}</button> : null}
              {hasSelection ? <button type="button" className={styles.secondaryButton} onClick={clearPlayer}>Browse without a player</button> : null}
              {error ? <button type="button" className={styles.secondaryButton} onClick={retry}>Retry directory</button> : null}
            </div>
            {storageNotice ? <p className={styles.notice} role="status">{storageNotice}</p> : null}
            <a className={styles.syncLink} href={SCORE_SYNC_URL} target="_blank" rel="noopener noreferrer">Sync scores on PIU Scores ↗</a>
          </section>
          <nav className={styles.navigation} aria-label="Primary navigation">
            {destinations.map((destination) => <Link key={destination.href} href={profileHref(destination.href)}
              aria-current={active === destination.active ? "page" : undefined} onClick={() => setOpen(false)}>
              {destination.label}
              {!player && !loading && ["recommendations", "skill-radar"].includes(destination.active ?? "") ? <small>Choose a player to get started</small> : null}
            </Link>)}
          </nav>
        </div>
      </dialog>
    </header>
  );
}
