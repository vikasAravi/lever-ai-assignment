# assignment-pack

Everything you need to run the Founding Engineer assignment locally. Read
`ASSIGNMENT.pdf` first; this file only explains what is in the box and how to
start it. Anything unclear, or any question at any point: careers@uselever.ai.

## What is in the box

| Path | What it is |
|---|---|
| `docker-compose.yml` | GLPI 11.0.12 (pinned), MariaDB 11.4, and the fault proxy. GLPI's own port is not published. |
| `seed/` | The seeded organisation, loaded by MariaDB on its first start only. |
| `init/` | A small GLPI script that installs your API tokens from `.env`, run by the `glpi-init` service at every start. GLPI keeps tokens encrypted with a per-instance key, so the seed cannot carry them. |
| `proxy/` | The fault proxy: `proxy.py`, its `Dockerfile`, `config.yml` (seed and rates) and `README.md`. Faults are written to `proxy/logs/faults.log` while you develop. |
| `workbook.xlsx` | Agent A's input. Six sheets, required columns marked `*`, blank means unset. |
| `inventory.csv` | Agent B's input. The first line names the entity it was taken in. |
| `.env.example` | The names of the credentials. The values were sent to you separately. |

## Start

```
cp .env.example .env            # paste the values you were sent
docker compose up -d --build    # first start takes one to three minutes
```

`docker compose ps -a` will show two services that have exited with code 0,
`glpi-configure` and `glpi-init`. That is expected: they are one-shot steps that
write GLPI's database config and install your API tokens, and they run again
harmlessly at every `up`.

Then:

- UI: http://localhost:8080 — sign in with the UI admin account from `.env`.
- Legacy REST API: http://localhost:8080/apirest.php (App-Token plus a user token;
  two API accounts exist, `onboarding.bot` and `inventory.bot`, one per role).
- High-level API: http://localhost:8080/api.php
- Proxy health: http://localhost:8080/_proxy/health

Check the seed loaded: the UI's entity selector shows `Root entity > Acme > India`
and `Root entity > Acme > Europe`, and Assets > Computers lists 40 machines in
`India > Engineering`.

## Reset to the seeded state

```
docker compose down -v && docker compose up -d --build
```

`down -v` deletes the database volume; the seed and your `.env` credentials are
applied again on the next first start.

## Rules

- Do not modify, bypass or disable the proxy. You may change the proxy `seed` in
  `proxy/config.yml` for your own testing. Leave the rates alone.
- Do not modify GLPI or change its image tag. Reading GLPI's source is encouraged.
- Point your browser and your code at the proxy, nothing else.
- Never commit `.env`.
