# Runbook: Release and roll back Larkspur Track

How a build reaches dev and prod, and how to undo it. The design is in ADR-0004; image builds are in ADR-0003.

## How releases work
- Every merge to `main` that changes `app/` runs the **Image** workflow. It builds amd64 and arm64 images, scans them with Trivy, and pushes `ghcr.io/chefboyrjeff/larkspur-track:sha-<7-character merge commit>`.
- Each environment runs the tag in its values file: `charts/larkspur-track/values-dev.yaml` and `values-prod.yaml`. **A pull request that changes that tag is the release**; Argo CD applies it after the merge.
- **Rule:** prod only gets a tag that dev is running.
- Every page footer shows `Release <tag>`, so anyone can see which build an environment runs.
- Each sync runs `alembic upgrade head` (migration job) before new pods start; if it fails, the sync stops and the running release keeps serving.

## Release to dev
1. Wait for the Image workflow on `main` to finish, then confirm the tag exists (PowerShell):
   ```powershell
   $tag = 'sha-1234567'   # the merge commit's first 7 characters
   $token = (Invoke-RestMethod 'https://ghcr.io/token?scope=repository:chefboyrjeff/larkspur-track:pull').token
   (Invoke-RestMethod -Headers @{ Authorization = "Bearer $token" } 'https://ghcr.io/v2/chefboyrjeff/larkspur-track/tags/list').tags -contains $tag
   ```
2. Branch `release/dev-<tag>`, change `image.tag` in `values-dev.yaml`, and open a pull request titled `chore(platform): promote <tag> to dev`. Merge when `app-checks` and `chart-checks` pass.
3. Verify: the footer at https://track-dev.larkspur.home.arpa shows the tag, and `kubectl rollout status deployment/larkspur-track -n larkspur-dev` reports success.

## Release to prod
1. Confirm dev runs the tag and its rollout is complete:
   ```powershell
   kubectl get deployment larkspur-track -n larkspur-dev -o jsonpath='{.spec.template.spec.containers[0].image}'
   kubectl rollout status deployment/larkspur-track -n larkspur-dev --timeout=5s
   ```
2. Branch `release/prod-<tag>`, change `image.tag` in `values-prod.yaml`, and open `chore(platform): promote <tag> to prod`. In the description, say how dev went and how to roll back.
3. Verify the footer at https://track.larkspur.home.arpa and `kubectl rollout status deployment/larkspur-track -n larkspur-prod`.

## Roll back
1. Open the promotion pull request, click **Revert**, title it `revert(platform): roll prod back to <previous tag>`, and merge when the checks pass.
2. Argo CD polls Git every few minutes. To apply the revert now:
   ```powershell
   kubectl -n argocd annotate application larkspur-track-prod argocd.argoproj.io/refresh=hard --overwrite
   ```
3. Verify the footer and `kubectl get deployment larkspur-track -n larkspur-prod -o jsonpath='{.spec.template.spec.containers[0].image}'`.

### Roll back a release that is stuck
If the release never finished (for example its image cannot be pulled, which `LarkspurReleaseStuck` reports), Argo CD is still running that sync: it waits for the migration job (up to 5 minutes), then retries. **It will not start the revert's sync while the stuck one runs.** Since 11-27-2026 each retry uses the latest commit, so the revert is picked up at the next attempt, within about 5 minutes. To apply it at once, merge the revert, then stop the stuck sync (what `argocd app terminate-op` does):
```powershell
$file = Join-Path ([IO.Path]::GetTempPath()) 'terminate-op.json'
[IO.File]::WriteAllText($file, '{"status":{"operationState":{"phase":"Terminating"}}}')
kubectl patch application larkspur-track-prod -n argocd --type merge --patch-file $file
kubectl -n argocd annotate application larkspur-track-prod argocd.argoproj.io/refresh=hard --overwrite
kubectl get application larkspur-track-prod -n argocd -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}'
```
The last command shows `Succeeded` and the revert's commit when it is done. Measured in a drill on dev (11-27-2026), with a tag missing from GHCR: the web pods were never touched (593 of 593 requests succeeded); without stopping it, the stuck sync was still retrying 2 minutes after the revert; after stopping it, the revert's sync succeeded within 25 seconds.

**Migrations:** a revert changes only the image; it never runs a downgrade. Rolling back is safe only when the previous release works with the current schema, so migrations must be backward compatible (add first, remove in a later release). When they are not, roll forward with a fix instead. Releases before `sha-e5f376a` have no sign-in: rolling back past it works with the schema but turns sign-in off (ADR-0006).

**Expected:** after the merge and a refresh, a release or rollback finishes in well under a minute with no failed requests (rolling update with `maxUnavailable: 0` and a 5 s `preStop` pause). Add the time the pull request checks take.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| The tag is not on GHCR | The Image workflow is still running, or failed (for example a Trivy finding) | Check the Actions tab; fix the finding and merge again. `chart-checks` fails a promotion whose tag is not on GHCR; re-run it once the image is published |
| The app stays OutOfSync after the merge | Argo CD has not polled Git yet | Run the refresh command above |
| The sync fails at the migration job | The migration errored | `kubectl logs job/larkspur-track-migrate -n <namespace>`; the running release keeps serving; fix forward |
| A revert is merged but nothing changes | The previous release's sync is still running (stuck) | "Roll back a release that is stuck" above |
| `git pull` says `cannot lock ref` | A background fetch (VS Code auto-fetch) held the lock | `git fetch origin`, then `git merge --ff-only origin/main` |
| The footer briefly shows the old release after the rollout completes | The last old pod is still in its 5 s shutdown pause | Wait a few seconds |
