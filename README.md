# vstack

Personal agent skills, packaged as one plugin (`vstack`) for Claude Code, Codex, GitHub Copilot and Antigravity.

- `babysit`: watch a PR until it's green and approved, fixing and replying as needed (`--merge` to merge)
- `html-plan`: write an implementation plan as one interactive HTML page
- `remove-comments`: delete comment noise from a change and check that no code changed
- `review`: read-only multi-lens code review of a PR, branch or path

## Install

### Claude Code

```
claude plugin marketplace add VaggelisKa/vstack
claude plugin install vstack@vaggeliska
```

Use `/vstack:<skill>`. Update with `claude plugin marketplace update vaggeliska` then `claude plugin update vstack@vaggeliska`, remove with `claude plugin uninstall vstack@vaggeliska`.

### Codex

```
codex plugin marketplace add VaggelisKa/vstack
codex plugin add vstack@vaggeliska
```

Use `$<skill>`. Update with `codex plugin marketplace upgrade vaggeliska`, remove with `codex plugin remove vstack@vaggeliska`.

### GitHub Copilot

CLI:

```
copilot plugin marketplace add VaggelisKa/vstack
copilot plugin install vstack@vaggeliska
```

Verified with Copilot CLI 1.0.104 (add, install, update, uninstall, marketplace remove). Update with `copilot plugin marketplace update vaggeliska` then `copilot plugin update vstack@vaggeliska`, remove with `copilot plugin uninstall vstack`. Invoke a skill by name, e.g. `/review` (per the docs; not run in a session).

VS Code (experimental, untested): add `"chat.plugins.marketplaces": ["VaggelisKa/vstack"]` to settings (needs `chat.plugins.enabled`), or install from the Extensions view (`@agentPlugins`), or run "Chat: Install Plugin From Source" with the Git URL; skills appear as `/vstack:<skill>`.

Cloud agent (untested): in the target repo's `.github/copilot/settings.json`, set `extraKnownMarketplaces` to `{"vaggeliska": {"source": {"source": "github", "repo": "VaggelisKa/vstack"}}}` and `enabledPlugins` to `{"vstack@vaggeliska": true}`.

A project or personal skill with the same name silently overrides the plugin's.

### Antigravity

```
agy plugin install https://github.com/VaggelisKa/vstack
```

Use `/vstack:<skill>`. Update by uninstalling and installing again; remove with `agy plugin uninstall vstack`.

## Docs

- [Contributing and layout](docs/contributing.md)
- [review](docs/review.md) and [remove-comments](docs/remove-comments.md): what's tested, and known limits

## License

Apache-2.0 for this repository's own work (see `LICENSE`). Bundled skills keep their upstream licenses; see `NOTICE`.
