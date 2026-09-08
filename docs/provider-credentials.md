# Getting credentials for each provider

One page per provider: where to go, what to click, the one command that stores it, and the one command that proves it works. Prices are anchors as of September 2026 — check the vendor's page before relying on them.

Check everything at once with:

```bash
vocalize auth status
```

## The keychain on macOS

Every stored key lives in your login keychain under the service name
`vocalize`. Since 0.12.0 vocalize writes and reads those items through
Apple's own `/usr/bin/security` tool rather than from its Python process.
The difference matters: macOS pins a keychain item to the binary that
created it, so a key stored from your terminal used to be invisible, or
behind an "Allow" dialog, when a different Python asked for it — the one
inside Claude Code's shell, or the one left by `uv tool upgrade`. With the
tool doing the reading, the accessing application is always the same
Apple-signed binary, whatever spawned it, and the dialog never comes for
an item vocalize 0.12.0 or later stored. An item an older vocalize left
behind is still pinned to whichever Python wrote it, so the first read
after upgrading may raise one "Allow" dialog until you re-run
`vocalize auth login`.

- The secret never appears on a command line: the tool reads its command
  from stdin.
- Each item carries the date the key was last validated, shown by
  `vocalize auth status` as `validated YYYY-MM-DD`.
- A key stored by an older vocalize is deleted and re-stored the next time
  you run `vocalize auth login`, which is what clears the older pinning; no
  manual step.
- If a key contains a double quote or a backslash, vocalize refuses to
  store it rather than risk the tool misreading the line.

## Anthropic (dictation cleanup and notes — not a voice)

The one key that is not a voice. It powers the `anthropic` backend of
`[stt] cleanup` and, from 0.14, the notes summariser. It never appears in
the chain, and `vocalize chain` does not accept it. If you already use
Claude Code, the `claude-cli` backend does the same job through the
`claude` binary with no key at all; this slot is for a Mac without it.

1. Sign in at the Anthropic Console and create an API key (Settings →
   API keys). Copy it once — the console never shows it again either.
2. Store it. The key is checked with Anthropic before it is written:

   ```bash
   vocalize auth login --provider anthropic
   ```

3. Prove it:

   ```bash
   vocalize auth status --provider anthropic
   ```

4. Turn it on — in `~/.config/vocalize/config.toml`:

   ```toml
   [stt]
   cleanup = "anthropic"
   ```

   or pick it in the Cleanup select on the Local tab of `vocalize portal`.

Or do steps 2 and 3 on the Keys tab of `vocalize portal`: **Test without
storing** checks a key and keeps nothing; **Store this key** checks it and
writes it to the keychain; **Remove stored key** forgets it, and reads the
keychain back before saying so.

**The budget.** `[providers.anthropic] monthly_chars` defaults to
2,000,000 characters a month — a few hundred dictated takes — and vocalize
stops sending when the ledger reaches it. The ledger is this Mac's own
count, not the account's: two Macs with the same key each get the full
budget, and nothing here reads Anthropic's billing.

**What leaves the machine.** Every take sent for cleanup prints one line
on stderr, `vocalize: sent to anthropic`, and the notification names it
too. Nothing is sent when `cleanup` is `off` or `local`.

Set the order providers are tried in (first is primary, the rest are fallbacks):

```bash
vocalize chain elevenlabs google say
```

## Google Cloud Text-to-Speech (already set up on this Mac)

**Done for you:** the Text-to-Speech API is enabled on project `mathew-hager-program`, and an API key named `vocalize-tts` exists that can call *only* that API. The key string was stored straight into the keychain and never printed.

- **Free tier:** about 4 million Standard or 1 million WaveNet characters a month, renewing monthly. Beyond that Google bills automatically — it does not stop.
- **Budget guard:** your config sets `monthly_chars = 900000` under `[providers.google]`, so vocalize stops using Google for the month before the free tier runs out.
- **If the key is ever lost:** Google Cloud console → APIs & Services → Credentials → `vocalize-tts` → Show key, then `vocalize auth login --provider google` and paste it.
- **Optional hard cap:** IAM & Admin → Quotas & System Limits → Cloud Text-to-Speech → set a requests-per-day override. Google has no native spend stop.

Test:

```bash
vocalize speak "Google works" --provider google
```

## OpenAI

No free tier; prepaid credit only. Roughly **$15 per million characters** on `tts-1`; `gpt-4o-mini-tts` is billed by token and lands in the same range.

1. Go to https://platform.openai.com/ and sign in.
2. **Settings → Billing → Add to credit balance.** Put in a small amount ($5–10 is plenty for testing).
3. **API keys** (left sidebar) → **Create new secret key**. Name it `vocalize`. Copy it once — it is shown only once.
4. Store it:

```bash
vocalize auth login --provider openai
```

5. Add to `~/.config/vocalize/config.toml`:

```toml
[providers.openai]
voice = "marin"
model = "gpt-4o-mini-tts"
```

Test:

```bash
vocalize speak "OpenAI works" --provider openai
```

Voices to try: `marin`, `cedar` (best), `nova`, `alloy`, `onyx`. List them with `vocalize voices --provider openai`.

## Amazon Polly

**Free tier:** Standard voices 5 million characters a month, ongoing. Neural voices 1 million a month for the first 12 months. After that: Standard $4, Neural $16 per million characters. Beyond the free tier AWS bills automatically.

Polly uses your normal AWS credentials — vocalize does **not** store them; the `aws` tool does.

1. Install the extra and the AWS CLI:

```bash
pip install "vocalize-cli[polly]"
```

```bash
brew install awscli
```

2. AWS console → **IAM → Users → Create user**. Name: `vocalize-polly`. No console access.
3. **Permissions → Attach policies directly → Create policy → JSON**, paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["polly:SynthesizeSpeech", "polly:DescribeVoices"],
    "Resource": "*"
  }]
}
```

Name it `vocalize-polly-speak`, attach it, finish creating the user.

4. Open the user → **Security credentials → Create access key → Command Line Interface (CLI)** → download or copy the key pair once.
5. Store it under a named profile:

```bash
aws configure --profile vocalize
```

Enter the access key, the secret, region `us-east-1`, output `json`.

6. Add to `~/.config/vocalize/config.toml`:

```toml
[providers.polly]
voice = "Matthew"
engine = "neural"
region = "us-east-1"
profile = "vocalize"
monthly_chars = 900000
```

Test:

```bash
vocalize speak "Polly works" --provider polly
```

List voices with `vocalize voices --provider polly`.

## Kokoro (local, free, offline)

Nothing to sign up for. One command downloads the model (~354 MB) after showing you exactly what it will fetch:

```bash
vocalize local install
```

Then:

```bash
vocalize speak "Kokoro works" --provider kokoro
```

With Kokoro or `say`, no text leaves the machine.

## macOS `say`

Built in. No setup. Put it last in your chain as the safety net:

```bash
vocalize chain elevenlabs google kokoro say
```
