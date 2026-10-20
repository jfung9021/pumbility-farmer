"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import styles from "./player-shell.module.css";

type ChooserPlayer = { playerKey: string; displayName: string; username?: string };
export type PlayerChooserDialogProps = {
  open: boolean; title: string; players: ChooserPlayer[]; selectedKey: string;
  loading?: boolean; error?: string; onSelect: (key: string) => void; onClose: () => void;
};

export function PlayerChooserDialog({ open, title, players, selectedKey, loading = false, error = "", onSelect, onClose }: PlayerChooserDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const searchId = useId();
  const [query, setQuery] = useState("");
  const [draftKey, setDraftKey] = useState(selectedKey);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open) {
      setQuery("");
      setDraftKey(selectedKey);
      if (!node.open) node.showModal();
    } else if (node.open) node.close();
  }, [open, selectedKey]);
  const matches = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return players.filter((player) => `${player.displayName} ${player.username ?? ""}`.toLocaleLowerCase().includes(normalized));
  }, [players, query]);
  const chosen = players.find((player) => player.playerKey === draftKey);
  return (
    <dialog ref={dialog} className={styles.chooser} aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={styles.dialogBody}>
        <div className={styles.dialogHeading}>
          <h2 id={titleId}>{title}</h2>
          <button type="button" className={styles.iconButton} aria-label="Close player chooser" onClick={onClose}>×</button>
        </div>
        <label className={styles.searchLabel} htmlFor={searchId}>Search players</label>
        <input autoFocus id={searchId} type="search" className={styles.search} value={query}
          placeholder="Name or username" onChange={(event) => setQuery(event.target.value)} />
        <div className={styles.playerResults} aria-label="Players" aria-busy={loading}>
          {loading ? <p className={styles.note} role="status">Loading players…</p> : null}
          {error ? <p className={styles.notice} role="status">{error}</p> : null}
          {!loading && !error && !matches.length ? <p className={styles.note}>No matching players.</p> : null}
          {matches.map((player) => (
            <button key={player.playerKey} type="button" className={styles.playerOption}
              aria-pressed={draftKey === player.playerKey} onClick={() => setDraftKey(player.playerKey)}>
              <span><strong>{player.displayName}</strong>
                {player.username && player.username !== player.displayName ? <small>{player.username}</small> : null}
              </span><span aria-hidden="true">{draftKey === player.playerKey ? "✓" : ""}</span>
            </button>
          ))}
        </div>
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} onClick={onClose}>Cancel</button>
          <button type="button" className={styles.primaryButton} disabled={!chosen || loading}
            onClick={() => { if (chosen) onSelect(chosen.playerKey); }}>Use player</button>
        </div>
      </div>
    </dialog>
  );
}
