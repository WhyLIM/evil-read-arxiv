---
name: biomed-papers
description: Search biomedical literature with PubMed and Europe PMC, then generate an Obsidian recommendation note
---

# Goal

Help users search biomedical literature relevant to their interests and save a recommendation note into their Obsidian vault.

# Scope Check

Use this skill when the user's request is about biomedical literature or biomedical sources, especially if they mention PubMed, Europe PMC, cancer, immunology, clinical studies, disease, genes, proteins, drugs, single-cell biology, or medical research.

Do not use this skill for general arXiv or computer science paper discovery. If the request is about AI, machine learning, NLP, computer vision, robotics, multi-agent systems, or a generic "start my day" workflow without biomedical intent, route it to `start-my-day` instead.

If the request combines biomedical and computer science topics, prefer this skill when the expected paper source is PubMed/Europe PMC or when the user's topic is primarily biomedical. Ask one concise clarification question only when the intended source or domain is ambiguous.

# Workflow

## Step 1: Load configuration

Read `biomed-papers.yaml` from this skill directory. The config defines:

- `vault_path`
- `keywords`
- `excluded_keywords`
- `lookback_days`
- `top_n`
- `article_types`

## Step 2: Search biomedical literature

Run:

```bash
cd "$SKILL_DIR"
python scripts/search_biomed_papers.py \
  --config "$SKILL_DIR/biomed-papers.yaml" \
  --output biomed_papers_filtered.json
```

The script:

- searches PubMed for recent papers
- fetches metadata and abstracts
- enriches records with Europe PMC when available
- applies lightweight scoring and deduplication
- writes a recommendation note to `10_Daily/`

## Step 3: Read the output

Review `biomed_papers_filtered.json` and summarize the most relevant papers for the user.

# Output

The script produces:

- `biomed_papers_filtered.json`
- `10_Daily/YYYY-MM-DD-biomed-papers.md`

# Rules

- Keep this skill independent from `start-my-day`
- Do not modify existing paper workflows when using this skill
- Treat Europe PMC enrichment as optional enhancement, not a hard requirement
