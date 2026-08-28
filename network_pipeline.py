"""Repository-root entry point; uses the caller's explicit Python environment."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent/".agents/skills/build-truck-drone-network/scripts"))
from run_network_pipeline import main

if __name__=="__main__":
    main()
