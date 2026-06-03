#!/usr/bin/env python3
import os
OUT=os.path.join(os.environ.get("REPO","."),"argo-cd/applicationsets"); os.makedirs(OUT,exist_ok=True)
RKE="git@github.com:RobertDWhite/whitehouse-rke2.git"
SERVER="https://kubernetes.default.svc"

# project -> glob prefix
TIERS={"platform":"platform","security":"security","observability":"observability","apps":"apps"}

BODY = '''# GENERATED CONTROL PLANE -- not yet wired to app-of-apps (step 1: authoring +
# parity proof only). Replaces the {project} project's plain-directory
# Application CRs. Each app ships an argo-cd/app-configs/{prefix}/<name>.yaml that
# this set templates into an Application. See PARITY-REPORT.md.
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: {project}
  namespace: argocd
spec:
  goTemplate: true
  generators:
    - git:
        repoURL: {rke}
        revision: main
        files:
          - path: "argo-cd/app-configs/{prefix}/*.yaml"
  # Base template is plain, valid YAML (only quoted string-templates + literal
  # defaults), so generic YAML tooling parses it. ALL per-app variation -- incl.
  # the typed prune/selfHeal overrides, which can't be {{{{}}}}'d in a bool
  # field -- is injected by the templatePatch block string below.
  template:
    metadata:
      name: "{{{{ .name }}}}"
      namespace: argocd
    spec:
      project: {project}
      source:
        repoURL: {rke}
        targetRevision: main
        path: "{{{{ .srcPath }}}}"
      destination:
        server: {server}
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
  templatePatch: |
    {{{{- if hasKey . "syncWave" }}}}
    metadata:
      annotations:
        argocd.argoproj.io/sync-wave: "{{{{ .syncWave }}}}"
    {{{{- end }}}}
    spec:
    {{{{- if hasKey . "namespace" }}}}
      destination:
        namespace: {{{{ .namespace }}}}
    {{{{- end }}}}
    {{{{- if or (hasKey . "kustomize") (hasKey . "directory") }}}}
      source:
      {{{{- if hasKey . "kustomize" }}}}
        kustomize: {{{{ .kustomize | toYaml | nindent 10 }}}}
      {{{{- end }}}}
      {{{{- if hasKey . "directory" }}}}
        directory: {{{{ .directory | toYaml | nindent 10 }}}}
      {{{{- end }}}}
    {{{{- end }}}}
      syncPolicy:
    {{{{- if or (hasKey . "prune") (hasKey . "selfHeal") }}}}
        automated:
      {{{{- if hasKey . "prune" }}}}
          prune: {{{{ .prune }}}}
      {{{{- end }}}}
      {{{{- if hasKey . "selfHeal" }}}}
          selfHeal: {{{{ .selfHeal }}}}
      {{{{- end }}}}
    {{{{- end }}}}
    {{{{- if hasKey . "syncOptions" }}}}
      {{{{- if .syncOptions }}}}
        syncOptions:
        {{{{- range .syncOptions }}}}
          - {{{{ . }}}}
        {{{{- end }}}}
      {{{{- end }}}}
    {{{{- else }}}}
        syncOptions:
          - CreateNamespace=true
    {{{{- end }}}}
    {{{{- if hasKey . "ignoreDifferences" }}}}
      ignoreDifferences: {{{{ .ignoreDifferences | toYaml | nindent 6 }}}}
    {{{{- end }}}}
'''

for project,prefix in TIERS.items():
    txt=BODY.format(project=project,prefix=prefix,rke=RKE,server=SERVER)
    open(os.path.join(OUT,f"{project}.yaml"),"w").write(txt)
    print("wrote",f"argo-cd/applicationsets/{project}.yaml")
