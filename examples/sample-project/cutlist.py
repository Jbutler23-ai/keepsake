"""Print a cut list for the sample shelf plan."""

import json
from pathlib import Path


def main() -> None:
    plan_path = Path(__file__).parent / "config" / "shelves.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    total_length = 0
    for shelf in plan["shelves"]:
        quantity = shelf["quantity"]
        length = shelf["length_mm"]
        total_length += quantity * length
        print(f"{quantity} x {length} mm: {shelf['name']}")
    print(f"Total board length before waste: {total_length} mm")


if __name__ == "__main__":
    main()
