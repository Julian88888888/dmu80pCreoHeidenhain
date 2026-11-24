"""Профіль верстата: база за замовчуванням Q339=3."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machines.dmu80p import MACHINE_DMU80P


def main() -> None:
    assert MACHINE_DMU80P["q339"] == 3, MACHINE_DMU80P["q339"]
    assert MACHINE_DMU80P["name"] == "DMU 80P"
    print("PASS: Q339=3, DMU 80P")


if __name__ == "__main__":
    main()
