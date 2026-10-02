# Install checklist

The real test of the README is a second person installing Tumnis from it alone, on their own machine, with nobody helping (PRD, phase 4 exit). This file is where they record how it went. Each blocker they hit becomes a README fix and, where it can be checked automatically, a new tagged block that the README job (`.github/workflows/readme.yml`) runs from then on. Row 3 of the [release checklist](RELEASE-CHECKLIST.md) links the entry below.

## How to do it

1. Use a machine that has never run Tumnis: a fresh VM or server that meets the README's [Requirements](../README.md#requirements).
2. Follow [README.md](../README.md) from Requirements to First run, and nothing else. Do not read the code or ask the author.
3. Time each step from its first command to its last. Write down everything that confused you, failed, or needed a guess, even when you worked it out.
4. Add your entry under Entries and open a pull request with it.

## Steps the README job cannot run

CI runs every block tagged `readme:install`, `readme:env` and `readme:first-run` on a fresh Ubuntu 24.04 VM. These steps are tagged `readme:manual` because they need an account, a second device or a person, so only a human install proves them:

| Step | README section | Why it is manual |
| --- | --- | --- |
| Clone the repository | Install, 2. Get the code | CI checks out the commit under test instead |
| Bind to the Tailscale address and fetch the Tailscale certificate | Install, 6. Get an HTTPS certificate | Needs a tailnet with HTTPS certificates turned on |
| Install with Coolify | Install, With Coolify instead | Needs a Coolify server |
| Scan the QR code with an authenticator app and sign in from a browser | First run | Needs a phone and a person (CI signs in with the headless block instead) |
| Open Tumnis on the phone, add it to the Home Screen and turn on push | On the phone | Needs a phone on the tailnet |
| Create a runner, install the daemon and the Hermes profiles | Agents | Needs an agent server with Hermes |
| Turn on backups to Backblaze B2 | [OPERATIONS.md, Backups](OPERATIONS.md#backups) | Needs a B2 account and keys |

## Entry template

Copy this under Entries and fill it in.

```markdown
### <date>: <name or handle>

- Machine: <OS and version, CPU, memory; VM or bare metal>
- Path: <self-signed localhost, or Tailscale>; <Docker and Compose versions>
- Total time: <minutes, from the clone to signed in>

| Step | Minutes | Friction |
| --- | --- | --- |
| Requirements | | |
| 1. Check the tools | | |
| 2. Get the code | | |
| 3. Choose the address | | |
| 4. Create the keys | | |
| 5. Write the settings | | |
| 6. Get an HTTPS certificate | | |
| 7. Build and start | | |
| 8. Check that it answers | | |
| First run | | |
| On the phone | | |
| Agents (optional) | | |

Blockers (each with its fix PR, or "open"):

- <none>
```

## Entries

pending: no second-person install has been recorded yet. Release checklist row 3 stays pending until one is, with no open blockers.
