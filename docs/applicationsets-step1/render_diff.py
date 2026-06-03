#!/usr/bin/env python3
"""Render an Application from each app-config.yaml using the SAME logic the
ApplicationSet template encodes, then deep-diff against the original CR.
83/83 identical == the app-config + template losslessly reproduce the CRs."""
import yaml, json, os, sys
os.chdir(os.environ.get("REPO","."))
RKE_SSH="git@github.com:RobertDWhite/whitehouse-rke2.git"
SERVER="https://kubernetes.default.svc"
manifest=json.load(open("docs/applicationsets-step1/manifest.json"))

def render(cfg, project, path):
    app={"apiVersion":"argoproj.io/v1alpha1","kind":"Application",
         "metadata":{"name":cfg["name"],"namespace":"argocd"},
         "spec":{"project":project,
                 "source":{"repoURL":cfg.get("repoURL",RKE_SSH),
                           "targetRevision":cfg.get("targetRevision","main"),"path":cfg["srcPath"]},
                 "destination":{"server":cfg.get("server",SERVER)},
                 "syncPolicy":{"automated":{"prune":cfg.get("prune",True),
                                            "selfHeal":cfg.get("selfHeal",True)}}}}
    if "syncWave" in cfg:
        app["metadata"]["annotations"]={"argocd.argoproj.io/sync-wave":cfg["syncWave"]}
    if "kustomize" in cfg: app["spec"]["source"]["kustomize"]=cfg["kustomize"]
    if "directory" in cfg: app["spec"]["source"]["directory"]=cfg["directory"]
    if "namespace" in cfg: app["spec"]["destination"]["namespace"]=cfg["namespace"]
    if "syncOptions" in cfg:
        if cfg["syncOptions"]: app["spec"]["syncPolicy"]["syncOptions"]=cfg["syncOptions"]
    else:
        app["spec"]["syncPolicy"]["syncOptions"]=["CreateNamespace=true"]
    if "ignoreDifferences" in cfg: app["spec"]["ignoreDifferences"]=cfg["ignoreDifferences"]
    return app

def diff(a,b,p=""):
    out=[]
    if type(a)!=type(b): return [f"{p}: type {type(a).__name__}!={type(b).__name__} ({a!r} vs {b!r})"]
    if isinstance(a,dict):
        for k in set(a)|set(b):
            if k not in a: out.append(f"{p}.{k}: missing in RENDERED (cr={b[k]!r})")
            elif k not in b: out.append(f"{p}.{k}: EXTRA in rendered ({a[k]!r})")
            else: out+=diff(a[k],b[k],f"{p}.{k}")
    elif isinstance(a,list):
        if len(a)!=len(b): out.append(f"{p}: list len {len(a)}!={len(b)}")
        else:
            for i,(x,y) in enumerate(zip(a,b)): out+=diff(x,y,f"{p}[{i}]")
    elif a!=b: out.append(f"{p}: {a!r} != {b!r}")
    return out

ok=0; fails=[]
for m in manifest:
    cfg=yaml.safe_load(open(m["cfgfile"]))
    rendered=render(cfg, m["project"], m["path"])
    original=list(yaml.safe_load_all(open(m["crfile"])))[0]
    d=diff(rendered, original, m["name"])
    if d: fails.append((m["name"],d))
    else: ok+=1
print(f"PARITY: {ok}/{len(manifest)} Applications render byte-identical to their CR\n")
for name,d in fails:
    print(f"--- MISMATCH: {name} ---")
    for line in d: print("   ",line)
sys.exit(1 if fails else 0)
