from pathlib import Path
import sys

SCRIPTS=Path(__file__).parents[1]/".agents/skills/build-truck-drone-network/scripts"
sys.path.insert(0,str(SCRIPTS))
