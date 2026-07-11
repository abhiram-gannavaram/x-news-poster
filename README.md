# X News Poster

Automated **AI & Tech news** poster for X (Twitter), powered by:

| Piece | Tech |
|--------|------|
| Schedule | GitHub Actions (6× daily) |
| News source | Free RSS feeds (`feedparser`) |
| Analysis + tweets | Amazon Bedrock · Claude Sonnet 4.6 |
| Publishing | X API v2 (`tweepy`) |
| Dedup history | `data/posted_news.json` (committed back each run) |

---

## How it works

```
fetch → research → write → validate → (optional) post
  RSS     page+facts   insight    style/fact/quality     X API
```

1. **Fetch** RSS (recency + blocklist + AI scoring)  
2. **Research** top stories: download page text, extract **verified facts** only  
3. **Write** one human insight (no links, no AI voice, no `_` / em dashes)  
4. **Validate** in layers: style gate → fact grounding → quality score (≥7)  
5. **Post** only if `validation_approved=true` **and** `AUTO_POST=true`  
6. **History** saved so the next run skips the same story  

Manual runs default to **dry_run=true** so you can inspect drafts first.  


---

## Project layout

```
x-news-poster/
├── .github/workflows/
│   └── post-x.yml          # cron (6×/day) + workflow_dispatch
├── agents/
│   ├── fetch_news.py       # RSS ingest + dedup
│   ├── analyze_and_generate.py  # Bedrock Claude pick + tweet
│   └── post_to_x.py        # quality gate + X post + history
├── data/
│   └── posted_news.json    # durable post history
├── requirements.txt
└── README.md
```

---

## Recommended free RSS feeds (included)

| # | Source | Feed URL | Focus |
|---|--------|----------|--------|
| 1 | **Hacker News** | `https://hnrss.org/frontpage` | High-signal tech discussion |
| 2 | **TechCrunch** | `https://techcrunch.com/feed/` | Startups & product launches |
| 3 | **The Verge** | `https://www.theverge.com/rss/index.xml` | Consumer tech & culture |
| 4 | **Ars Technica** | `https://feeds.arstechnica.com/arstechnica/index` | Deep tech reporting |
| 5 | **MIT Technology Review** | `https://www.technologyreview.com/feed/` | Serious AI & research |
| 6 | **VentureBeat AI** | `https://venturebeat.com/category/ai/feed/` | AI business & products |
| 7 | **Reddit r/MachineLearning** | `https://www.reddit.com/r/MachineLearning/.rss` | Research papers & discussion |
| 8 | **OpenAI Blog** | `https://openai.com/blog/rss.xml` | First-party OpenAI news |
| 9 | **Google AI Blog** | `https://blog.google/technology/ai/rss/` | Google / DeepMind AI |
| 10 | **Wired AI** | `https://www.wired.com/feed/tag/ai/latest/rss` | AI long-form & industry |

Edit the list anytime in `agents/fetch_news.py` → `RSS_FEEDS`.

---

## Prerequisites

### 1. X (Twitter) developer app

1. Go to [developer.x.com](https://developer.x.com/) and create a Project + App.  
2. App permissions: **Read and Write**.  
3. Generate:
   - API Key + API Secret  
   - Access Token + Access Token Secret (for the account that will post)  
4. Ensure your plan can call **POST /2/tweets** (Free tier is limited — check current X pricing).

### 2. AWS + Amazon Bedrock

1. IAM user (or role) with permission to invoke Bedrock, e.g.:

   ```json
   {
     "Effect": "Allow",
     "Action": ["bedrock:InvokeModel"],
     "Resource": [
       "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
       "arn:aws:bedrock:*:*:inference-profile/*"
     ]
   }
   ```

2. In the Bedrock console, **enable model access** for Claude Sonnet 4.6 in your region.  
3. Default model ID (US inference profile): `us.anthropic.claude-sonnet-4-6`  
   - Sonnet 4.6 requires an inference profile (base model ID alone fails). Other geos: `eu.` / `jp.` / `global.anthropic.claude-sonnet-4-6`.  
4. Create access keys for the IAM user (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) — or use an existing configured profile.

### 3. GitHub repository secrets

| Secret | Description |
|--------|-------------|
| `X_API_KEY` | X API Key (consumer key) |
| `X_API_SECRET` | X API Secret |
| `X_ACCESS_TOKEN` | User access token |
| `X_ACCESS_TOKEN_SECRET` | User access token secret |
| `AWS_ACCESS_KEY_ID` | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key |
| `AWS_REGION` | e.g. `us-east-1` |

Optional **repository variable** (Settings → Secrets and variables → Actions → Variables):

| Variable | Description |
|----------|-------------|
| `BEDROCK_MODEL_ID` | Override model / inference profile ID |

---

## Setup (step by step)

```bash
# 1. Create repo and push this project
cd x-news-poster
git init
git add .
git commit -m "feat: initial X news poster"
# create empty GitHub repo, then:
git remote add origin git@github.com:<YOU>/x-news-poster.git
git branch -M main
git push -u origin main
```

2. In GitHub → **Settings → Secrets and variables → Actions**, add all secrets listed above.  
3. Enable Actions (if first time).  
4. Open **Actions → Post AI/Tech News to X → Run workflow**.  
   - Use **dry_run = true** first to validate fetch + Claude without posting.  
5. When dry run looks good, run with **dry_run = false** (or wait for the schedule).

### Schedule

Cron (UTC): `0 0,4,8,12,16,20 * * *` → **6 runs per day**, every 4 hours.

> GitHub can delay scheduled workflows by a few minutes under load; that is normal.

---

## Local development

```bash
cd x-news-poster
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Export credentials
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
export X_API_KEY=...
export X_API_SECRET=...
export X_ACCESS_TOKEN=...
export X_ACCESS_TOKEN_SECRET=...

# Pipeline
python agents/fetch_news.py
python agents/analyze_and_generate.py
DRY_RUN=true python agents/post_to_x.py   # safe
python agents/post_to_x.py                # real post
```

---

## Quality checks

Before posting, tweets must:

- Be between ~40 and **280** characters  
- Include the article URL  
- Avoid spam patterns (`click here`, excessive hashtags / emojis / `!!!!`)  
- Not already exist in `posted_news.json`  

Claude is instructed to avoid clickbait and invented facts; the local gate is a second layer.

---

## Costs & limits (rough)

| Service | Notes |
|---------|--------|
| **GitHub Actions** | Free tier is usually enough for 6 short Python jobs/day |
| **Amazon Bedrock** | Claude Sonnet charged per input/output tokens; each run sends ~20–25 headlines |
| **X API** | Posting requires a paid/eligible developer tier — confirm current plan limits |

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `AccessDeniedException` from Bedrock | Model access enabled? IAM `bedrock:InvokeModel`? Correct `AWS_REGION`? |
| Model not found | Try inference profile ID via `BEDROCK_MODEL_ID` |
| X `403 Forbidden` | App must be **Read and Write**; regenerate user tokens after permission change |
| X `401 Unauthorized` | Wrong keys / tokens, or tokens for a different app |
| No candidates | Feeds blocked? Check Actions logs; Reddit sometimes needs a proper User-Agent (already set) |
| History not updating | Workflow needs `contents: write` (set in YAML); branch protection may block bot push |
| Duplicate posts | Ensure `data/posted_news.json` is committed and pushed after each successful post |

---

## Customization

- **Tone / hashtags** → edit the prompt in `agents/analyze_and_generate.py`  
- **Feeds** → `RSS_FEEDS` in `agents/fetch_news.py`  
- **Post count** → Claude returns 1–2; capped in code at 2  
- **Schedule** → cron in `.github/workflows/post-x.yml`  
- **Model** → `BEDROCK_MODEL_ID` or `DEFAULT_MODEL_ID`  

---

## License

MIT — use and modify freely.
