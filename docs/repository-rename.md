# ML² repository rename

The chosen repository slug is `ml2`, with **ML²** as the display name.

## Coordinated rename checklist

1. Tell collaborators when the rename will happen.
2. Rename the existing repository in GitHub Settings; do not create a replacement repo.
3. Change `frontend/vite.config.ts` from the old base to `/ml2/` if retaining
   a project GitHub Pages URL. Build and redeploy in the same cutover. A custom domain
   can decouple the public site URL from the repository name.
4. Update README/frontend README deployment links, branding in the UI and HTML title,
   contributor/agent documentation, badges, and references in external integrations.
   Known hard-coded project references include `AGENTS.md` and
   `docs/agents/issue-tracker.md`. Ownership-based `CODEOWNERS` entries do not change
   merely because the repository name changes.
5. Collaborators should update their existing checkout (no reclone required):

   ```powershell
   git remote set-url origin https://github.com/DJsudartha/ml2.git
   git remote -v
   ```

   People using SSH should retain their SSH transport. Fork users should update their
   `upstream` remote instead; their `origin` usually points to their own fork. Local
   directory names need not change. If changed, update local IDE and scheduler paths.
6. Audit integrations for literal repository URLs: webhooks, scheduled commands,
   external CI, and any other repository importing an action hosted here. Verify the
   existing CI and Pages workflow after the cutover.
7. Do not reuse the old repository name: that would remove its redirect.

## Collaborator impact

GitHub redirects repository web traffic and old clone/fetch/push locations after a
rename. Existing history, branches, and collaboration stay in the same repository;
updating remotes avoids confusion. **Project Pages URLs are the exception**: plan for
the site URL changing and old bookmarks breaking. References to actions hosted by the
renamed repository are also not redirected.

Source: [GitHub's repository-renaming documentation](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository).
