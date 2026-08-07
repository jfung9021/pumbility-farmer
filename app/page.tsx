import Link from "next/link";


export default function HomePage() {
  return (
    <main className="home-page">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="site-header">
        <Link className="brand" href="/" aria-label="Pumbility Farmer home">
          <span className="brand-mark">PF</span>
          <span>Pumbility <b>Farmer</b></span>
        </Link>
        <span className="home-status"><i /> Phoenix scoring tools</span>
      </header>

      <section className="home-hero">
        <p className="home-eyebrow">PUMP IT UP · PLAYER-NORMALIZED ANALYSIS</p>
        <h1>Choose how you want<br />to <em>farm.</em></h1>
        <p className="home-intro">
          Explore the full scoring-difficulty tier list, or use your Phoenix 2
          history to find charts near your current skill that offer the most Pumbility.
        </p>

        <div className="feature-grid">
          <Link className="feature-card feature-tier" href="/tier-list">
            <span className="feature-index">01</span>
            <div>
              <p>GLOBAL ANALYSIS</p>
              <h2>Tier List</h2>
              <span>
                Browse the existing Phoenix 1 and Phoenix 2 scoring-difficulty
                rankings with every current filter and evidence label.
              </span>
            </div>
            <b aria-hidden="true">↗</b>
          </Link>

          <Link className="feature-card feature-recommendations" href="/recommendations">
            <span className="feature-index">02</span>
            <div>
              <p>PERSONALIZED ROUTE</p>
              <h2>Recommendations</h2>
              <span>
                Select a Phoenix 2 username, see your scoring rating, and rank
                nearby charts by projected top-50 Pumbility gain.
              </span>
            </div>
            <b aria-hidden="true">↗</b>
          </Link>
        </div>
      </section>

      <footer className="home-footer">
        <p>Built from consented score data. Player IDs and raw score histories stay private.</p>
        <p>Phoenix 2 is authoritative for every recommendation chart and overlapping score.</p>
      </footer>
    </main>
  );
}
