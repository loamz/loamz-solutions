# Data Concierge

You have files in several folders, years of mail, and perhaps a Loamz deployment. The Concierge answers: where does my data
live, what is in it, what can I do with it, and what does it say about X?

## Try it now
```bash
uv run loamz-solutions data demo                                  # sample files + the synthetic inbox: the map and ideas
uv run loamz-solutions data --demo ask "what is the indemnity cap, and is anything unresolved?"
```

## Point it at yours
Set `DATA_FOLDERS` (comma separated) in `.env`; add the mail settings and/or `LOAMZ_ENDPOINT` to include those sources.
```bash
uv run loamz-solutions data scan        # index the folders (unchanged files are skipped next time)
uv run loamz-solutions data map         # where your data lives: counts by type, newest files, what it cannot read yet
uv run loamz-solutions data search "northwind contract"
uv run loamz-solutions data ask "what did we agree about the Northwind contract?"
uv run loamz-solutions data suggest     # specific things worth doing with what you have
```

## What it reads
Text, Markdown, reStructuredText, YAML/TOML/INI, source files, HTML, JSON, CSV/TSV (a summary of columns and the first rows).
PDF, Office documents and images are **counted and listed on the map as "not readable yet"**, never silently skipped: add an
extractor and they join the index. Hidden folders, build output and files over 1 MB are skipped.

## How answers are made
Search is full text (SQLite FTS5). A question retrieves the best matches **from each source**, so files cannot crowd out mail,
and a model writes the answer from those excerpts only, citing them. With no model configured you get the matches. It reads
and never writes: your files, mail and Loamz data are untouched. Excerpts go to the configured model endpoint only when you
ask a question; use a local OpenAI-compatible server to keep everything on your machine.
