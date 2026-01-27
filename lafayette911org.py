import os
import sys

from lafayette911.main import main


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sys.exit(main(base_dir=base_dir))
