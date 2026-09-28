# VibePulse Lovable Credits (experimental)

Local Chrome extension for the optional Lovable screen. It reads explicitly
labelled credit values from the rendered billing page, then sends a small JSON
snapshot to the tokenserver on this computer. It never exports login credentials.

**Full setup, permissions, source selection, troubleshooting and removal:**
[Lovable Pulse](../../docs/lovable-pulse.md#setup).

Quick preparation from the repository root:

```sh
python3 tools/lovable-browser/setup_browser.py --workspace-name "Your workspace" --port 8737
```

Use the printed directory with Chrome's **Load unpacked**, not this source
directory. Keep your existing tokenserver arguments and add
`--lovable --lovable-source browser`. Browser mode needs no MCP OAuth login.

The `manifest.json` key is a **public** identity key for a stable extension ID;
it is not a token or secret. There is no private key in this project. The ID
pins the loopback receiver's expected browser source, not authorization against
other local processes. Content scripts are restricted to the billing page;
Chrome host permissions cover lovable.dev and 127.0.0.1.

The automatic browser refresh path still needs an installed-extension end-to-end
check. The photographed hardware checkpoint used a real one-time browser read.
Do not represent the photograph or Python unit tests as browser lifecycle proof.
