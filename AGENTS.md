# Repository execution rules

Use `$build-truck-drone-network` for network work and read its selected references before acting. This clone contains the complete portable implementation under `.agents/skills/build-truck-drone-network/`; do not import private files from E-Truck-Drone-System_TRE.

Development/shared environment is `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`, with interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. On that host all Python uses this interpreter. On a teammate host, record a user-approved existing Python 3.12 path. Do not silently create a venv or install packages. Use a short workspace path on Windows, for example `C:/tdn-work`, to avoid the 260-character legacy limit; do not change OS settings automatically.

- Preserve raw data, prior results and published releases. Create new output versions.
- Keep secrets and bulk data out of Git. No AMap request is needed or allowed for NZ.
- Follow the latest user scope. Do not launch solver/model/training jobs as an implicit next step.
- Do not present synthetic fixtures as real observations, or small tests as real-country validation.
- Do not modify the original model's mathematics or scenario identity contracts to fit these outputs. This repo produces a portable Wuding-style interface; a Model Agent must explicitly verify any production adapter.
- New Markdown reports must record the development/shared environment above and the actual execution interpreter.
- Dataset selection, licences, Depot, population threshold, microgrid coverage, road speeds and energy proxies must be visible in the regional profile. Stop for material unresolved choices.
