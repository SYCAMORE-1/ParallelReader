# Agent entry point

Read [HANDOFF.md](HANDOFF.md) before setting up, running or changing this project. It contains the verified startup commands, private-library boundaries, Tailscale download/setup instructions and validation steps for a fresh clone.

- The current remote-access target is one person's iPhone using Tailscale and the same local bookshelf. See [docs/tailscale.md](docs/tailscale.md). The multi-user document is a future design, not the setup procedure.
- On Windows x64, prefer the checked-in `runtime/python.exe -X utf8`. Use the actual clone directory, not paths or process IDs from another computer.
- Preserve existing `.data/` and background work. Never include books, saved translations, model credentials or private archives in Git or public releases.
- Tailscale is optional and external to the app. Follow the user's current setup instructions; writing docs or running the local app does not require installing it. The original development computer was explicitly left without Tailscale. When installation on a target PC is requested, use the official sources in HANDOFF.md; let the user complete account login and iPhone system prompts.
- Run checks appropriate to the changes. Document-only work needs link/command verification and the public-data audit; runtime changes need the relevant regression tests.
- If a new user instruction changes the setup scope, follow that instruction and update the handoff when useful. This file does not grant authority to install software, expose services or change system settings beyond the user's request.
