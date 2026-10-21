import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from orc.aris import arm
from orc.shell import start

if __name__ == "__main__":
    arm()   # ARIS hotkey armed silently
    start()