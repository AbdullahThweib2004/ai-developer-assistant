# Demo video script (2–3 minutes)

Real mode, so the issue at the end is real. It is created in your **sandbox** repository,
never in `fastapi/fastapi`.

## Before recording (checklist)

- [ ] `.env`: a working LLM key, `GITHUB_ALLOWED_REPOS=fastapi/fastapi`, `GITHUB_DEFAULT_REPO=fastapi/fastapi`,
      `GITHUB_WRITE_ENABLED=true`, `GITHUB_WRITE_TOKEN` (Issues: read and write on the sandbox only),
      `GITHUB_WRITE_ALLOWED_REPOS=<you>/ai-dev-assistant-sandbox`, `MCP_ALLOWED_PROJECT_ROOTS` includes `examples/`.
- [ ] Build the UI: `cd frontend && npm run build`. Start: `uv run ai-dev-assistant web`.
- [ ] Index `fastapi-auth-demo` once before recording (Knowledge Base → Index project), so the
      demo doesn't wait on the embedding model.
- [ ] Browser at 1440×900 or 1280×800, zoom 100%, light theme, notifications off,
      bookmarks bar hidden. Close other tabs and any window that shows `.env`.
- [ ] Do one dry run, then delete the test issue from the sandbox (or close it).
- [ ] Record at 1080p or higher; keep the cursor slow.

## Script

| Time | Click / action | Say |
|---|---|---|
| 0:00 | App open on **AI Assistant**, empty state. | "This is a local developer assistant. It indexes a codebase, investigates questions with read-only tools, and cites everything it says." |
| 0:10 | Point at the top bar: project `fastapi-auth-demo`, GitHub pill **Connected**. | "It's pointed at a small FastAPI service, and at FastAPI's GitHub repository for related issues. GitHub access here is read-only." |
| 0:20 | Click the example **"Why do requests to /users/me return 401 even though I send a valid bearer token?"** | "A classic bug: valid token, still 401." |
| 0:25 | Let the steps stream. Point at the **Investigation Context** panel as cards change. | "It decides what it needs: it searches the project, reads the auth and config files, then checks GitHub. Each step streams live, with timing and token counts." |
| 0:50 | Answer appears. Point at the summary and **Key findings**. | "Two causes, both with evidence: the header parser only accepts lowercase `bearer`, and the secret is read from `JWT_SECRET` while the docs say `JWT_SECRET_KEY`." |
| 1:05 | Hover a citation chip **S1**, then click **Open source**. | "Every claim is cited. Open source jumps to the exact lines in the code." |
| 1:15 | In **Code Explorer**, point at the highlighted lines (`scheme != "bearer"`). Click **AI Assistant** in the sidebar. | "Here's the case-sensitive check. The session is kept when I come back." |
| 1:25 | Scroll to **From GitHub** and the GitHub source cards. | "Related FastAPI issues about 401 versus 403 are shown as leads, not proof. GitHub text is treated as untrusted data." |
| 1:40 | Click **Draft GitHub issue**. | "Now I want to report this. The assistant can only draft; it can't create anything." |
| 1:45 | In the drawer, point at **Destination repository** (your sandbox), edit the title slightly. | "The destination is fixed by configuration, not by the model. I can edit everything. The draft is checked for secrets." |
| 2:00 | Point at the disabled **Approve and create** button. Type the repo name exactly. | "Approval needs me to type the repository name. Until it matches, the button stays disabled, and any edit clears it." |
| 2:10 | Click **Approve and create**. Wait for **Issue #N created**. Click the link. | "One attempt, bound to exactly this draft. Now it exists on GitHub." |
| 2:20 | Show the issue on GitHub. Switch back, open **Activity**. | "The audit log records the approval and the result: never the body or any token." |
| 2:30 | End on the Activity page or the issue. | "Read-only by default, cited answers, and nothing written without my approval." |

## If something goes wrong

- Rate limited by GitHub: wait a minute, or set `GITHUB_TOKEN` for higher limits.
- Similar issues found: the drawer asks you to confirm "Create anyway"; mention it as a feature.
- Answer differs from the script: the model is live; describe what's on screen and keep the
  same flow (sources → GitHub leads → draft → approve).
- Without keys, rehearse with `uv run ai-dev-assistant web --demo` (the issue is mocked as #42).
