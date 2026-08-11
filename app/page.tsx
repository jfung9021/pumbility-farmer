import Link from "next/link";
import { ScoreSyncLink } from "./_components/score-sync-link";
import { SiteHeader } from "./_components/site-header";

export default function HomePage() {
  return (
    <main className="home-page">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <SiteHeader />

      <section className="home-hero">
        <div className="feature-grid">
          <Link className="feature-card feature-recommendations" href="/recommendations">
            <div>
              <p>PERSONALIZED ROUTE</p>
              <h2>Recommendations</h2>
              <span>
                Select a username, see which version supplies your skill rating,
                and rank nearby charts against your current Phoenix 2 history.
              </span>
            </div>
            <b aria-hidden="true">↗</b>
          </Link>

          <Link className="feature-card feature-tier" href="/tier-list">
            <div>
              <p>GLOBAL ANALYSIS</p>
              <h2>Tier List</h2>
              <span>
                Compare scoring-based difficulty estimates across Phoenix 1 and
                Phoenix 2 charts in compact or detailed views.
              </span>
            </div>
            <b aria-hidden="true">↗</b>
          </Link>
        </div>

        <section className="home-how-to" aria-labelledby="home-how-to-title">
          <div>
            <p>HOW TO</p>
            <h2 id="home-how-to-title">Sync your scores before you start</h2>
          </div>
          <div className="home-how-to-copy">
            <ol>
              <li>Log in to PIU Scores on its separate website.</li>
              <li>Open the Phoenix score upload page and sync your latest scores.</li>
              <li>Return here to view recommendations based on the updated history.</li>
            </ol>
            <ScoreSyncLink>Open PIU Scores upload</ScoreSyncLink>
          </div>
        </section>
      </section>

      <footer className="home-footer">
        <p>Built from consented score data. Player IDs and raw score histories stay private.</p>
        <p>Phoenix 2 is authoritative for every recommendation chart and overlapping score.</p>
      </footer>
    </main>
  );
}
