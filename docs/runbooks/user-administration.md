# Runbook: User administration

How people get access to Larkspur Track, and what to do when access goes wrong. The design is in ADR-0006.

## Roles
| Role | Can |
|---|---|
| clerk | View and search shipments, receive them, and move them received -> stored -> picked |
| supervisor | Everything a clerk can, plus mark picked shipments as shipped and read the audit log (`/audit`) |
| admin | Everything a supervisor can, plus manage users (`/admin/users`) |

## Create the first admin of an environment
Needed once per environment (and after a database restore that lost the users). Run in PowerShell with the cluster's kubeconfig; type the password twice at the prompt. Save it in Bitwarden first, one entry per environment.
```powershell
kubectl exec -it -n larkspur-dev deploy/larkspur-track -c web -- larkspur-track-admin create-user --username <name> --display-name "<Full Name>" --role admin
```
For prod, use `-n larkspur-prod`. The command prints `Created admin user <name>.` and records `user_created` in the audit log.

## Everyday tasks (signed in as an admin, Users page)
| Task | How | Effect |
|---|---|---|
| Add a user | **Add a user**: username, full name, role, password twice | The user can sign in at once |
| Change a role | Open the user, **Change role** | Applies from the user's next page view |
| Deactivate | Open the user, **Deactivate and sign out everywhere** | Sessions end at once; history is kept |
| Reactivate | Open the user, **Reactivate** | The user can sign in again |
| Reset a password | Open the user, **Reset password** | Sessions end; any lockout is cleared |
| Unlock | Open the user, **Unlock now** (shown only while locked) | The user can try again at once |

Give a new or reset password to the person directly, never by email or chat, and ask them to keep it in a password manager. Admins cannot change their own role or deactivate themselves; another admin must.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| "The username or password is incorrect." for a known user | Wrong password, a deactivated account, or a lockout after 5 wrong passwords (15 minutes) | Check the user's status on the Users page; unlock or reset the password |
| Sent back to the sign-in page after 60 minutes away | The session was idle for 60 minutes (or 8 hours passed since sign-in) | Sign in again |
| "Nothing was changed" (403) after submitting a form | The page was older than the session (signed out or signed in again in another tab), or the request came from another site | Reload the page and submit again |
| "You don't have access to that" (403) | The role is too low for the page or action | An admin changes the role if appropriate |
| The only admin forgot their password | No admin can reset it | Create a second admin with the command above, sign in as them, reset the first admin's password, then deactivate the extra admin if it is not needed |
| Signing in locally returns to the sign-in page | `SESSION_COOKIE_SECURE` is not `false` in `app/.env` | See `docs/runbooks/local-dev.md` |

## Audit log
Supervisors and admins open **Audit** to see the newest 200 entries, filterable by action: sign-ins (including failed ones, with the typed username), sign-outs, shipment changes and user administration, each with the client's address. Entries cannot be edited or deleted; the database rejects it.
