# OpenRappter Release Train

Flight deck for the OpenRappter release rings. Static files only — no server,
no build step.

    canary -> nightly -> alpha -> beta -> stable

Dashboard: https://kody-w.github.io/openrappter-release-train/

## Rings

| Ring | npm dist-tag | Cut by |
|---|---|---|
| canary | `canary` | every push to `main` |
| nightly | `nightly` | daily, only when `main` moved |
| alpha | `alpha` | hand-promoted from nightly |
| beta | `beta` | hand-promoted from alpha |
| stable | `latest` | released via `release.yml` |

Only `stable` owns `latest`, so a plain `npm install -g openrappter` can never
receive a prerelease.

## Install a ring

```sh
curl -fsSL https://kody-w.github.io/openrappter/install.sh | bash -s -- --channel beta
```

The ring is remembered in `~/.openrappter/channel`, so upgrades stay on the
same train car. An exact `OPENRAPPTER_VERSION` always wins over the ring.

## Promotion

Promotion re-points an npm dist-tag at an already published version. It never
rebuilds and never republishes — the bytes that enter at canary are the bytes
that reach stable. `stable` is deliberately not reachable by dist-tag
promotion; shipping stable means publishing the real release version.

## Static API

| Endpoint | Purpose |
|---|---|
| `api/v1/channels.json` | ring → dist-tag → version + install commands |
| `api/v1/registry.json` | train topology and source repo |
| `api/v1/status.json` | per-ring occupancy |

Regenerated from the live npm registry by `scripts/ring-cli.mjs manifest`
in [kody-w/openrappter](https://github.com/kody-w/openrappter).
