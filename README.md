# X News Poster

Automated **AI & Tech insight** posts for X (Twitter), powered by:

| Piece | Tech |
|--------|------|
| Schedule | GitHub Actions (6× daily) |
| News | Free RSS feeds (`feedparser`) |
| Research + write + validate | Amazon Bedrock · Claude Sonnet 4.6 |
| Publishing | X API v2 (`tweepy`) |
| Dedup history | `data/posted_news.json` (atomic write + git commit) |

---

## How it works

```
fetch → research → write → validate → (optional) post
  RSS     page+facts   insight    style/fact/quality     X API
```

1. **Fetch** RSS (recency, undated cap, blocklist, AI scoring)  
2. **Research** top stories: SSRF-safe page fetch, extract **verified facts** only  
3. **Write** one human insight (no links, no AI voice, no `_` / em dashes)  
4. **Validate** style + fact grounding + quality (≥7); one rewrite if needed  
5. **Post** only if `validation_approved=true` **and** `AUTO_POST=true`  
6. **History** saved after each successful live post (atomic JSON)

---

## Project layout

```
x-news-poster/
├── .github/workflows/post-x.yml
├── agents/
│   ├── utils.py                 # normalize_url, atomic JSON, coercions
│   ├── bedrock_client.py
│   ├── fetch_news.py
│   ├── research.py
│   ├── analyze_and_generate.py
│   ├── validate.py
│   └── post_to_x.py
├── data/posted_news.json
├── requirements.txt
└── README.md
```

---

## Posting controls (important)

| Mode | How | Posts to X? |
|------|-----|-------------|
| Manual dry run (default) | Actions → dry_run=`true` | No |
| Manual live | dry_run=`false` **and** auto_post=`true` | Yes (if validated) |
| Schedule | Cron (unless kill-switch) | Yes (if validated) |
| Schedule paused | Repo variable `ENABLE_AUTO_POST=false` | No (dry only) |

**Local live post requires both:**

```bash
AUTO_POST=true python agents/post_to_x.py
# or simulate:
DRY_RUN=true python agents/post_to_x.py
```

Bare `python agents/post_to_x.py` **refuses** to post (safe default).

---

## Setup

### Secrets (GitHub → Settings → Secrets and variables → Actions)

| Secret | Description |
|--------|-------------|
| `X_API_KEY` | X consumer key |
| `X_API_SECRET` | X consumer secret |
| `X_ACCESS_TOKEN` | User access token |
| `X_ACCESS_TOKEN_SECRET` | User access token secret |
| `AWS_ACCESS_KEY_ID` | IAM key with Bedrock invoke |
| `AWS_SECRET_ACCESS_KEY` | IAM secret |
| `AWS_REGION` | e.g. `us-east-1` |

### Variables (optional)

| Variable | Description |
|----------|-------------|
| `BEDROCK_MODEL_ID` | Default: `us.anthropic.claude-sonnet-4-6` |
| `ENABLE_AUTO_POST` | Set `false` to pause scheduled live posts |

### First run

1. Push repo and add secrets  
2. **Run workflow** with dry_run=`true`  
3. Inspect artifacts / logs  
4. For a real manual post: dry_run=`false` **and** auto_post=`true`  

---

## Local development

```bash
cd x-news-poster
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
export X_API_KEY=...
export X_API_SECRET=...
export X_ACCESS_TOKEN=...
export X_ACCESS_TOKEN_SECRET=...

python agents/fetch_news.py
python agents/research.py
python agents/analyze_and_generate.py
python agents/validate.py
DRY_RUN=true python agents/post_to_x.py   # preview only
AUTO_POST=true python agents/post_to_x.py # live
```

---

## Quality gates (before post)

Tweets must:

- Be **70–240** characters  
- Have **no** URLs, domains, hashtags, emojis  
- Have **no** `_`, em/en dashes, or `…` / `...`  
- Pass fact grounding vs researched page facts  
- Quality score ≥ 7, substance ≥ 6, human ≥ 6  
- Be marked `validation_approved: true`  
- Not already exist in history (normalized URL + exact text)

---

## Safety notes

- History uses **atomic writes** (`tmp` + replace); corrupt history **fails the job** (no silent empty dedup)  
- Research fetches are **HTTPS-only** with private/metadata IP blocks and redirect re-check  
- Live X API failures return **non-zero exit** when all approved posts fail  
- Rejected drafts are kept in `tweets_to_post.json` with `status: rejected` for debugging  

---

## RSS feeds

Hacker News, TechCrunch, The Verge, Ars Technica, MIT Technology Review, VentureBeat AI, Reddit r/MachineLearning, OpenAI Blog, Google AI Blog, Wired AI — edit in `agents/fetch_news.py`.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| WOULD POST but nothing on X | Need `AUTO_POST=true` (and dry_run false) |
| Corrupt history error | Restore `data/posted_news.json` from git |
| Bedrock access denied | Model access + inference profile `us.anthropic.claude-sonnet-4-6` |
| Schedule not posting | `ENABLE_AUTO_POST` may be `false` |
| Duplicate posts | Ensure history commit/push succeeded after live post |

---

## License

MIT
