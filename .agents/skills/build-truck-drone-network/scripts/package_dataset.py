"""Package a validated release, source code and permitted raw inputs for transfer.

Creates a new ZIP; omitted/licence-restricted raw inputs remain in an explicit
download manifest. Never uploads the ZIP or modifies its scientific tables.
"""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from portable_core import GateError,digest,read_json,safe_path
from validate_pipeline import validate_release


def package(root,release,output,include_raw=False):
    root=Path(root).resolve(); release=Path(release).resolve(); output=Path(output).resolve()
    if output.exists() or output.is_relative_to(release):
        raise GateError("Bundle must be a new file outside the published release")
    check=validate_release(release,root)
    inventory=read_json(release/"registry/input_inventory.json")
    manifest=read_json(release/"registry/run_manifest.json")
    for name,sha in manifest["core_csv_hashes"].items():
        if digest(release/name)!=sha:
            raise GateError("Published core changed since its run manifest; do not bundle")
    files={str(p.relative_to(root)).replace("\\","/"):p for p in release.rglob("*") if p.is_file()}
    files["parameters/region_profile_snapshot.json"]=release/"registry/parameter_snapshot.json"
    omitted=[]
    for item in inventory:
        path=safe_path(root,item["path"])
        if not include_raw or item.get("redistribution_permitted") is not True:
            omitted.append({**item,"bundle_status":"manual_download_required_not_included"})
            continue
        if "amap" in item["source_name"].lower() or "amap" in item["source_url"].lower():
            omitted.append({**item,"bundle_status":"provider_restricted_not_included"}); continue
        if digest(path)!=item["sha256"]:
            raise GateError("Raw/source input differs from the validated release")
        files[item["path"]]=path
    scripts=Path(__file__).parent
    repository=scripts.parents[3]
    skill=scripts.parent
    for f in skill.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and f.suffix in {".py",".md",".json",".csv",".yaml"}:
            files[str(f.relative_to(repository)).replace("\\","/")]=f
    for name in ["README.md","AGENTS.md","NOTICE","requirements.txt","requirements-lock-tested.txt","network_pipeline.py"]:
        f=repository/name
        if f.is_file():
            files[name]=f
    for f in (repository/"docs").rglob("*"):
        if f.is_file():
            files[str(f.relative_to(repository)).replace("\\","/")]=f
    # Runtime definitions contain project-relative paths. Preserve that layout.
    report={"validated":check["passed"],"release_path":str(release.relative_to(root)).replace("\\","/"),
            "file_count":len(files),"included_input_count":len(inventory)-len(omitted),"omitted_inputs":omitted,
            "archive_checksums":{name:digest(path) for name,path in sorted(files.items())},
            "instruction":"Extract into one project root. Obtain omitted inputs at recorded exact websites/versions. No credentials included."}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,path in sorted(files.items()):
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise GateError("Unsafe ZIP member")
            archive.write(path,name)
        archive.writestr("DATASET_BUNDLE_MANIFEST.json",json.dumps(report,indent=2,ensure_ascii=False))
    return {"output":str(output),"file_count":len(files),"omitted_input_count":len(omitted),"sha256":digest(output)}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root",type=Path,required=True); p.add_argument("--release",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True); p.add_argument("--include-permitted-raw",action="store_true")
    a=p.parse_args(); print(json.dumps(package(a.project_root,a.release,a.output,a.include_permitted_raw),indent=2))
