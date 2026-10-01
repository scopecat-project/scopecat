# Open the Mac preview

Download the Mac preview from the project's linked GitHub build. Open the DMG,
drag **Scopecat** to **Applications**, then open the copied app. Python is included.

This preview uses a local (ad-hoc) signature. It has no Apple-verified developer
identity or notarization, so macOS may block the first opening.

If the message says the developer cannot be verified or Apple cannot check the
app for malicious software, and you trust this project's download:

1. Dismiss the message and open **System Settings → Privacy & Security**.
2. Find the message about Scopecat and choose **Open Anyway**, if offered.
3. Confirm the system prompt, then open Scopecat normally.

These steps follow [Apple's first-open guidance](https://support.apple.com/en-us/102445).
The exact wording and available controls depend on macOS and managed-computer policy.
This is an explicit approval of this application, not an automatic installation step.

If macOS says **the app is damaged** or **will damage your computer**, or offers no
way to open it, stop and send the maintainer the exact message, macOS version and
GitHub build link. A “Move to Trash” button alone does not identify the problem.
Do not delete your experiment data. The maintainer should check the downloaded
artifact; do not disable Gatekeeper or remove quarantine as a routine installation step.

Older preview builds lacked complete application signing. Download a build whose
acceptance results include the Mac signature/download checks; rebuilding your
Python environment will not repair an application signature.

Once the app opens, use **Settings → New code folder** to create a device-free
example and its own Python environment for VS Code. See
[getting started](../getting-started/index.md).
