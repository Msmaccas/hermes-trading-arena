---
description: >
  Update an existing trading persona with new rules, principles, or methodology from an external
  source (YouTube interview, blog post, X/Twitter thread, podcast transcript, article, or book
  excerpt). Extracts actionable trading rules and appends them to the persona's .md file in
  .claude/agents/. Never deletes or overwrites original content. Commits and pushes to GitHub.
allowed-tools:
  - yfinance
  - mcp_tradingview_*
  - curl
  - python3
disable-model-invocation: false
help: true
prompt: |
  URL or transcript source, and the persona name to update (e.g., "YouTube interview with Mark Minervini https://... into minervini" or "Buffett annual letter https://... into buffett"):
---

# /update-trading-persona — Update a Trading Persona from New Source Material

## When to run
- You discover new interview, lecture, or written content from an existing persona
- A persona publishes a new book, whitepaper, or updated methodology
- You want to extract rules from a YouTube video or X/Twitter thread for a new or existing persona
- You need to refresh a persona's approach for the current market regime

## Workflow

### 1. Parse the Input

Accept either:
- **URL** (YouTube, X/Twitter thread, blog, podcast transcript, article) — fetch and extract content
- **Direct text** (paste transcript, article, book excerpt)

Identify:
- **Persona name** to update (the key from step 2)
- **Source type**: YouTube interview / X thread / blog post / podcast / book excerpt / annual letter
- **Date of source material**
- **Original author/speaker** (confirm it matches the persona)

### 2. Validate Persona Exists

Check `.claude/agents/` for the target persona file:

```
Personas available:
- oneil (William O'Neil)
- buffett (Warren Buffett)
- lynch (Peter Lynch)
- minervini (Mark Minervini)
- qullamaggie (Kristjan Qullamaggie)
- david-ryan (David Ryan)
- matt-caruso (Matt Caruso)
- brian-shannon (Brian Shannon)
- dan-zanger (Dan Zanger)
- nick-schmidt (Nick Schmidt)
```

If the persona doesn't exist, ask: "Create a new persona from this source? You'll need to provide a `name` and `description` for the YAML frontmatter."

### 3. Extract Source Content

- **For YouTube**: Use Python with `yt-dlp` or `youtube-transcript-api` to extract transcript/captions
- **For X/Twitter threads**: Fetch thread via API or scrape the content
- **For blog posts/articles**: Use `curl` to fetch HTML and extract main content
- **For direct text**: Use as-is

```python
# Example: Extract YouTube transcript
import json, sys
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    video_id = sys.argv[1].split('v=')[-1].split('&')[0]
    transcript = YouTubeTranscriptApi.get_transcript(video_id)
    text = ' '.join([entry['text'] for entry in transcript])
    print(text)
except ImportError:
    print("ERROR: youtube_transcript_api not installed. Install with: pip install youtube-transcript-api")
```

### 4. Extract Actionable Rules/Principles

From the source material, extract specific, **actionable** trading rules or principles. Filter for:

- **Trading rules**: Specific entry/exit criteria, position sizing formulas, risk management rules
- **Screening criteria**: Quantitative or qualitative screens the persona uses
- **Market analysis methodology**: How they assess market direction, sector rotation, regime
- **Risk management**: Stop-loss placement, position size limits, portfolio-level risk controls
- **Psychological principles**: Mindset, discipline, emotional control rules
- **New developments**: Updates to their methodology (e.g., "I've changed my stop-loss from 8% to 5%")
- **Direct quotes**: Verbatim quotes that capture their philosophy (add to their QUOTE DATABASE)

**Exclude**:
- General market commentary without specific rules
- Personal anecdotes about past trades (unless they illustrate a new rule)
- Repetition of rules already in the persona file (deduplicate)

### 5. Format New Rules for Append

Structure the new rules in this format:

```markdown
## Addendum: [Source Title] — [Date]

*Source: [URL or citation] — [date accessed]*
*Extracted: [current date]*

### New Rules

1. **[Rule Title]**: [Full rule description with specific criteria, thresholds, and conditions]
   - Context: [When/how this rule applies, what market conditions]
   - Example: [Concrete example from the source, if provided]

2. **[Rule Title]**: ...

### Updated Screening Criteria

- [Criterion 1]
- [Criterion 2]

### New Quotes for Quote Database

> "[Verbatim quote]" — [Persona Name], [Source], [Date]

> "[Verbatim quote]" — [Persona Name], [Source], [Date]

### Methodology Changes

- **Change 1**: [Old approach → New approach with rationale]
- **Change 2**: [Old approach → New approach with rationale]

### Conflicts with Existing Principles

If any new rule contradicts an existing rule in the persona file:
- **Existing Rule**: [Quote from current persona file]
- **New Rule**: [Quote from new source]
- **Reconciliation**: [How to resolve — did the persona change their mind? Is this market-specific?]
```

### 6. Append to Persona File

Use the append operation:
- Read the current persona file
- Append the new addendum section
- **Never delete or modify** any existing content
- Add a horizontal rule `---` before the addendum as a visual separator
- Only add genuinely new rules — skip content that's already covered

### 7. Verify Append

- Confirm the file still has valid YAML frontmatter
- Confirm the original content is intact
- Confirm the new addendum appears at the end
- Run any available linting on the markdown

### 8. Git Commit and Push

```bash
cd ~/hermes-trading-arena
git add .claude/agents/<persona>.md
git commit -m "Update <persona> persona: add rules from <source-type> (<date>)"
git push origin main
```

### 9. Confirm to User

Report back:
- **Persona updated**: [name]
- **Source**: [URL/title]
- **New rules extracted**: [count]
- **New quotes extracted**: [count]
- **Methodology changes**: [summary of any changes flagged]
- **Conflicts resolved**: [any existing rule conflicts and resolution]
- **Git commit**: [commit hash]
- **File**: `.claude/agents/<persona>.md`
