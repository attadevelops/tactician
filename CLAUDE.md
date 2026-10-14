# Working on Tactician

Tactician is a product repo that runs on several Macs, each working for a different company. Read `README.md` first.

- **No tenant data in the repo, ever.** Client names, transcripts, contract details, eval files, wrap-ups and
  call notes live in the tenant folder (`~/.tactician`, or `$TACTICIAN_HOME`). Tests and examples use invented names.
  The `pre-push` hook (`git config core.hooksPath hooks`, set by `install.sh`) refuses pushes that contain the
  tenant's `denylist` terms or a private history in `blocked-roots`; never bypass it with `--no-verify`.
- **Branches.** The main development Mac commits to `main`. Any other machine works on a branch and opens a pull
  request (`gh pr create`), or files an issue (`gh issue create`) when the change is not for now. Use the personal
  GitHub account on every machine.
- **Releases.** Machines update themselves to the newest `v*` tag (menu bar: Check for Updates, also every 6 hours,
  never during a call, never over local changes). Cut one from `main` once the checks pass:
  `gh release create vX.Y.Z --generate-notes`.
- **Checks.** `python3 test_tactician.py` and `python3 evals/judge.py --selftest` must pass. A prompt change also needs
  one judged replay (README, "Evals"): keep it only if recall holds and the targeted behaviour improved.
- **Prompt rules are principles.** Facts about a project or company belong in that tenant's context map or digest.
