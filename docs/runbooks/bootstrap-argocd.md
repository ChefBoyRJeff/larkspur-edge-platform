# Runbook: Bootstrap Argo CD on a new cluster

Use on a fresh k3s cluster (after `ansible-playbook playbooks/site.yml`). Afterwards everything in `platform/apps/` is deployed by Argo CD from `main`.

## 1. Install Argo CD once with Helm (PowerShell, repo root)
```
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update argo
helm upgrade --install argocd argo/argo-cd --namespace argocd --create-namespace --version 10.19.0 -f platform/argocd/values.yaml --wait --timeout 10m
kubectl -n argocd get pods
```

## 2. Sign in and replace the initial admin password
- Copy the initial password without displaying it: read `argocd-initial-admin-secret`, base64-decode, send to the clipboard; store it in the password manager.
- `kubectl -n argocd port-forward svc/argocd-server 8080:443`, then open `https://localhost:8080` and sign in as `admin`.
- User Info -> Update Password; store the new password; make sure `argocd-initial-admin-secret` no longer exists.

## 3. Hand over to GitOps
```
kubectl apply -f platform/bootstrap/root-app.yaml
kubectl -n argocd get applications
```
All applications should reach `Synced` and `Healthy`. From here on, changes are made by pull request only.
