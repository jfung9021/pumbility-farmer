"use client";

import { useId, useState } from "react";
import type { RecommendationPlayerSummary } from "../../lib/types";

type PlayerPickerProps = {
  id?: string;
  players: RecommendationPlayerSummary[];
  selectedKey: string;
  query: string;
  disabled?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onQueryChange: (query: string) => void;
  onSelect: (playerKey: string, displayName: string) => void;
};

export function PlayerPicker({ id, players, selectedKey, query, disabled, open, onOpenChange, onQueryChange, onSelect }: PlayerPickerProps) {
  const generatedId = useId();
  const inputId = id || generatedId;
  const listId = `${inputId}-options`;
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const normalized = selectedKey ? "" : query.trim().toLocaleLowerCase();
  const options = normalized
    ? players.filter((player) => `${player.displayName} ${player.username}`.toLocaleLowerCase().includes(normalized))
    : players;
  const activeIndex = Math.min(highlightedIndex, Math.max(0, options.length - 1));
  const select = (player: RecommendationPlayerSummary) => {
    onSelect(player.playerKey, player.displayName);
    onOpenChange(false);
  };

  return (
    <div className="player-combobox">
      <input
        aria-activedescendant={open && options.length ? `${listId}-${activeIndex}` : undefined}
        aria-autocomplete="list"
        aria-controls={listId}
        aria-expanded={open}
        aria-haspopup="listbox"
        autoComplete="off"
        disabled={disabled}
        id={inputId}
        onBlur={() => onOpenChange(false)}
        onChange={(event) => { setHighlightedIndex(0); onQueryChange(event.target.value); }}
        onClick={() => onOpenChange(true)}
        onFocus={(event) => { event.currentTarget.select(); onOpenChange(true); }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            onOpenChange(true);
            const direction = event.key === "ArrowDown" ? 1 : -1;
            setHighlightedIndex(open ? Math.max(0, Math.min(options.length - 1, activeIndex + direction)) : 0);
          } else if (event.key === "Enter" && open && options[activeIndex]) {
            event.preventDefault();
            select(options[activeIndex]);
          } else if (event.key === "Escape") {
            onOpenChange(false);
          }
        }}
        placeholder="Type or select a player"
        role="combobox"
        type="text"
        value={query}
      />
      {open ? (
        <div className="player-options" id={listId} role="listbox">
          {options.map((player, index) => (
            <button
              aria-selected={selectedKey === player.playerKey}
              data-highlighted={index === activeIndex || undefined}
              id={`${listId}-${index}`}
              key={player.playerKey}
              onClick={() => select(player)}
              onMouseDown={(event) => event.preventDefault()}
              role="option"
              tabIndex={-1}
              type="button"
            >{player.displayName}</button>
          ))}
          {!options.length ? <p>No matching usernames</p> : null}
        </div>
      ) : null}
    </div>
  );
}
