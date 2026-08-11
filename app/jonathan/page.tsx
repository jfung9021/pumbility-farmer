import type { Metadata } from "next";

import JonathanControls from "./JonathanControls";
import styles from "./jonathan.module.css";


export const metadata: Metadata = {
  title: "Refresh controls | Pumbility Farmer",
  robots: {
    index: false,
    follow: false,
  },
};

export default function JonathanPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <a className={styles.brand} href="/" aria-label="Pumbility Farmer home">
          <span>Pumbility</span>
          <b>Farmer</b>
        </a>
      </header>
      <section className={styles.panel} aria-labelledby="jonathan-title">
        <p className={styles.eyebrow}>Operator controls</p>
        <h1 id="jonathan-title">Refresh Phoenix 2 data</h1>
        <p className={styles.intro}>
          Incremental refresh fetches scores newer than the current watermark. Full refresh
          discards that watermark and refetches every consented player&apos;s complete history.
        </p>
        <JonathanControls />
      </section>
    </main>
  );
}
