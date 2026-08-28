"""Conservative pre-commit scan of code/doc assets; never prints matched secrets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def scan(repository):
    root=Path(repository).resolve()
    candidates=[]
    for relative in [".agents/skills/build-truck-drone-network","tests","docs","validation"]:
        folder=root/relative
        if folder.exists():
            candidates.extend(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    candidates.extend(p for p in root.iterdir() if p.is_file() and p.name not in {".DS_Store"})
    patterns=[("github_credential",re.compile(r"gh[pousr]_[A-Za-z0-9_]{25,}")),
              ("private_key",re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
              ("embedded_32hex_credential",re.compile(r"[\"'][0-9a-fA-F]{32}[\"']")),
              ("credential_url",re.compile(r"https?://[^\s/:]+:[^\s/@]+@"))]
    problems=[]; count=0
    for path in sorted(set(candidates)):
        rel=str(path.relative_to(root)).replace("\\","/")
        if path.suffix.lower() in {".tif",".tiff",".pbf",".gpkg",".shp",".dbf",".shx",".zip",".key",".pem"} or path.name.startswith(".env"):
            problems.append({"file":rel,"rule":"unexpected_data_or_credential_file"}); continue
        if path.stat().st_size>2_000_000:
            problems.append({"file":rel,"rule":"unexpected_large_source_asset"}); continue
        if path.suffix.lower() in {".png",".jpg"}:
            continue
        try:
            text=path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            problems.append({"file":rel,"rule":"unexpected_binary"}); continue
        count+=1
        for name,pattern in patterns:
            if pattern.search(text):
                problems.append({"file":rel,"rule":name})
    return {"passed":not problems,"text_files_scanned":count,"issues":problems,
            "scope":"code_docs_tests_validation_allowlist_not_raw_workspaces","matched_values_redacted":True}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--repository",type=Path,required=True)
    result=scan(parser.parse_args().repository)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result["passed"] else 2)
