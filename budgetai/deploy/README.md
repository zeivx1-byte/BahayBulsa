# Deploy BudgetAI from GitHub

GitHub Actions tests the code and publishes the API image. An always-on Linux
server runs the API and Ollama. Caddy provides HTTPS and requires a password for
the API and interactive docs. The model and API ports stay inside Docker.

## Publish the image

1. Add the project and its workflows to the team's GitHub repository.
2. Let **BudgetAI checks** pass on the code you want to deploy.
3. In **Actions**, select **Publish BudgetAI image** and click **Run workflow**.
   Use the image reference beginning with `ghcr.io/` and ending in `@sha256:…`
   from the workflow summary. It identifies the exact build the host will run.

The [GitHub Container Registry guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
describes access for private images. On the server, use `docker login ghcr.io`
with an account that can read the package and a GitHub token scoped to
`read:packages`. Enter the token at the password prompt.

## Start the server

Use a Linux server with Docker Engine and Compose v2, enough memory to load the
model, and persistent disk for the downloaded weights. Point the API hostname
at the server and allow inbound TCP ports 80 and 443 for Caddy. The server needs
outbound access to download images and the Ollama model.

Clone the GitHub repository on the server, go to this `deploy` directory, and
create the local settings file on the first deployment.

```bash
umask 077
cp .env.example .env
docker run --rm -it caddy:2-alpine caddy hash-password
```

Edit `deploy/.env` with the bare hostname in `BUDGETAI_DOMAIN`, the published
digest reference in `BUDGETAI_IMAGE`, and a login in `BUDGETAI_USERNAME`. Put the
bcrypt hash from Caddy in `BUDGETAI_PASSWORD_HASH` and keep its single quotes so
Compose preserves the dollar signs. Keep the actual password in the team's
password manager. The default model is `qwen3.5:4b-q4_K_M` with a 4,096-token
context. Set `BUDGETAI_MODEL` and `BUDGETAI_CONTEXT_TOKENS` in `deploy/.env` to
override these settings. On an existing deployment, update any saved model
setting to select the new default.

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

On the first start, the stack downloads the configured model and waits for its
download to succeed before starting the API. Named volumes keep Ollama model
files and Caddy certificate data across container restarts. [Compose startup
order](https://docs.docker.com/compose/how-tos/startup-order/) · [Caddy automatic
HTTPS](https://caddyserver.com/docs/automatic-https/)

## Verify from another device

1. Open `https://` followed by your API hostname and `/docs`. Sign in with the
   credentials you configured. An unauthenticated request should receive `401`.
2. Run **POST /budget** with the [fictional household from the local API guide](../README.md#phase-7-local-api)
   and confirm the expected allocations. In **POST /chat**, try the provided rent
   increase example and confirm both the original and hypothetical plans are returned.
3. Repeat a successful budget request with your development laptop switched
   off. That's the check to pass before removing the laptop's model copy.

`GET /health` checks that the API process responds. A successful `/budget`
request confirms the model is available. To diagnose startup, run
`docker compose logs --tail 50 caddy api ollama ollama-init` on the server.

## Update or roll back

Publish the new Git commit, record the currently deployed image reference, and
replace `BUDGETAI_IMAGE` in the server's `.env` with the new digest. Run
`docker compose pull api`, then `docker compose up -d` and repeat the HTTPS
checks. To roll back the API, restore the previous digest and run the same
commands.
