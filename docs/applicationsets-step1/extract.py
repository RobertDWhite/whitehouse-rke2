#!/usr/bin/env python3
"""Extract a minimal app-config.yaml from each plain-dir Application CR.
Captures ONLY deviations from the template defaults; asserts invariants so any
app that doesn't fit is surfaced, not silently dropped."""
import yaml, glob, os, sys, json
os.chdir(os.environ.get("REPO","."))
RKE_SSH="git@github.com:RobertDWhite/whitehouse-rke2.git"
SERVER="https://kubernetes.default.svc"
PROJ_PREFIX={"platform":"platform/","security":"security/","observability":"observability/","apps":"apps/"}
DEFAULT_SYNCOPTS=["CreateNamespace=true"]

configs=[]    # (project, path, cfg, original_cr_dict, crfile)
problems=[]
paths_seen={}
for f in sorted(glob.glob("argo-cd/applications/*.yaml")):
    try: d=list(yaml.safe_load_all(open(f)))[0]
    except Exception as e: continue
    if not d or d.get("kind")!="Application": continue
    sp=d["spec"]; name=d["metadata"]["name"]; pr=sp.get("project")
    is_multi="sources" in sp
    srcs=sp.get("sources") or ([sp["source"]] if "source" in sp else [])
    has_helm=any(s.get("chart") for s in srcs)
    git_path=any(s.get("path") and not s.get("chart") and "whitehouse-rke2" in s.get("repoURL","") for s in srcs)
    if is_multi or has_helm or not git_path: continue   # not a plain-dir app
    s=sp["source"]; path=s["path"]
    cfg={"name":name}
    if "namespace" in sp["destination"]: cfg["namespace"]=sp["destination"]["namespace"]
    cfg["srcPath"]=path
    # --- invariants: capture deviations rather than assume ---
    if s.get("repoURL")!=RKE_SSH: cfg["repoURL"]=s.get("repoURL")
    if s.get("targetRevision")!="main": cfg["targetRevision"]=s.get("targetRevision")
    if sp["destination"].get("server")!=SERVER: cfg["server"]=sp["destination"]["server"]
    if pr=="default": continue   # argocd self-install: stays an explicit CR
    if pr not in PROJ_PREFIX: problems.append(f"{name}: project {pr} not a tier"); continue
    if not path.startswith(PROJ_PREFIX[pr]): problems.append(f"{name}: path {path} not under {pr}/")
    if path in paths_seen: problems.append(f"{name}: path {path} shared with {paths_seen[path]}")
    paths_seen[path]=name
    # --- source tool blocks (verbatim) ---
    if "kustomize" in s: cfg["kustomize"]=s["kustomize"]
    if "directory" in s: cfg["directory"]=s["directory"]
    # --- syncPolicy ---
    syncp=sp.get("syncPolicy") or {}; a=syncp.get("automated") or {}
    if a.get("prune",True) is not True: cfg["prune"]=a.get("prune")
    if a.get("selfHeal",True) is not True: cfg["selfHeal"]=a.get("selfHeal")
    so=syncp.get("syncOptions")
    if so is None: cfg["syncOptions"]=[]            # explicit: no syncOptions at all
    elif so!=DEFAULT_SYNCOPTS: cfg["syncOptions"]=so  # full replacement when != default
    # --- annotations / ignoreDifferences ---
    ann=(d["metadata"].get("annotations") or {})
    w=ann.get("argocd.argoproj.io/sync-wave")
    if w is not None: cfg["syncWave"]=w
    extra_ann={k:v for k,v in ann.items() if k!="argocd.argoproj.io/sync-wave"}
    if extra_ann: problems.append(f"{name}: unexpected annotations {list(extra_ann)}")
    if sp.get("ignoreDifferences"): cfg["ignoreDifferences"]=sp["ignoreDifferences"]
    # any spec key we don't handle?
    handled={"project","source","destination","syncPolicy","ignoreDifferences"}
    unh=set(sp.keys())-handled
    if unh: problems.append(f"{name}: unhandled spec keys {unh}")
    configs.append((pr,path,cfg,d,f))

# write configs into a separate, un-synced control tree: argo-cd/app-configs/<tier>/<name>.yaml
man=[]
for pr,path,cfg,d,f in configs:
    cdir=os.path.join("argo-cd/app-configs",pr); os.makedirs(cdir,exist_ok=True)
    out=os.path.join(cdir,cfg["name"]+".yaml")
    with open(out,"w") as fh:
        yaml.safe_dump(cfg,fh,default_flow_style=False,sort_keys=False)
    man.append({"project":pr,"path":path,"crfile":f,"name":cfg["name"],"cfgfile":out})

json.dump(man, open("docs/applicationsets-step1/manifest.json","w"))
print(f"extracted {len(configs)} app-config.yaml files")
from collections import Counter
byp=Counter(pr for pr,_,_,_,_ in configs); print("per project:",dict(byp))
deviating=[c["name"] for _,_,c,_,_ in configs if set(c)-{"name","namespace"}]
print(f"apps with deviations beyond name/namespace: {len(deviating)}")
print("  ",sorted(deviating))
if problems:
    print("\n!!! PROBLEMS (must resolve) !!!"); [print("  ",p) for p in problems]; sys.exit(1)
print("\nno invariant problems")
