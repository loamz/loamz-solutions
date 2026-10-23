# Mail Concierge

Ranks your mail by how much it matters, tells you what to do with each message, and answers questions about it.

## Try it now (no credentials)
```bash
uv sync
uv run loamz-solutions mail demo                              # a synthetic inbox: sync, triage, digest
uv run loamz-solutions mail --demo ask "who is waiting on me?"
```

## Connect your mailbox
1. Create an **app password** for your mail account (Gmail: Google Account → Security → 2-Step Verification → App
   passwords; Outlook and iCloud have the same). Use IMAP, not your login password.
2. Copy `.env.example` to `.env` and fill `MAIL_IMAP_HOST`, `MAIL_USER`, `MAIL_PASSWORD`. Add `MAIL_VIPS` (people or
   `@domains` that always matter), `MAIL_KEEP` (lists you never want buried) and `MAIL_ABOUT` (one or two sentences about
   your role and what you are waiting for: the model uses it).
3. Add `LLM_API_KEY` for the model. Without it rules alone rank your mail, and everything they cannot decide is "normal".

```bash
uv run loamz-solutions mail sync              # read the last 2 days, triage what is new
uv run loamz-solutions mail digest            # print the digest; add --send to mail it to yourself (set SMTP_HOST)
uv run loamz-solutions mail ask "what did Priya say about the contract?"
uv run loamz-solutions mail watch --every 600 # sync every 10 minutes
```

## How a message is ranked
1. **Rules first**, each with a stated reason: a VIP sender, a meeting invitation, a security notice, a payment problem, a
   reply to a conversation you are in, bulk or automated mail (unsubscribe header, `no-reply` sender), receipts, a deadline
   in the text. Rules are free, instant and explainable.
2. **The model second**, only for what the rules cannot decide, with your `MAIL_ABOUT` sentence. It returns a priority, a
   category, an action and a reason, and its answer is clamped to the allowed values.
3. **Priorities:** urgent (today), important (this week), normal, low (skim or file), noise (archive or unsubscribe).

## What it does and does not do
- It is **read-only**: the folder is opened read-only and messages are fetched with `BODY.PEEK`, so nothing is marked read,
  moved or deleted. The digest suggests actions; you take them.
- Everything is stored locally in `.state/mail/` (a SQLite index with full-text search, and a hash-chained ledger of what
  was triaged and why). Message text is sent to the model endpoint you configure for messages the rules cannot decide and
  for questions; nothing else leaves your machine. Use a local OpenAI-compatible server (`LLM_BASE_URL`) to keep it all
  on your machine.
- Important messages can also be remembered in Loamz memory (`LoamzMemorySink`), so any agent on your cell can recall
  them under the same tenant and audit rules.

## Tuning
Add senders to `MAIL_VIPS` or `MAIL_KEEP` and re-run `sync` after clearing `.state/mail`. Rules live in
`loamz/solutions/mail/triage.py` and each has a test in `tests/test_mail_concierge.py`; add a rule and a test together.
