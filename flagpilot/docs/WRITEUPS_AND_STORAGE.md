# Solution Writeups and Encrypted Storage

Completed CLI and Web attempts generate an English solution writeup from the
recorded trace. Generation uses no additional model request. The report contains
the task, model outputs, tool observations, submitted script, replay outcome,
and recorded usage. Failed and no-gold runs retain their actual verdicts.

## Writeups

The default artifact is `<run_dir>/writeup.md`. The Web result panel provides
**Download writeup**, including when reopening a saved run after a server restart.
Downloads are served as attachments and are not cached by the server response.

To generate a report for an older completed run, from the application directory:

```bash
python3 -m flagpilot writeup --run-dir runs/example/crypto-005-s0
```

The command also accepts `--out PATH`. In encrypted mode, saved artifacts use
an additional `.enc` suffix; the Web download decrypts the selected report in memory.
A downloaded report contains the original evidence and may contain its flag.

## Enable encryption

The standard-library runtime remains available without encrypted storage.
Install the optional dependency to use Fernet authenticated encryption:

```bash
python3 -m pip install -r requirements-encryption.txt
python3 -m flagpilot storage-keygen --out "$HOME/.config/flagpilot/storage.fernet.key"
export FLAGPILOT_ENCRYPTION_KEY_FILE="$HOME/.config/flagpilot/storage.fernet.key"
```

The key command creates a new file with mode `0600` and refuses to overwrite an
existing key. Keep the key outside the repository and the `runs/` directory,
and keep a separate secure backup. The API credential and this encryption key
serve different purposes; do not use the API credential as an encryption key.

Set `FLAGPILOT_ENCRYPTION_KEY_FILE` for every CLI/Web process that accesses the
encrypted runs. A missing key, wrong key, or modified ciphertext produces an
explicit error. It does not fall back to returning ciphertext as a result or
append plaintext records to an existing encrypted log.

Fernet is provided by the maintained `cryptography` package. The application
does not implement its own cipher. See the [Fernet documentation](https://cryptography.io/en/latest/fernet/).

## Stored data

With the key configured:

- Each complete trace, batch result, recall-probe result, chat message, and
  memory record is encrypted before it is appended to its JSONL file. This
  covers copies of a flag inside model output, observations, and replay stdout,
  as well as the `flag` field itself.
- Writeups are stored as `writeup.md.enc`.
- On normal attempt completion, remaining files in `work/` and `replay/` are
  encrypted in place with an `.enc` suffix. Symlinks are not followed.
- Live events and browser results remain available to the local user in memory.
  Existing metrics, failure classification, batch resume, and saved-run replay
  read encrypted records through the same storage decoder.

Task inputs and user-uploaded attachments are not an encrypted dataset. Tools
need plaintext working files during execution. An interrupted process can leave
active scratch files on disk; rerun history protection after stopping the server
to protect completed workspaces. Encryption at rest is separate from sandbox
isolation and Web authentication. Use the existing localhost-only deployment.

Without the key setting, new runs retain the original plaintext format. Legacy
plaintext records remain readable with either mode enabled or disabled.

## Protect existing history

Stop the Web server and any batch jobs first. Configure the encryption key,
then explicitly migrate existing run records and completed workspaces:

```bash
python3 -m flagpilot encrypt-history --root runs
```

JSONL files are replaced atomically with mode `0600`; malformed records cause an
error rather than being discarded. The command can be rerun. It does not rewrite
external task datasets or unrelated documents. Previously exported reports and
copies of old runs need to be managed separately.

## Validation

The focused tests use synthetic flags, mock model calls, and local HTTP servers:

```bash
python3 -m unittest tests.test_storage_writeup tests.test_writeup_web -q
```

They cover round trips, missing/wrong keys, modified ciphertext, legacy records,
resume/upsert, history migration, workspace sealing, failed/no-gold reports,
downloads, invalid run IDs, and recovery after clearing in-memory server state.
