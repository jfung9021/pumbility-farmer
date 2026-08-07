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
        <p className="home-intro">
          Use your Phoenix history to find charts near your current skill that offer
          the most Pumbility, or explore the combined scoring-difficulty tier list.
        </p>

        <div className="feature-grid">
          <Link className="feature-card feature-recommendations" href="/recommendations">
            <span className="feature-index">01</span>
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
            <span className="feature-index">02</span>
            <div>
              <p>GLOBAL ANALYSIS</p>
              <h2>Tier List</h2>
              <span>
                Browse one current tier list built from normalized Phoenix 1 and
                Phoenix 2 evidence with every filter and evidence label.
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
