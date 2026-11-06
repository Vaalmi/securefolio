# Security

## Reporting a vulnerability

Please report it privately through GitHub: on this repository's **Security** tab, choose **Report a vulnerability**. Don't open a public issue, pull request or discussion for it.

Say what you found, how to reproduce it, and what it lets someone do. Vaalmi maintains SecureFolio and will reply within two weeks. Once a fix is out, you'll be credited in its release notes unless you'd rather not be.

Only the latest release and `main` get security fixes.

## What SecureFolio promises

`docs/ARCHITECTURE.md` lists the privacy rules in full. A way around any of these is a vulnerability:

- Text, images or other data from a **Private** folder reaching a cloud model, the MCP tools or any other off-Mac destination, on any path: a question, a rewrite, a summary, a memory, a card, a page image.
- **Offline mode** making any connection off the Mac.
- SecureFolio **writing, moving or deleting** an original file.
- The backend's API or MCP tools answering a request **without the right token** for this launch, answering from another host or origin, or the MCP token opening anything but the MCP tools.
- A **document's text acting as an instruction**: making the agent call something, change something, or read outside the conversation's scope.
- Answers or emails **loading remote content or running script** in the app.
- A cloud key or a document's password **written to disk or a log**.
- **Spend** passing the monthly cap without asking, or a Deep run over its threshold running without asking.

## What it doesn't defend against

- Someone, or some program, running as your user on your Mac. They can read the vault, the Keychain items you allow, and the token file, as they can read your documents. SecureFolio doesn't encrypt its vault or backups; FileVault protects them at rest.
- What a cloud provider does with a request you allowed to leave. That's under your agreement with Anthropic, OpenAI or OpenRouter.
- Ollama's own configuration. SecureFolio talks to it only on loopback and asks you to set `OLLAMA_NO_CLOUD=1`, but it can't enforce Ollama's settings.
- A build you signed yourself, or a modified copy.
