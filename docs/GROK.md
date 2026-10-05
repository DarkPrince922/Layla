# Grok account connection

In Settings → Providers, select **Connect Grok**. Layla opens xAI's device authorization page and displays the confirmation code. Sign in to the account with your Grok subscription, check the code and approve access. Layla detects approval and loads Grok chat models into the existing model picker. No password or API key is entered into Layla.

The account's xAI entitlements and limits apply; signing in does not guarantee access to every model. If model discovery fails after a successful login, the encrypted connection remains saved. Use the provider's **Загрузить модели** action to retry. To change accounts or renew a revoked login, select Connect Grok again. Delete the provider to remove locally stored account credentials.

Run the normal database migration (`alembic upgrade head`) when deploying this change. Revision `0021_grok_login` stores pending device grants so polling works across processes and restarts. Pending device codes, access tokens and refresh tokens are encrypted using the existing Layla secret key. Tokens are never returned to the browser. Refresh tokens rotate before expiry and are saved before inference.

The implementation follows the public device grant in the official [Grok CLI authentication guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md) and [device grant implementation](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-login/src/device_code.rs). It uses the CLI's public OAuth client by default. `LAYLA_GROK_CLIENT_ID` can select a separately registered xAI client with device-grant access. Account credentials use xAI's token-auth header and the Responses endpoint for chat and tools; normal API-key providers keep their existing transport.

Automated tests use mocked xAI responses. Live subscription entitlements and xAI's acceptance of the OAuth client must be checked by signing in with an actual account after deployment.
